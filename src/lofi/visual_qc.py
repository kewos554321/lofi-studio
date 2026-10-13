#!/usr/bin/env python3
"""
visual_qc.py — 用 ffmpeg/ffprobe 對 AI 生成的場景圖做自動品檢。

對應 auto_qc.py（音樂品檢），對象是 assets/visuals/ 的圖片。
純標準庫 + ffmpeg，所以系統 python3 就能跑（不需要 numpy / Pillow）。

只抓「整張爛掉」這類一眼可判的失敗（用統計量，不做語意判斷）：
  - 檔案解不開（PNG 被截斷 / 寫到一半的殘檔）
  - 解碼途中報錯（檔頭完好但資料毀損）
  - 尺寸為 0（無法解碼）或與 --expect 不符
  - 全黑 / 全白（fp16 VAE 溢位 -> NaN 最常見的長相）
  - 純色 / 幾乎沒有結構（entropy ~0，空白或根本沒畫完）
  - 灰圖 / 完全無彩度（VAE/色域壞掉）
  - 對比過低（灰濛一片）、檔案小到不合理

**不做**內容崩壞（人臉融化、多手多腳）判斷——那需要 CLIP/人臉偵測等模型，
成本高，先用這裡的統計法把「整張爛掉」擋掉（CP 值最高）。

結果寫回圖片旁的 sidecar（<image>.png.json 的 visual_qc 欄位），並 append 一筆到
catalog/visuals.jsonl（append-only 事件流，與 tracks.jsonl 分開）。

用法:
  lofi visual-qc                        # 掃描 assets/visuals 尚未品檢的圖
  lofi visual-qc --all                  # 掃描全部（已品檢者仍跳過）
  lofi visual-qc --all --force          # 強制重算
  lofi visual-qc IMG [IMG ...]          # 只檢查指定檔
  lofi visual-qc --expect 1536x1024     # 一併比對尺寸
  lofi visual-qc --no-write             # 只印結果，不寫 sidecar
  lofi visual-qc --json                 # 以 JSON 輸出

閾值可用 --set key=value 覆寫（key 見 DEFAULT_THRESHOLDS）。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from lofi import catalog as cat
from lofi import io
from lofi import media
from lofi.paths import CATALOG_DIR, VISUALS_DIR

VISUAL_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VISUALS_JSONL = CATALOG_DIR / "visuals.jsonl"

DEFAULT_THRESHOLDS = {
    # signalstats 走 yuv 有限範圍：純黑=16、純白=235（不是 0/255）
    "black_ymax": 20.0,           # YMAX <= 此值 -> 全黑（fail）
    "white_ymin": 230.0,          # YMIN >= 此值 -> 全白（fail）
    "flat_entropy": 0.05,         # normalized_entropy < 此值 -> 純色/無結構（fail）
    "sat_max_fail": 2.0,          # SATMAX < 此值 -> 完全無彩度（fail）
    "sat_avg_warn": 4.0,          # SATAVG < 此值 -> 近灰（warn）
    "contrast_min": 15.0,         # YHIGH-YLOW < 此值 -> 灰濛一片（warn）
    "bytes_per_pixel_min": 0.02,  # 每像素位元組 < 此值 -> 殘檔（warn）
}


# ----------------------------- 底層量測 -----------------------------

def ffprobe_info(path):
    """回傳 {'width','height','pix_fmt'}；解不開時寬高為 0。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        return None
    parts = out.split(",")
    try:
        w, h = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    return {"width": w, "height": h,
            "pix_fmt": parts[2] if len(parts) > 2 else ""}


def decode_stderr(path):
    """完整解碼一次，回傳 stderr（用來抓截斷/毀損的警告）。"""
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr


def signalstats(path):
    """回傳亮度/彩度統計（yuv 有限範圍，Y:16-235、SAT:0-~180）。"""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vf", "signalstats,metadata=print", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    keys = ("YMIN", "YLOW", "YAVG", "YHIGH", "YMAX", "SATAVG", "SATMAX")
    vals = {}
    for k in keys:
        m = re.search(rf"lavfi\.signalstats\.{k}=(-?\d+\.?\d*)", err)
        if m:
            vals[k.lower()] = float(m.group(1))
    return vals


def entropy(path):
    """回傳 R 通道的 normalized_entropy（0=純色，照片約 0.7~0.97）。"""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vf", "format=rgb24,entropy,metadata=print", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    m = re.search(r"lavfi\.entropy\.normalized_entropy\.normal\.R=(-?\d+\.?\d*)", err)
    return float(m.group(1)) if m else None


# ----------------------------- 判定 -----------------------------

def measure(path):
    """把一張圖所有量測集中成一個 dict（唯一的 I/O；判定交給 judge）。"""
    path = Path(path)
    info = ffprobe_info(path)
    w = (info or {}).get("width") or 0
    h = (info or {}).get("height") or 0
    m = {"width": w, "height": h, "pix_fmt": (info or {}).get("pix_fmt") or None,
         "bytes": path.stat().st_size if path.exists() else 0,
         "decode_error": False, "stats": {}, "entropy": None}
    if info and w > 0 and h > 0:
        err = decode_stderr(path)
        m["decode_error"] = bool(re.search(
            r"Decoding error|Invalid data|corrupt|chunk too big", err, re.I))
        m["stats"] = signalstats(path)
        m["entropy"] = entropy(path)
    return m


def judge(m, expect_size=None, thresholds=None):
    """依量測結果判定 verdict/score/flags（純函式，不碰檔案，方便測試）。"""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    w, h = m.get("width") or 0, m.get("height") or 0
    stats = m.get("stats") or {}
    ent = m.get("entropy")
    size_bytes = m.get("bytes") or 0

    hard, warn = [], []
    if not w or not h:
        hard.append("undecodable")
    elif m.get("decode_error"):
        hard.append("decode_error")

    ymin, ymax = stats.get("ymin"), stats.get("ymax")
    ylow, yhigh = stats.get("ylow"), stats.get("yhigh")
    satavg, satmax = stats.get("satavg"), stats.get("satmax")
    contrast = (yhigh - ylow) if (ylow is not None and yhigh is not None) else None
    bpp = (size_bytes / (w * h)) if w and h else None

    blank = False
    if ymax is not None and ymax <= th["black_ymax"]:
        hard.append("black_frame")
        blank = True
    if ymin is not None and ymin >= th["white_ymin"]:
        hard.append("white_frame")
        blank = True

    # 已經全黑/全白就不用再報純色/灰/低對比，避免旗標一堆雜訊
    if not blank:
        if ent is not None and ent < th["flat_entropy"]:
            hard.append("flat_image")
        if satmax is not None and satmax < th["sat_max_fail"]:
            hard.append("grayscale")
        if satavg is not None and satavg < th["sat_avg_warn"]:
            warn.append("low_saturation")
        if contrast is not None and contrast < th["contrast_min"]:
            warn.append("low_contrast")

    if expect_size and w and h and (w, h) != tuple(expect_size):
        warn.append("size_mismatch")
    # 已經有硬傷時就不用再報殘檔（純色/黑圖本來就很小），少一點雜訊
    if not hard and bpp is not None and bpp < th["bytes_per_pixel_min"]:
        warn.append("suspiciously_small")

    if hard:
        verdict = "fail"
    elif warn:
        verdict = "warn"
    else:
        verdict = "pass"
    score = max(0, 100 - 25 * len(hard) - 7 * len(warn))

    return {
        "verdict": verdict,
        "score": score,
        "flags": hard + warn,
        "width": w or None,
        "height": h or None,
        "pix_fmt": m.get("pix_fmt"),
        "bytes": size_bytes,
        "bytes_per_pixel": round(bpp, 4) if bpp is not None else None,
        "ymin": ymin, "ylow": ylow, "yavg": stats.get("yavg"),
        "yhigh": yhigh, "ymax": ymax,
        "satavg": satavg, "satmax": satmax,
        "contrast": contrast,
        "entropy": round(ent, 4) if ent is not None else None,
    }


def analyze(path, expect_size=None, thresholds=None):
    return judge(measure(path), expect_size=expect_size, thresholds=thresholds)


def qc_file(image, force=False, thresholds=None, expect_size=None, write=True):
    """對單張圖品檢；write=True 時寫 sidecar 並 append 事件。回傳 (path, record, skipped)。"""
    image = Path(image)
    sc = cat.load_sidecar(image) or {}
    if sc.get("visual_qc") and not force:
        return image, sc["visual_qc"], True

    result = analyze(image, expect_size=expect_size, thresholds=thresholds)

    if write:
        if not sc:
            sc = {"visual_id": image.stem, "file": cat.relpath(image)}
        sc["visual_qc"] = result
        cat.save_sidecar(image, sc)
        append_event(image, sc)
    return image, result, False


def append_event(image, record):
    """把一筆視覺品檢事件 append 到 catalog/visuals.jsonl（含檔案鎖）。"""
    ev = {"ts": cat.now_iso(), "event": "visual_qc",
          "visual_id": record.get("visual_id", Path(image).stem),
          "file": record.get("file", cat.relpath(image)),
          "visual_qc": record.get("visual_qc")}
    io.append_jsonl(VISUALS_JSONL, ev)
    return ev


def iter_visual_files():
    """遞迴走訪 assets/visuals 下的所有圖片。"""
    if not VISUALS_DIR.exists():
        return
    for p in sorted(VISUALS_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in VISUAL_EXTS:
            yield p


# ----------------------------- CLI -----------------------------

def _parse_size(s):
    try:
        w, h = (int(x) for x in s.lower().split("x"))
        return (w, h)
    except ValueError:
        sys.exit(f"--expect 格式應為 寬x高，例如 1536x1024（收到 {s!r}）")


def main():
    ap = argparse.ArgumentParser(description="用 ffmpeg 對 AI 生成的場景圖做自動品檢")
    ap.add_argument("files", nargs="*", help="指定圖片（預設掃描 assets/visuals 全部）")
    ap.add_argument("--all", action="store_true", help="掃描全部（已品檢者仍跳過，除非 --force）")
    ap.add_argument("--force", action="store_true", help="即使已有 visual_qc 也重算")
    ap.add_argument("--no-write", action="store_true", help="只印結果，不寫 sidecar")
    ap.add_argument("--expect", default="", metavar="WxH", help="比對尺寸，例如 1536x1024")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                    help="覆寫閾值，可重複，例如 --set black_ymax=25")
    args = ap.parse_args()

    media.require("ffmpeg", "ffprobe")

    thresholds = {}
    for kv in args.set:
        k, _, v = kv.partition("=")
        try:
            thresholds[k] = float(v)
        except ValueError:
            sys.exit(f"閾值格式錯誤: {kv}")
    expect = _parse_size(args.expect) if args.expect else None

    targets = [Path(f) for f in args.files] if args.files else list(iter_visual_files())
    targets = [t for t in targets if t.exists()]
    if not targets:
        sys.exit("找不到任何圖片（assets/visuals 是空的？）")

    results = [qc_file(t, force=args.force, thresholds=thresholds,
                       expect_size=expect, write=not args.no_write) for t in targets]

    if args.json:
        print(json.dumps({
            "images": [{"file": cat.relpath(p), "skipped": s, **r} for p, r, s in results],
        }, ensure_ascii=False, indent=2))
        return

    icons = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for p, r, skipped in results:
        if skipped:
            print(f"  ⏭  {p.name}（已有品檢，--force 可重算）")
            continue
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        dim = f"{r['width']}x{r['height']}" if r["width"] else "解不開"
        print(f"{icons.get(r['verdict'], '?')} {p.name}  score={r['score']}"
              f" | {dim} | Y {r['yavg']}/{r['ymax']} | SAT {r['satavg']}"
              f" | ent {r['entropy']}")
        if r["flags"]:
            print(f"      旗標: {', '.join(r['flags'])}")

    print(f"\n小計: pass={counts['pass']}  warn={counts['warn']}  fail={counts['fail']}")


if __name__ == "__main__":
    main()
