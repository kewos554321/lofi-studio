#!/usr/bin/env python3
"""
backfill_catalog.py — 把「已經生成、但沒有目錄紀錄」的舊曲子補回 catalog（P0）。

會盡量從既有資料還原每首的來源：
  - 檔名         -> style / id（例：cozy_morning_16_00001.mp3 -> style=cozy_morning, id=16）
  - prompts/generated/<style>.csv 或 prompts/prompt_library.csv -> prompt / mood / key / bpm ...
  - prompts/styles/<style>.json   -> style_version / 建議時長
  - output/logs/*.log             -> seed（若 log 還在）
  - 檔案本身      -> sha1 / 時長 / 建立時間

用法:
  python3 scripts/backfill_catalog.py --dry-run    # 先看會補什麼，不寫檔
  python3 scripts/backfill_catalog.py              # 寫入 sidecar（已存在的不覆蓋）
  python3 scripts/backfill_catalog.py --force      # 連已存在的 sidecar 也覆蓋
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_lib as cat  # noqa: E402

ROOT = cat.ROOT
LOGS_DIRS = [ROOT / "logs", ROOT / "output" / "logs"]
PROMPT_LIB = ROOT / "prompts" / "prompt_library.csv"

# cozy_morning_16_00001.mp3 -> style="cozy_morning", id="16"
NAME_ID_RE = re.compile(r"^(?P<style>.+?)_(?P<id>\d{2})_\d{5}$")
# lofi_rainy_night_00001.mp3 -> style="lofi_rainy_night"
NAME_RE = re.compile(r"^(?P<style>.+)_\d{5}$")
LOG_BLOCK_RE = re.compile(r"id=(\d+)[^\n]*?seed=(\d+)(.*?)(?=\n\s*\[|\Z)", re.S)
LOG_FILE_RE = re.compile(r"->\s*(\S+\.(?:mp3|wav|flac))")


def scan_seeds_from_logs():
    """回傳 {檔名: seed}；同一檔名多筆時以最後出現者為準。"""
    seeds = {}
    for logs_dir in LOGS_DIRS:
        if not logs_dir.exists():
            continue
        for log in sorted(logs_dir.glob("*.log")):
            text = log.read_text(encoding="utf-8", errors="ignore")
            for m in LOG_BLOCK_RE.finditer(text):
                seed = int(m.group(2))
                fm = LOG_FILE_RE.search(m.group(3))
                if fm:
                    seeds[Path(fm.group(1)).name] = seed
    return seeds


def load_rows():
    """把所有 CSV 依『列的 style 欄』分組；prompt_library 歸到 style=""。
    回傳 {style: {id: (row, source_path)}}。同一 id 以檔名排序第一個出現者為準。"""
    groups = {}
    gen_dir = ROOT / "prompts" / "generated"
    files = [PROMPT_LIB]
    if gen_dir.exists():
        files += sorted(gen_dir.glob("*.csv"))
    for path in files:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rid = (row.get("id") or "").strip()
                if not rid:
                    continue
                key = (row.get("style") or "").strip()
                groups.setdefault(key, {})
                groups[key].setdefault(rid, (row, path))
    return groups


def parse_name(stem):
    m = NAME_ID_RE.match(stem)
    if m:
        return m.group("style"), m.group("id")
    m = NAME_RE.match(stem)
    if m:
        return m.group("style"), None
    return None, None


def build_record(audio, groups, seeds):
    stem = audio.stem
    style, rid = parse_name(stem)
    entry = None
    if style:
        entry = groups.get(style, {}).get(rid or "")
    if not entry and rid:
        # 沒有對應風格（例如 prompt_library.csv 產生的 lofi_XX）就退回預設清單
        entry = groups.get("", {}).get(rid)
    row, src_path = entry if entry else ({}, None)

    # 只有真的存在 prompts/styles/<style>.json 才算「風格曲」
    real_style = style if (style and (ROOT / "prompts" / "styles" / f"{style}.json").exists()) else ""

    record = {
        "track_id": stem,
        "file": cat.relpath(audio),
        "sha1": cat.sha1_file(audio),
        "source": "backfill",
        "source_csv": cat.relpath(src_path) if src_path else "",
        "created_at": cat.now_iso(),
        "style": real_style or row.get("style", ""),
        "style_version": cat.style_version(real_style),
        "row_id": rid or row.get("id", ""),
        "mood": row.get("mood", ""),
        "instruments": row.get("instruments", ""),
        "key": row.get("key", ""),
        "bpm": int(row["bpm"]) if (row.get("bpm") or "").strip().isdigit() else None,
        "prompt": row.get("prompt", ""),
        "negative": row.get("negative", ""),
        "gen": {
            "seed": seeds.get(audio.name),
            "steps": None,
            "cfg_scale": None,
            "temperature": None,
            "duration": None,
            "model": "acestep_v1.5_turbo",
        },
        "status": "test" if stem.startswith("demo_") else "unreviewed",
        "tags": [],
        "review": {},
        "usage": [],
    }

    # 建議時長：優先用風格 JSON 的 duration
    if real_style:
        sp = ROOT / "prompts" / "styles" / f"{real_style}.json"
        if sp.exists():
            try:
                record["gen"]["duration"] = json.loads(sp.read_text(encoding="utf-8")).get("duration")
            except (json.JSONDecodeError, OSError):
                pass

    # 建立時間改用檔案 mtime（比 backfill 時間更真實）
    try:
        from datetime import datetime
        record["created_at"] = datetime.fromtimestamp(
            audio.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        pass
    return record


def main():
    ap = argparse.ArgumentParser(description="把既有音檔補進 catalog")
    ap.add_argument("--dry-run", action="store_true", help="只顯示，不寫檔")
    ap.add_argument("--force", action="store_true", help="覆蓋已存在的 sidecar")
    args = ap.parse_args()

    groups = load_rows()
    seeds = scan_seeds_from_logs()
    if seeds:
        print(f"從 log 撈到 {len(seeds)} 筆 seed")

    files = list(cat.iter_audio_files())
    if not files:
        sys.exit("assets/tracks 沒有音檔")

    added = skipped = 0
    for audio in files:
        existing = cat.load_sidecar(audio)
        if existing and not args.force:
            skipped += 1
            continue
        rec = build_record(audio, groups, seeds)
        missing = []
        if not rec["prompt"]:
            missing.append("prompt")
        if rec["gen"]["seed"] is None:
            missing.append("seed")
        note = f"  ⚠️ 缺 {', '.join(missing)}" if missing else ""
        print(f"  + {audio.name}  style={rec['style'] or '-'} id={rec['row_id'] or '-'}"
              f" seed={rec['gen']['seed'] or '-'}{note}")
        if not args.dry_run:
            cat.upsert_track(audio, rec, event="backfill")
        added += 1

    print(f"\n{'（dry-run）' if args.dry_run else ''}補入 {added} 首，略過已存在 {skipped} 首")
    if not args.dry_run:
        idx, n = cat.reconstruct_index()
        print(f"已重建索引: {cat.relpath(idx)}（{n} 首）")


if __name__ == "__main__":
    main()
