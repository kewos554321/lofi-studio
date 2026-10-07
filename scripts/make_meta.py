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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_lib as cat  # noqa: E402

ROOT = cat.ROOT
YT_DIR = ROOT / "prompts" / "youtube"
STYLE_DIR = ROOT / "prompts" / "styles"
PUBLISH_DIR = ROOT / "publish"


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
    return tl


def load_tracklist(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("tracks", data if isinstance(data, list) else [])


# ----------------------- 文字組裝 -----------------------

def build_description(cfg, duration, chapters):
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

    if cfg.get("hashtags"):
        lines.append("")
        lines.append(" ".join(cfg["hashtags"]))
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="產生 YouTube 上片資訊")
    ap.add_argument("--video", required=True, help="成片路徑（output/videos/xxx.mp4）")
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
    stem = video.stem
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

    description = build_description(cfg, duration, chapters)

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
    (PUBLISH_DIR / f"{stem}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = [f"# {title}", "", f"- 影片: `{record['video']}`",
          f"- 長度: {record['duration']}s | 風格: {record['style']} | 章節: {len(chapters)}",
          f"- 狀態: {record['status']}", ""]
    if record.get("warning"):
        md += [f"> ⚠️ {record['warning']}", ""]
    md += ["## Title", "", "```", title, "```", "", "## Description", "", "```text",
           description.rstrip(), "```", "", "## Tags", "", "```",
           ", ".join(record["tags"]), "```", ""]
    (PUBLISH_DIR / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")

    print(f"✅ {stem}")
    print(f"   標題: {title}")
    print(f"   章節: {len(chapters)} 段 | tags: {len(record['tags'])} 個")
    print(f"   → publish/{stem}.json  publish/{stem}.md")
    if record.get("warning"):
        print(f"   ⚠️  {record['warning']}")


if __name__ == "__main__":
    main()
