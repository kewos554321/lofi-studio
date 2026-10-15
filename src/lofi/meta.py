#!/usr/bin/env python3
"""
make_meta.py — 為成片產生 YouTube 上片資訊（title / description / chapters / tags）。

風格比照本頻道既有影片：
  [詩意短句] | [曲風描述]
  描述：開場 → 內容要點 → 沉浸段 → 放鬆句 → 互動 → Subscribe → Tracklist(章節) → hashtags

章節由「選用的音軌 + crossfade 秒數」推算（也可直接吃 build_long_mix 產生的 tracklist JSON）。
每首曲名取自 catalog 側錄的 mood（title case）。

用法:
  # 由軌道清單推算章節（最常用）
  python3 scripts/make_meta.py --video output/videos/lofi_30min.mp4 \
      --style rainy_lofi --tracks assets/tracks/rainy_lofi_*.mp3 --xfade 8

  # 直接吃 tracklist JSON
  python3 scripts/make_meta.py --video X.mp4 --style rainy_lofi --tracklist mix.tracklist.json

  # 只印不寫檔
  python3 scripts/make_meta.py --video X.mp4 --style rainy_lofi --print

輸出:
  publish/<影片名>.json   機器用（含狀態，給 upload_status.py）
  publish/<影片名>.md     人用（可複製到 YouTube）
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from lofi import catalog as cat
from lofi import config
from lofi import io
from lofi.media import ffprobe_duration
from lofi.paths import PUBLISH_DIR, STYLE_DIR, YT_DIR


def prettify(name):
    """rain on the window -> Rain on the Window"""
    name = re.sub(r"^\d+[-_]?", "", name)
    name = name.replace("_", " ").strip()
    small = {"of", "the", "a", "an", "in", "on", "at", "by", "for", "and", "to", "with", "from"}
    words = name.split()
    return " ".join(
        w.lower() if (i > 0 and w.lower() in small) else (w[:1].upper() + w[1:] if w else w)
        for i, w in enumerate(words)
    )


def stable_index(key, n):
    if n <= 0:
        return 0
    return sum(ord(c) for c in key) % n


def dominant(values):
    vals = [v for v in values if v]
    if not vals:
        return None
    return max(sorted(set(vals)), key=vals.count)


def fmt_ts(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ----------------------- config -----------------------

def load_yt_config(style):
    """優先 prompts/youtube/<style>.json，否則用 prompts/styles/<style>.json 合成。"""
    if style:
        p = YT_DIR / f"{style}.json"
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                cfg.setdefault("name", style)
                return cfg
            except json.JSONDecodeError:
                pass
    return synthesize_config(style)


def synthesize_config(style):
    """沒有 youtube 設定檔時，用風格骨架拼出合理的預設值。"""
    st = {}
    if style:
        sp = STYLE_DIR / f"{style}.json"
        if sp.exists():
            try:
                st = json.loads(sp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                st = {}
    label = (style or "").replace("_", " ").title()
    if label:
        descriptor = f"{label} Lofi Mix for Study & Relaxation"
        genre = f"{label.lower()} lofi"
    else:
        label = "Mellow Lofi"
        descriptor = "Mellow Lofi Mix for Study & Relaxation"
        genre = "mellow lofi"
    instr = (st.get("instruments") or ["mellow keys, dusty drums, warm bass"])[0]
    return {
        "name": style or "lofi",
        "emoji": "🎧",
        "title_pool": ["Quiet Hours", "Slow and Mellow", "Soft Focus", "Drift and Dream"],
        "descriptor_pool": [descriptor],
        "intro": "A soft, mellow lofi mix drifts in while the world slows down...",
        "genre_phrase": genre,
        "features": [
            f"Warm {instr}",
            "Dusty vinyl crackle and gentle tape saturation",
            "Soft, steady rhythms with a calm sidechain pulse",
            "Ideal for studying, working, or unwinding",
        ],
        "immersion": "Whether you're deep in focus, winding down, or just letting the music play — this is your quiet place to be.",
        "sip": "Breathe easy. Let the melody carry you.",
        "question": "What does this soundscape make you feel?",
        "subscribe": "Subscribe for more warm visuals & mellow lofi mixes every week.",
        "hashtags": ["#lofi", "#lofihiphop", "#studybeats", "#chillbeats", "#lofimix",
                     "#calmlife", "#aestheticlofi", "#relaxingmusic", "#focusmusic",
                     "#lofivibes", "#mellowbeats", "#lofiforstudy", "#backgroundmusic",
                     "#ambientlofi", "#lofiforwork", "#cozylofi", "#instrumentalbeats",
                     "#calmmusic", "#lofiradio", "#studywithme"],
        "tags": ["lofi", "lofi hip hop", "study beats", "chill beats", "mellow lofi",
                 "lofi mix", "relaxing music", "calm music", "focus music",
                 "background music", "aesthetic lofi", "instrumental beats",
                 "cozy lofi", "ambient lofi", "lofi radio", "study music", "chill music",
                 "lofi for work", "soft beats", "lofi vibes"],
        "category": "Music",
        "category_id": 10,
        "thumbnail_text": [label.upper(), "LOFI"],
    }


# ----------------------- 章節 / 曲目 -----------------------

# 專輯內同名時的修飾語池（key 用小寫比對；曲名來自 sidecar 的 mood 經 prettify）。
# 池子用完才退回 ` (pt. N)`，所以任何風格都不會撞名，只是名字會比較制式。
_TITLE_VARIANTS = {
    "slow weekend": ["Second Cup", "Open Window", "Warm Rug", "Late Sun", "Soft Rain",
                     "No Plans", "Kettle On", "Sunlit Floor", "Nothing to Do"],
    "cozy morning coffee": ["First Sip", "Warm Mug", "Window Seat", "Slow Stir",
                            "Second Cup", "Steam Rising", "Wooden Table", "Quiet Kitchen",
                            "Morning Light"],
    "gentle start of the day": ["First Light", "Soft Steps", "Curtains Open", "Slow Wake",
                                "Warm Room", "Quiet Morning", "Bare Feet"],
    "sunlit window": ["Dust Motes", "Curtain Breeze", "Warm Glass", "Afternoon",
                      "Plant on the Sill"],
    "spring morning breeze": ["Open Door", "Early Light"],
}


def unique_titles(titles):
    """專輯內曲名去重：同名者依出現順序加一個**不重複的修飾語**
    （例 `Slow Weekend, Second Cup`），池子用完才退回 ` (pt. N)`。

    為什麼：sidecar 的 `mood` 會重複（同風格多首共用同一句），同名會讓 DSP／版稅申報混淆；
    而 `(pt. 2)` 這種純編號在同一張專輯裡看起來像灌水。
    用「出現順序」配發（不隨機），確保重跑結果一致、可重現。
    """
    seen = {}
    out = []
    for t in titles:
        key = str(t or "").strip().lower()
        n = seen.get(key, 0) + 1
        seen[key] = n
        if n == 1:
            out.append(t)                      # 第一次出現：保留原名
            continue
        pool = _TITLE_VARIANTS.get(key) or []
        if n - 2 < len(pool):
            out.append(f"{t}, {pool[n - 2]}")  # 第二次起：加不重複的修飾語
        else:
            out.append(f"{t} (pt. {n})")       # 池子用完：退回原本的編號寫法
    return out


def track_title(audio):
    sc = cat.load_sidecar(audio)
    if sc and sc.get("mood"):
        return prettify(sc["mood"])
    stem = Path(audio).stem
    stem = re.sub(r"_\d{5}$", "", stem)      # 去掉 ComfyUI 序號
    style = None
    sc2 = cat.load_sidecar(audio)
    if sc2 and sc2.get("style"):
        style = sc2["style"]
        stem = re.sub(rf"^{re.escape(style)}_", "", stem)
    return prettify(stem)


def track_sidecar_meta(audio):
    sc = cat.load_sidecar(audio) or {}
    return {
        "title": track_title(audio),
        "key": sc.get("key"),
        "instruments": sc.get("instruments"),
        "bpm": sc.get("bpm"),
        "style": sc.get("style"),
    }


def track_model_credit(audio):
    """回傳該曲的模型來源（for 商用 provenance）；沒有記錄回 None。"""
    gen = (cat.load_sidecar(audio) or {}).get("gen") or {}
    model = gen.get("model")
    if not model:
        return None
    info = config.model_license(model)
    return {
        "model": model,
        "license": gen.get("model_license") or info["license"],
        "source": gen.get("model_source") or info["source"],
        "commercial": info["commercial"],
    }


def compute_timeline(tracks, xfade):
    """依 acrossfade 規則推算每首在混音中的起始秒數。"""
    tl = []
    cursor = 0.0
    for i, f in enumerate(tracks):
        d = ffprobe_duration(f) or 0.0
        if i > 0:
            cursor += (prev_d - xfade)
        tl.append({"file": str(f), "start": cursor, "duration": d,
                   "title": track_title(f), "meta": track_sidecar_meta(f)})
        prev_d = d
    # 同一個 mood 會有多首 → 曲名去重（否則 YT 說明的 Tracklist 與章節會出現同名）
    for row, t in zip(tl, unique_titles([r["title"] for r in tl])):
        row["title"] = t
    return tl


def load_tracklist(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("tracks", data if isinstance(data, list) else [])


# ----------------------- 文字組裝 -----------------------

def build_credits_block(credits):
    """YouTube 描述用的 AI 揭露＋模型授權（商用 provenance）。回傳行清單。

    為什麼要放進**描述**而不是只留在本機 JSON：DistroKid／Gumroad 都要 AI 揭露，
    YouTube 也要求「變造或合成內容」揭露；而 `docs/LICENSING.md` 說 provenance 是賣點
    ——查不到等於沒有。少了這段，頻道描述那句「授權清單隨每次發行附上」就只在商店成立。
    """
    if not credits:
        return []
    out = ["—", "", "🎛 AI disclosure & licences",
           "Music and artwork are created with AI models licensed for commercial use."]
    for c in credits:
        model = str(c.get("model") or "").split("/")[-1]
        out.append(f"- {model} ({c.get('license')})")
    out += ["", "The full model and licence list ships with every album release."]
    return out


def _page_bounds(shot, white=250):
    """量 qlmanage 輸出裡「頁面」的實際邊界（畫布是方形、四周可能是白邊）。

    回傳 (x, y, w, h)；量不到回 None。純標準庫：先請 ffmpeg 轉灰階 raw 再掃描。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "g.raw"
        rc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                             "-i", str(shot), "-f", "rawvideo", "-pix_fmt", "gray", str(raw)],
                            capture_output=True).returncode
        if rc != 0 or not raw.exists():
            return None
        data = raw.read_bytes()
    side = int(len(data) ** 0.5)
    if side * side != len(data):                     # 非方形就別猜
        return None
    row = [sum(data[y * side:(y + 1) * side]) / side for y in range(side)]
    nw = [y for y, v in enumerate(row) if v < white]
    if not nw:
        return None
    y0, y1 = nw[0], nw[-1]
    col = [sum(data[y * side + x] for y in range(y0, y1 + 1, 8)) / max(1, len(range(y0, y1 + 1, 8)))
           for x in range(side)]
    cw = [x for x, v in enumerate(col) if v < white]
    if not cw:
        return None
    x0, x1 = cw[0], cw[-1]
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def _sips_dpi(path, dpi=72):
    """把 JPEG 標成 72 DPI（Gumroad／YouTube 的慣例值）。用 macOS 內建 sips，沒有就跳過。"""
    try:
        return subprocess.run(["sips", "-s", "dpiWidth", str(dpi), "-s", "dpiHeight", str(dpi),
                               str(path)], capture_output=True).returncode == 0
    except OSError:
        return False


def make_thumbnail(text_lines, art, dst, size=(1280, 720)):
    """產一張 YouTube 縮圖：封面圖 + 底部暗階 + 標題文字（粗體、左下）。

    text_lines：list[str]（例 `["COZY", "MORNING", "LOFI"]`）
    art：封面圖路徑（建議 16:9）
    回傳 dst 或 None（沒有 qlmanage／渲染失敗）。
    """
    import shutil, tempfile
    if not art or not Path(art).exists() or not shutil.which("qlmanage"):
        return None
    lines = [str(x).strip() for x in (text_lines or []) if str(x).strip()]
    if not lines:
        return None
    w, h = size
    font = 74 if len(lines) <= 3 else 60
    spans = "".join(f"<span>{_html_escape(x)}</span>" for x in lines)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden}}
  .b{{position:relative;width:{w}px;height:{h}px;
     background:url('file://{Path(art).resolve()}') center/cover no-repeat}}
  .s{{position:absolute;inset:0;
     background:linear-gradient(to top, rgba(0,0,0,.74) 0%, rgba(0,0,0,0) 58%)}}
  .t{{position:absolute;left:{int(w*0.044)}px;bottom:{int(h*0.061)}px;color:#fff;
     font-family:"Arial Black",Arial,Helvetica,sans-serif;font-weight:900;
     line-height:.94;font-size:{font}px;letter-spacing:1px;
     text-shadow:0 3px 14px rgba(0,0,0,.6)}}
  .t span{{display:block}}
</style></head><body><div class="b"><div class="s"></div>
  <div class="t">{spans}</div></div></body></html>"""
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "thumb.html"
        page.write_text(html, encoding="utf-8")
        rc = subprocess.run(["qlmanage", "-t", "-s", str(w), "-o", td, str(page)],
                            capture_output=True).returncode
        shot = Path(td) / "thumb.html.png"
        if rc != 0 or not shot.exists():
            return None
        # qlmanage 的畫布是方形、頁面在頂端，而且**會縮放**（實測 720 CSS px → 900 px，
        # 因為它的版面寬是 1024）。所以不寫死裁切框：先量出頁面在畫布上的實際邊界再裁＋縮放。
        box = _page_bounds(shot)
        if box is None:
            return None
        bx, by, bw, bh = box
        rc2 = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                              "-i", str(shot), "-vf",
                              f"crop={bw}:{bh}:{bx}:{by},scale={w}:{h}:flags=lanczos",
                              "-q:v", "2", str(dst)], capture_output=True).returncode
        if rc2 != 0:
            return None
    _sips_dpi(dst)
    return dst


def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_thumbnail_text(rec):
    """縮圖文字：優先用上片資訊裡的 thumbnail_text，沒有就從標題推。"""
    t = rec.get("thumbnail_text")
    if isinstance(t, (list, tuple)) and t:
        return [str(x) for x in t]
    if isinstance(t, str) and t.strip():
        return [x.strip() for x in re.split(r"[|/]", t) if x.strip()]
    title = str(rec.get("title") or "")
    head = re.split(r"[|/]", title)[0].strip()
    return [w for w in head.upper().split() if w][:3]


def build_description(cfg, duration, chapters, credits=None):
    features = cfg.get("features") or []
    lines = []
    lines.append(f"{cfg.get('emoji','')} {cfg.get('intro','')}".strip())
    lines.append("")
    # 主要樂器 / 調性：以入選曲目的眾數為準
    instr = (cfg.get("_instruments") or "").strip()
    key = (cfg.get("_key") or "").strip()
    head = f"This {cfg.get('genre_phrase','lofi')} mix features {instr or 'warm keys, dusty drums, and soft bass'}"
    if key:
        head += f" in {key}"
    head += ". Woven with vinyl crackle and ambient texture, it's made to sit with you quietly."
    lines.append(head)
    lines.append("")
    lines.append("—")
    lines.append("")
    lines.append("🎧 What you'll find here:")
    lines.append("")
    for feat in features:
        lines.append(f"- {feat}")
    lines.append("")
    if cfg.get("immersion"):
        lines.append(cfg["immersion"])
        lines.append("")
    if cfg.get("sip"):
        lines.append(f"☕ {cfg['sip']}")
        lines.append("")
    lines.append("—")
    lines.append("")
    if cfg.get("question"):
        lines.append(f"💬 {cfg['question']}")
    if cfg.get("subscribe"):
        lines.append(f"🔔 {cfg['subscribe']}")

    if chapters and len(chapters) >= 3:
        lines.append("")
        lines.append("—")
        lines.append("")
        lines.append("Tracklist")
        for ch in chapters:
            lines.append(f"{ch['time']} {ch['title']}")

    if credits:
        lines.append("")
        lines.extend(build_credits_block(credits))

    if cfg.get("hashtags"):
        lines.append("")
        lines.append(" ".join(cfg["hashtags"]))
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="產生 YouTube 上片資訊")
    ap.add_argument("--video", required=True, help="成片路徑（output/videos/xxx.mp4）")
    ap.add_argument("--name", default="", help="上片資訊識別名（預設用影片檔名）")
    ap.add_argument("--style", default="", help="風格名（prompts/styles/<style>.json）")
    ap.add_argument("--tracks", nargs="*", default=[], help="混音用到的音軌（依序）")
    ap.add_argument("--xfade", type=float, default=8.0, help="crossfade 秒數（預設 8）")
    ap.add_argument("--tracklist", default="", help="改吃 tracklist JSON（含 tracks[]）")
    ap.add_argument("--title", default="", help="覆寫標題")
    ap.add_argument("--print", dest="to_stdout", action="store_true", help="只印出，不寫檔")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"找不到影片 {video}")
    stem = args.name or video.stem
    duration = ffprobe_duration(video)

    cfg = load_yt_config(args.style)

    # 章節
    if args.tracklist and Path(args.tracklist).exists():
        tl = load_tracklist(args.tracklist)
        for t in tl:
            t.setdefault("title", prettify(Path(t.get("file", "")).stem))
    elif args.tracks:
        tl = compute_timeline(args.tracks, args.xfade)
    else:
        tl = []

    # clamp 到影片長度，且過濾掉起點超過片長的
    chapters = []
    if tl and duration:
        for t in tl:
            if t.get("start", 0) >= duration:
                break
            chapters.append({"time": fmt_ts(t["start"]), "title": t.get("title", "Untitled")})

    # 主樂器 / 調性（取入選曲目眾數）
    metas = [t.get("meta", {}) for t in tl]
    cfg["_instruments"] = dominant([m.get("instruments") for m in metas]) or ""
    cfg["_key"] = dominant([m.get("key") for m in metas]) or ""

    # 標題
    if args.title:
        title = args.title
    else:
        tp = cfg.get("title_pool") or ["Lofi Mix"]
        dp = cfg.get("descriptor_pool") or ["Lofi Mix"]
        phrase = tp[stable_index(stem, len(tp))]
        desc = dp[stable_index(stem + "d", len(dp))]
        title = f"{phrase} | {desc}"

    # 商用 provenance：彙整用到的音樂模型（授權/來源）；**先算**，才能一起寫進描述
    credits = {}
    for t in tl:
        c = track_model_credit(t.get("file"))
        if c:
            credits[(c["model"], c["license"], c["source"])] = c
    credits = sorted(credits.values(), key=lambda x: x["model"])

    description = build_description(cfg, duration, chapters, credits)

    record = {
        "video": cat.relpath(video),
        "video_file": video.name,
        "duration": round(duration, 1) if duration else None,
        "style": args.style or cfg.get("name", ""),
        "title": title,
        "description": description,
        "tags": cfg.get("tags", []),
        "hashtags": cfg.get("hashtags", []),
        "category": cfg.get("category", "Music"),
        "category_id": cfg.get("category_id", 10),
        "chapters": chapters,
        "thumbnail_text": cfg.get("thumbnail_text", []),
        "credits": credits,
        "ai_disclosure": True,
        "status": "pending",
        "generated_at": cat.now_iso(),
        "uploaded_at": None,
        "youtube_url": None,
    }

    # 長度警告：混音音軌總長 < 影片（尾段會沒聲音）
    tl_total = None
    if tl:
        last = tl[-1]
        tl_total = last.get("start", 0) + (last.get("duration") or 0)
    if tl_total and duration and tl_total + 5 < duration:
        record["warning"] = f"音軌總長僅 {fmt_ts(tl_total)}，短於影片 {fmt_ts(duration)}（尾段可能無聲）"

    if args.to_stdout:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return

    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    io.atomic_write_json(PUBLISH_DIR / f"{stem}.json", record)

    md = [f"# {title}", "", f"- 影片: `{record['video']}`",
          f"- 長度: {record['duration']}s | 風格: {record['style']} | 章節: {len(chapters)}",
          f"- 狀態: {record['status']}", ""]
    if record.get("warning"):
        md += [f"> ⚠️ {record['warning']}", ""]
    if credits:
        md += ["- 模型 credits（商用 provenance）:"] + [
            f"  - `{c['model']}` — {c['license']}{(' · ' + c['source']) if c['source'] else ''}"
            for c in credits] + [""]
    md += ["## Title", "", "```", title, "```", "", "## Description", "", "```text",
           description.rstrip(), "```", "", "## Tags", "", "```",
           ", ".join(record["tags"]), "```", ""]
    io.atomic_write_text(PUBLISH_DIR / f"{stem}.md", "\n".join(md))

    print(f"✅ {stem}")
    print(f"   標題: {title}")
    print(f"   章節: {len(chapters)} 段 | tags: {len(record['tags'])} 個")
    print(f"   → publish/{stem}.json  publish/{stem}.md")
    if record.get("warning"):
        print(f"   ⚠️  {record['warning']}")


if __name__ == "__main__":
    main()
