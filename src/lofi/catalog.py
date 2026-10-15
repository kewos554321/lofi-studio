#!/usr/bin/env python3
"""
catalog.py — 音樂目錄（catalog）共用工具。

設計：
  - sidecar：assets/tracks/<file>.json，記錄**單曲目前的完整狀態**（唯一真相）。
  - 事件流：catalog/tracks.jsonl，append-only，記錄每次生成 / 品檢 / 評分 / 標籤 / 使用。
  - 索引：catalog/index.csv，由 sidecar 掃描重建，方便用試算表看。

會被 generate / qc / backfill 等共用。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from lofi import io
from lofi.paths import ROOT, TRACKS_DIR, CATALOG_DIR, MASTERS_DIR

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
    """遞迴走訪 assets/tracks 下的所有音檔（支援 <style>/<run_id>/ 分層）。"""
    if not TRACKS_DIR.exists():
        return
    for p in sorted(TRACKS_DIR.rglob("*")):
        if p.is_file() and is_audio(p):
            yield p


def subdir(root, style, run_id):
    """在 root 下組出 <style>/<run_id>/（風格與 run 都做檔名清洗）。"""
    def clean(v, default):
        v = (v or "").strip().replace(" ", "_").replace("/", "_")
        return v or default
    return Path(root) / clean(style, "misc") / clean(run_id, "legacy")


def track_subdir(style, run_id):
    """回傳某風格/批次的存放子目錄：assets/tracks/<style>/<run_id>/。"""
    return subdir(TRACKS_DIR, style, run_id)


def masters_subdir(style, run_id):
    """回傳某風格/批次的**無損母帶**子目錄：output/masters/<style>/<run_id>/（外接碟）。

    為什麼分兩層：FLAC 母帶是同長度 mp3 的 ~8 倍大（48kHz 立體 ≈30MB/首），
    全放內接碟（228GB）會吃爆；內接只留工作用的 mp3，母帶放外接。
    見 docs/DISTRIBUTION.md §6-3。
    """
    return subdir(MASTERS_DIR, style, run_id)


LOSSLESS_EXTS = (".flac", ".wav", ".aiff", ".aif")   # 無損母帶可接受的容器


def lossless_master(audio, sidecar=None, style=None, run_id=None):
    """回傳這首的無損母帶路徑（output/masters/...）；沒有回 None。

    優先序：sidecar 的 `master_file`（生成時寫入）→ 母帶夾內同名 stem 的無損檔。
    給「串影片優先吃無損母帶」（`library query --lossless`）與 release 共用，
    找不到時呼叫端自行退回工作檔（下游 fail-soft，不新增失敗模式）。
    """
    sc = sidecar if sidecar is not None else (load_sidecar(audio) or {})
    mf = sc.get("master_file")
    if mf:
        p = ROOT / mf
        if p.exists():
            return p
    d = masters_subdir(sc.get("style") or style, sc.get("run_id") or run_id)
    for ext in LOSSLESS_EXTS:
        p = d / f"{Path(audio).stem}{ext}"
        if p.exists():
            return p
    return None


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


def summarize_event(event, record):
    """事件流只記「發生什麼」，不記整包快照（避免 tracks.jsonl 單調膨脹）。

    完整狀態在 sidecar（唯一真相）；事件只需可追溯的關鍵欄位，
    `fingerprint` 等大欄位不進事件流。空值會被省略讓每行更短。
    """
    d = record if isinstance(record, dict) else {"track_id": record}
    out = {"track_id": d.get("track_id"), "file": d.get("file")}
    if event == "auto_qc":
        aq = d.get("auto_qc") or {}
        out.update({k: aq.get(k) for k in
                    ("verdict", "score", "flags", "lufs", "true_peak", "bpm_est", "duration")})
    elif event == "tag":
        out["tags"] = d.get("tags")
    elif event == "set-status":
        out["status"] = d.get("status")
    elif event == "review":
        rv = d.get("review") or {}
        out.update({"rating": rv.get("rating"), "note": rv.get("note"),
                    "status": d.get("status")})
    else:  # generated / backfill / upsert / 其他
        out.update({k: d.get(k) for k in
                    ("style", "style_version", "mood", "row_id", "run_id",
                     "source", "source_csv", "sha1", "prompt", "negative", "bpm", "key")})
        gen = d.get("gen") or {}
        out.update({k: gen.get(k) for k in ("seed", "model", "duration")})
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def append_event(event, record):
    """把一筆（精簡的）事件 append 到 catalog/tracks.jsonl（含檔案鎖，append-only）。"""
    ev = {"ts": now_iso(), "schema_version": SCHEMA_VERSION, "event": event}
    ev.update(summarize_event(event, record))
    io.append_jsonl(JSONL_PATH, ev)
    return ev


def load_sidecar(audio):
    p = sidecar_path(audio)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 壞檔先隔離保留，避免被當新檔覆蓋掉半壞的內容
        io.quarantine(p, why="sidecar JSON 壞損")
        return None


def save_sidecar(audio, data):
    p = sidecar_path(audio)
    data["updated_at"] = now_iso()
    io.atomic_write_json(p, data)
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
    import io as _io

    catalog = load_catalog()
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "track_id", "file", "style", "style_version", "mood", "instruments",
        "key", "bpm", "rating", "status", "auto_verdict", "auto_score",
        "tags", "seed", "duration", "sha1", "used_count",
    ]
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
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
    io.atomic_write_text(INDEX_CSV, buf.getvalue())
    return INDEX_CSV, len(catalog)
