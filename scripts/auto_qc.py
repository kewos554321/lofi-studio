#!/usr/bin/env python3
"""
auto_qc.py — 用 ffmpeg/ffprobe 對生成音樂做自動品檢（P1）。

純標準庫（不需要 numpy / librosa），所以用系統 python3 也能跑。
會對每首算出：
  - 時長是否短少（被截斷）
  - 響度（LUFS）/ 真峰值（dBTP）/ 是否削波
  - 靜音比例（死寂）
  - DC offset
  - 高頻能量（金屬感 / 刺耳的粗略代理）
  - 概略 BPM（與 prompt 要求的 BPM 比對）
  - 能量指紋（寫入 sidecar，供未來更強的近似比對使用）

結果寫回 sidecar 的 auto_qc 欄位，並可同步重建 catalog/index.csv。
明顯壞掉的曲子 status 直接標成 reject，不必進人工複審。

用法:
  python3 scripts/auto_qc.py                      # 掃描 assets/tracks 尚未品檢的
  python3 scripts/auto_qc.py --all                # 掃描全部（已品檢者仍會跳過）
  python3 scripts/auto_qc.py --all --force        # 強制重算
  python3 scripts/auto_qc.py FILE [FILE ...]      # 只檢查指定檔
  python3 scripts/auto_qc.py --dedupe             # 額外檢查完全相同的重複曲（sha1）
  python3 scripts/auto_qc.py --index              # 順便重建 catalog/index.csv
  python3 scripts/auto_qc.py --json               # 以 JSON 輸出結果

閾值可用 --set key=value 覆寫（key 見 DEFAULT_THRESHOLDS）。
"""
import argparse
import array
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_lib as cat  # noqa: E402

DEFAULT_THRESHOLDS = {
    "duration_ratio_min": 0.9,    # 實際/要求 < 此值 -> 截斷
    "lufs_min": -18.0,            # 響度低於此 -> warn
    "lufs_max": -10.0,            # 響度高於此 -> warn
    "true_peak_warn": 0.0,        # 真峰值高於此 -> warn（intersample 過衝）
    "true_peak_hard": 2.0,        # 真峰值高於此 -> fail（嚴重削波）
    "silence_ratio_warn": 0.25,   # 靜音比例高於此 -> warn
    "silence_ratio_hard": 0.5,    # 靜音比例高於此 -> fail
    "dc_offset_max": 0.01,        # DC offset 超過 -> warn
    "rms_floor_db": -50.0,        # 整體 RMS 低於此 -> fail（近乎無聲）
    "high_band_warn_db": -10.0,   # 6kHz 以上能量相對全頻 > 此 -> warn（刺耳）
    "crest_warn_max": 20.0,       # 峰值-均值 > 此 -> warn（動態過大/爆音風險）
    "bpm_tolerance": 12.0,        # 概略 BPM 與要求差距 > 此 -> warn
    "dedupe_similarity": 0.985,   # （保留）指紋相似度門檻，僅供未來實驗用
}


# ----------------------------- 底層量測 -----------------------------

def _run(cmd):
    """執行 ffmpeg 並回傳 stderr（filter 的統計都印在 stderr）。"""
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def measure_ebur128(path):
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                "-af", "ebur128=peak=true", "-f", "null", "-"])
    # 結尾的「Integrated loudness / LRA / True peak」summary 行沒有 t: 前綴，
    # 逐格輸出行則以 [Parsed_ebur128...] 開頭，故用行首錨定只取 summary。
    i = re.search(r"^\s+I:\s*(-?\d+\.?\d*)\s*LUFS", err, re.M)
    lra = re.search(r"^\s+LRA:\s*(-?\d+\.?\d*)\s*LU", err, re.M)
    peaks = [float(x) for x in re.findall(r"^\s+Peak:\s*(-?\d+\.?\d*)\s*dBFS", err, re.M)]
    return {
        "lufs": float(i.group(1)) if i else None,
        "lra": float(lra.group(1)) if lra else None,
        "true_peak": max(peaks) if peaks else None,
    }


def _astats_overall(err):
    idx = err.rfind("Overall")
    return err[idx:] if idx != -1 else err


def measure_astats(path, pre_args=(), af=None):
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *pre_args, "-i", str(path)]
    chain = (af + "," if af else "") + "astats=metadata=1:reset=0"
    cmd += ["-af", chain, "-f", "null", "-"]
    sec = _astats_overall(_run(cmd))

    def g(name):
        m = re.search(rf"{re.escape(name)}:\s*(-?\d+\.?\d*)", sec)
        return float(m.group(1)) if m else None

    return {
        "dc_offset": g("DC offset"),
        "peak_db": g("Peak level dB"),
        "rms_db": g("RMS level dB"),
        "flat_factor": g("Flat factor"),
        "zcr": g("Zero crossings rate"),
        "peak_count": g("Peak count"),
    }


def measure_silence_ratio(path, duration):
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                "-af", "silencedetect=noise=-50dB:d=1", "-f", "null", "-"])
    durs = [float(x) for x in re.findall(r"silence_duration:\s*(-?\d+\.?\d*)", err)]
    if not duration:
        return None
    return min(1.0, sum(durs) / duration)


def _mono_samples(path, sr):
    """用 ffmpeg 解成 mono int16，回傳 array('h')（原生位元組序已修正）。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
         "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True,
    ).stdout
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _rms(vals):
    if not vals:
        return 0.0
    total = 0.0
    for v in vals:
        total += v * v
    return math.sqrt(total / len(vals)) / 32768.0


def estimate_bpm(path):
    """用能量包絡在合理 lag 範圍內做自相關，粗估 BPM（僅供參考）。"""
    sr = 11025
    samples = _mono_samples(path, sr)
    n = len(samples)
    if n < sr * 4:
        return None
    frame, hop = 1024, 512
    nf = (n - frame) // hop
    if nf < 32:
        return None
    env = [_rms(samples[i * hop:i * hop + frame]) for i in range(nf)]
    onset = [max(0.0, env[i + 1] - env[i]) for i in range(len(env) - 1)]
    mean = sum(onset) / len(onset)
    onset = [v - mean for v in onset]

    fps = sr / hop
    lag_min = max(1, int(60.0 / 180.0 * fps))
    lag_max = max(lag_min + 1, int(60.0 / 60.0 * fps))
    best_lag, best_val = None, float("-inf")
    for lag in range(lag_min, lag_max + 1):
        s = 0.0
        for i in range(len(onset) - lag):
            s += onset[i] * onset[i + lag]
        if s > best_val:
            best_val, best_lag = s, lag
    if not best_lag:
        return None
    return round(60.0 * fps / best_lag, 1)


def fingerprint(path, dims=40):
    """把整首依正規化位置切成 dims 段，取每段 RMS，做 L2 正規化（與長度無關）。"""
    samples = _mono_samples(path, 8000)
    total = len(samples)
    if total < dims * 8:
        return None
    vec = []
    for k in range(dims):
        a, b = k * total // dims, (k + 1) * total // dims
        vec.append(_rms(samples[a:b]))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return None
    return [round(v / norm, 4) for v in vec]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# ----------------------------- 判定 -----------------------------

def analyze(path, requested_duration=None, thresholds=None):
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    duration = ffprobe_duration(path)
    loud = measure_ebur128(path)
    stats = measure_astats(path)
    silence = measure_silence_ratio(path, duration)
    high = measure_astats(path, af="highpass=f=6000")
    bpm_est = estimate_bpm(path)
    fp = fingerprint(path)

    hard, warn = [], []

    # 截斷
    if requested_duration and duration and duration < th["duration_ratio_min"] * requested_duration:
        hard.append("truncated")

    # 削波 / 真峰值（ACE-Step 輸出普遍偏燙，但 mix 階段會正規化，
    # 故只有「嚴重」才 fail，一般過衝只 warn 供人工優先複審）
    tp = loud.get("true_peak")
    if tp is not None:
        if tp > th["true_peak_hard"]:
            hard.append("clipping")
        elif tp > th["true_peak_warn"]:
            warn.append("hot_true_peak")

    # 響度
    lufs = loud.get("lufs")
    if lufs is not None and not (th["lufs_min"] <= lufs <= th["lufs_max"]):
        warn.append("loudness_out_of_range")

    # 靜音 / 近乎無聲
    if (stats.get("rms_db") if stats.get("rms_db") is not None else 0) < th["rms_floor_db"]:
        hard.append("near_silent")
    if silence is not None:
        if silence > th["silence_ratio_hard"]:
            hard.append("mostly_silent")
        elif silence > th["silence_ratio_warn"]:
            warn.append("trailing_silence")

    # DC offset
    if stats.get("dc_offset") is not None and abs(stats["dc_offset"]) > th["dc_offset_max"]:
        warn.append("dc_offset")

    # 高頻能量（金屬感粗略代理）
    high_band_db = None
    if stats.get("rms_db") is not None and high.get("rms_db") is not None:
        high_band_db = high["rms_db"] - stats["rms_db"]
        if high_band_db > th["high_band_warn_db"]:
            warn.append("harsh_highs")

    # 動態（峰值-均值）
    crest = None
    if stats.get("peak_db") is not None and stats.get("rms_db") is not None:
        crest = stats["peak_db"] - stats["rms_db"]
        if crest > th["crest_warn_max"]:
            warn.append("wide_dynamics")

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
        "duration": round(duration, 2) if duration else None,
        "lufs": round(lufs, 1) if lufs is not None else None,
        "lra": round(loud["lra"], 1) if loud.get("lra") is not None else None,
        "true_peak": round(tp, 2) if tp is not None else None,
        "peak_db": round(stats["peak_db"], 2) if stats.get("peak_db") is not None else None,
        "rms_db": round(stats["rms_db"], 2) if stats.get("rms_db") is not None else None,
        "crest_db": round(crest, 1) if crest is not None else None,
        "dc_offset": round(stats["dc_offset"], 5) if stats.get("dc_offset") is not None else None,
        "silence_ratio": round(silence, 3) if silence is not None else None,
        "high_band_db": round(high_band_db, 1) if high_band_db is not None else None,
        "bpm_est": bpm_est,
        "fingerprint": fp,
    }


def qc_file(audio, force=False, thresholds=None, write=True):
    """對單一檔案品檢；write=True 時寫回 sidecar 並 append 事件。回傳 (audio, record, skipped)。"""
    audio = Path(audio)
    sc = cat.load_sidecar(audio) or {}
    if sc.get("auto_qc") and not force:
        return audio, sc["auto_qc"], True

    requested = (sc.get("gen") or {}).get("duration")
    result = analyze(audio, requested_duration=requested, thresholds=thresholds)

    # BPM 比對：est 誤差大是常態，僅記錄供參考，不列入 verdict
    want_bpm = sc.get("bpm")
    if want_bpm and result.get("bpm_est"):
        try:
            result["bpm_want"] = float(want_bpm)
            result["bpm_match"] = abs(float(want_bpm) - float(result["bpm_est"])) <= DEFAULT_THRESHOLDS["bpm_tolerance"]
        except (TypeError, ValueError):
            pass

    if write:
        if not sc:
            sc = {"track_id": audio.stem, "file": cat.relpath(audio)}
        sc["auto_qc"] = result
        # 自動硬失敗才改狀態；已人工複審過的不覆蓋
        if result["verdict"] == "fail" and sc.get("status", "unreviewed") == "unreviewed":
            sc["status"] = "reject"
        if not sc.get("status"):
            sc["status"] = "unreviewed"
        cat.upsert_track(audio, sc, event="auto_qc")
    return audio, result, False


def run_dedupe(thresholds=None):
    """回傳完全相同的內容分組（依 sha1）。

    註：免依賴的能量指紋鑑別力不足（同風格曲動輒 0.99），會誤報，
    故不作為警示依據。真正的近似/翻唱比對需 chromaprint 或 librosa。
    指紋仍寫入 sidecar，留待未來更強指標使用。
    """
    catalog = cat.load_catalog()
    groups = {}
    for tid, rec in catalog.items():
        h = rec.get("sha1")
        if h:
            groups.setdefault(h, []).append(tid)
    dupes = [ids for ids in groups.values() if len(ids) > 1]
    dupes.sort(key=len, reverse=True)
    return dupes


# ----------------------------- CLI -----------------------------

def main():
    ap = argparse.ArgumentParser(description="用 ffmpeg 對生成音樂做自動品檢")
    ap.add_argument("files", nargs="*", help="指定音檔（預設掃描 assets/tracks 全部）")
    ap.add_argument("--all", action="store_true", help="掃描全部（已品檢者仍跳過，除非 --force）")
    ap.add_argument("--force", action="store_true", help="即使已有 auto_qc 也重算")
    ap.add_argument("--no-write", action="store_true", help="只印結果，不寫回 sidecar")
    ap.add_argument("--dedupe", action="store_true", help="額外檢查完全相同的重複曲（sha1）")
    ap.add_argument("--index", action="store_true", help="結束後重建 catalog/index.csv")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                    help="覆寫閾值，可重複，例如 --set true_peak_warn=-0.5")
    args = ap.parse_args()

    thresholds = {}
    for kv in args.set:
        k, _, v = kv.partition("=")
        try:
            thresholds[k] = float(v)
        except ValueError:
            sys.exit(f"閾值格式錯誤: {kv}")

    targets = [Path(f) for f in args.files] if args.files else list(cat.iter_audio_files())
    targets = [t for t in targets if t.exists()]
    if not targets:
        sys.exit("找不到任何音檔（assets/tracks 是空的？）")

    results = []
    for t in targets:
        results.append(qc_file(t, force=args.force, thresholds=thresholds,
                               write=not args.no_write))

    dupes = run_dedupe(thresholds) if args.dedupe else []
    idx, n = cat.reconstruct_index() if args.index else (None, None)

    if args.json:
        print(json.dumps({
            "tracks": [{"file": cat.relpath(a), "skipped": s, **r} for a, r, s in results],
            "duplicates": [{"ids": ids} for ids in dupes],
            "index": cat.relpath(idx) if idx else None,
        }, ensure_ascii=False, indent=2))
        return

    icons = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for a, r, skipped in results:
        if skipped:
            print(f"  ⏭  {a.name}（已有品檢，--force 可重算）")
            continue
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"{icons.get(r['verdict'],'?')} {a.name}  score={r['score']}"
              f" | {r['duration']}s | {r['lufs']} LUFS | TP {r['true_peak']} dB"
              f" | BPM~{r['bpm_est']}")
        if r["flags"]:
            print(f"      旗標: {', '.join(r['flags'])}")

    print(f"\n小計: pass={counts['pass']}  warn={counts['warn']}  fail={counts['fail']}")

    if dupes:
        print("\n⚠️  完全相同的內容（sha1 重複，建議刪掉多餘的）:")
        for ids in dupes[:20]:
            print("   " + " = ".join(ids))
    if idx:
        print(f"\n📄 已重建索引: {cat.relpath(idx)}（{n} 首）")


if __name__ == "__main__":
    main()
