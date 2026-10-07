#!/usr/bin/env python3
"""
upload_status.py — 看成片哪些「今天可以上傳」，並記錄上傳狀態。

以 publish/<影片>.json 為準（由 make_meta.py 產生）。
沒有 metadata 的成片會列在「未準備」。

用法:
  python3 scripts/upload_status.py                 # 佇列總覽
  python3 scripts/upload_status.py --ready         # 只看可上傳的
  python3 scripts/upload_status.py --json
  python3 scripts/upload_status.py --mark-uploaded lofi_30min_cozy --url https://youtu.be/xxxx
  python3 scripts/upload_status.py --mark-scheduled lofi_10min
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from lofi import catalog as cat
from lofi import meta as mm

ROOT = cat.ROOT
PUBLISH_DIR = ROOT / "publish"
VIDEOS_DIR = ROOT / "output" / "videos"
EPISODES_DIR = ROOT / "output" / "episodes"
LEGACY = {"demo_lofi"}  # 測試片不算正式成片

ICON = {"pending": "🟢", "scheduled": "🟡", "uploaded": "✅"}


def load_records():
    recs = {}
    if PUBLISH_DIR.exists():
        for p in sorted(PUBLISH_DIR.glob("*.json")):
            try:
                recs[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return recs


def all_videos():
    """回傳 {識別名: 影片路徑}。

    - 舊佈局：output/videos/<name>.mp4，識別名 = 檔名
    - 一集一包：output/episodes/<name>/video.mp4，識別名 = 資料夾名
    """
    out = {}
    if VIDEOS_DIR.exists():
        for p in sorted(VIDEOS_DIR.glob("*.mp4")):
            if p.stem in LEGACY:
                continue
            out[p.stem] = p
    if EPISODES_DIR.exists():
        for d in sorted(EPISODES_DIR.iterdir()):
            if not d.is_dir() or d.name in LEGACY:
                continue
            v = d / "video.mp4"
            if v.exists():
                out[d.name] = v
    return out


def duration_of(stem, rec, path):
    if rec and rec.get("duration"):
        return rec["duration"]
    return mm.ffprobe_duration(path)


def refresh_index(rows):
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    with (PUBLISH_DIR / "index.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "stem", "duration", "style", "status", "title", "chapters",
            "warning", "uploaded_at", "youtube_url"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ready", action="store_true", help="只列可上傳（pending）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-duration", type=float, default=300.0, help="視為成片的最短秒數（預設 300）")
    ap.add_argument("--mark-uploaded", metavar="STEM")
    ap.add_argument("--mark-scheduled", metavar="STEM")
    ap.add_argument("--url", default="", help="搭配 --mark-uploaded 記錄網址")
    args = ap.parse_args()

    if args.mark_uploaded or args.mark_scheduled:
        stem = args.mark_uploaded or args.mark_scheduled
        p = PUBLISH_DIR / f"{stem}.json"
        if not p.exists():
            sys.exit(f"找不到 publish/{stem}.json（先用 make_meta.py 產生）")
        rec = json.loads(p.read_text(encoding="utf-8"))
        if args.mark_uploaded:
            rec["status"] = "uploaded"
            rec["uploaded_at"] = cat.now_iso()
            if args.url:
                rec["youtube_url"] = args.url
        else:
            rec["status"] = "scheduled"
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ {stem} → {rec['status']}" + (f" ({rec.get('youtube_url')})" if rec.get("youtube_url") else ""))
        return

    recs = load_records()
    videos = all_videos()

    rows = []
    for stem, path in videos.items():
        rec = recs.get(stem)
        dur = duration_of(stem, rec, path)
        if rec:
            rows.append({
                "stem": stem,
                "duration": dur,
                "style": rec.get("style", ""),
                "status": rec.get("status", "pending"),
                "title": rec.get("title", ""),
                "chapters": len(rec.get("chapters") or []),
                "warning": (rec.get("warning") or ""),
                "uploaded_at": rec.get("uploaded_at") or "",
                "youtube_url": rec.get("youtube_url") or "",
                "_ready": rec.get("status", "pending") == "pending" and (dur or 0) >= args.min_duration,
            })
        else:
            rows.append({
                "stem": stem, "duration": dur, "style": "", "status": "unprepared",
                "title": "", "chapters": 0, "warning": "", "uploaded_at": "",
                "youtube_url": "", "_ready": False,
            })

    rows.sort(key=lambda r: (r["status"] != "pending", -(r["duration"] or 0)))
    refresh_index([{k: v for k, v in r.items() if k != "_ready"} for r in rows])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    ready = [r for r in rows if r["_ready"]]
    if args.ready:
        print(f"🌀 今天可上傳（{len(ready)} 支）")
        for r in ready:
            extra = f"  ⚠️ {r['warning']}" if r["warning"] else ""
            print(f"  {ICON['pending']} {r['stem']:<24} {mm.fmt_ts(r['duration'] or 0):>8}"
                  f"  [{r['style'] or 'mixed'}] 章節 {r['chapters']} | {r['title']}{extra}")
        return

    print("=" * 88)
    print(f" 可上傳（pending, ≥{int(args.min_duration)}s）: {len(ready)} 支")
    print("=" * 88)
    for g, label in (("pending", "待上傳"), ("scheduled", "已排程"), ("uploaded", "已上傳")):
        group = [r for r in rows if r["status"] == g]
        if not group:
            continue
        print(f"\n【{label}】{len(group)} 支")
        for r in group:
            icon = ICON.get(g, "•")
            extra = f"  ⚠️ {r['warning']}" if r["warning"] else ""
            print(f"  {icon} {r['stem']:<24} {mm.fmt_ts(r['duration'] or 0):>8}"
                  f"  [{r['style'] or 'mixed'}] 章節 {r['chapters']} | {r['title']}{extra}")

    unprep = [r for r in rows if r["status"] == "unprepared"]
    if unprep:
        print(f"\n【未準備 metadata】{len(unprep)} 支（用 make_meta.py 產生）")
        for r in unprep:
            print(f"  ⚪ {r['stem']:<24} {mm.fmt_ts(r['duration'] or 0):>8}")
    print(f"\n📄 已更新 publish/index.csv")


if __name__ == "__main__":
    main()
