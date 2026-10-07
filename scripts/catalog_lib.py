#!/usr/bin/env python3
"""
catalog_lib.py — 音樂目錄（catalog）共用工具。

設計：
  - sidecar：assets/tracks/<file>.json，記錄**單曲目前的完整狀態**（唯一真相）。
  - 事件流：catalog/tracks.jsonl，append-only，記錄每次生成 / 品檢 / 評分 / 標籤 / 使用。
  - 索引：catalog/index.csv，由 sidecar 掃描重建，方便用試算表看。

會被 batch_generate.py、auto_qc.py、backfill_catalog.py 等共用。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKS_DIR = ROOT / "assets" / "tracks"
CATALOG_DIR = ROOT / "catalog"
JSONL_PATH = CATALOG_DIR / "tracks.jsonl"
INDEX_CSV = CATALOG_DIR / "index.csv"

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")
SCHEMA_VERSION = 1


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def relpath(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def is_audio(p):
    return Path(p).suffix.lower() in AUDIO_EXTS


def sidecar_path(audio):
    audio = Path(audio)
    return audio.with_name(audio.name + ".json")


def sha1_file(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def iter_audio_files():
    """依檔名排序走訪 assets/tracks 下的音檔。"""
    if not TRACKS_DIR.exists():
        return
    for p in sorted(TRACKS_DIR.iterdir()):
        if p.is_file() and is_audio(p):
            yield p


def read_jsonl():
    if not JSONL_PATH.exists():
        return []
    events = []
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def append_event(event, record):
    """把一筆事件 append 到 catalog/tracks.jsonl。"""
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    ev = {"ts": now_iso(), "event": event}
    if isinstance(record, dict):
        ev.update(record)
    else:
        ev["track_id"] = record
    with JSONL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def load_sidecar(audio):
    p = sidecar_path(audio)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_sidecar(audio, data):
    p = sidecar_path(audio)
    data["updated_at"] = now_iso()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def upsert_track(audio, data, event="upsert"):
    """寫入 sidecar 並 append 事件。data 會被就地補上預設欄位。"""
    audio = Path(audio)
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("track_id", audio.stem)
    data.setdefault("file", relpath(audio))
    save_sidecar(audio, data)
    append_event(event, data)
    return data


def load_catalog():
    """回傳 {track_id: record}；以 sidecar 為準。"""
    catalog = {}
    for audio in iter_audio_files():
        sc = load_sidecar(audio)
        if sc:
            catalog[sc.get("track_id") or audio.stem] = sc
    return catalog


def style_version(name):
    """讀 prompts/styles/<name>.json 的 version 欄位（沒有則回 1；找不到風格回 None）。"""
    if not name:
        return None
    p = ROOT / "prompts" / "styles" / f"{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("version", 1)
    except (json.JSONDecodeError, OSError):
        return None


def reconstruct_index():
    """由 sidecar 重建 catalog/index.csv，回傳 (路徑, 曲數)。"""
    import csv

    catalog = load_catalog()
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "track_id", "file", "style", "style_version", "mood", "instruments",
        "key", "bpm", "rating", "status", "auto_verdict", "auto_score",
        "tags", "seed", "duration", "sha1", "used_count",
    ]
    with INDEX_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for tid, r in sorted(catalog.items()):
            aq = r.get("auto_qc") or {}
            rv = r.get("review") or {}
            gen = r.get("gen") or {}
            w.writerow({
                "track_id": tid,
                "file": r.get("file", ""),
                "style": r.get("style", ""),
                "style_version": r.get("style_version", ""),
                "mood": r.get("mood", ""),
                "instruments": r.get("instruments", ""),
                "key": r.get("key", ""),
                "bpm": r.get("bpm", ""),
                "rating": rv.get("rating", ""),
                "status": r.get("status", ""),
                "auto_verdict": aq.get("verdict", ""),
                "auto_score": aq.get("score", ""),
                "tags": "|".join(r.get("tags", [])),
                "seed": gen.get("seed", ""),
                "duration": gen.get("duration", ""),
                "sha1": r.get("sha1", ""),
                "used_count": len(r.get("usage") or []),
            })
    return INDEX_CSV, len(catalog)
