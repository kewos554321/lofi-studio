#!/usr/bin/env python3
"""
library.py — 音樂圖書館（SQLite）＋自動標籤 ＋ 大規模 QC 檢視。

以 catalog 的 sidecar 為來源，建出可秒查的 catalog/library.db：
  - 結構化自動標籤（style / key / bpm 級距 / energy / brightness / verdict / rating / duration）
  - 支援依 tag / style / status / verdict / 分數 / BPM 篩選（1000 首也秒查）
  - 產出 QC 總表、Keeper 候選、reject 原因排行

用法:
  python3 scripts/library.py build                      # 重建 DB（會自動補結構化標籤）
  python3 scripts/library.py query --tag style:rainy_lofi --verdict pass --limit 20
  python3 scripts/library.py query --status keep --json
  python3 scripts/library.py summary                    # QC/風格/標籤總表
  python3 scripts/library.py tag --auto                 # 自動標籤寫回 sidecar
  python3 scripts/library.py tag --add "moody,night" --style rainy_lofi
  python3 scripts/library.py set-status keep --verdict pass --min-score 90
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from lofi import catalog as cat

DB_PATH = cat.CATALOG_DIR / "library.db"

AUTO_PREFIXES = ("style:", "key:", "bpm:", "energy:", "brightness:",
                 "verdict:", "rating:", "duration:", "instrumental")


def slug(s):
    return "".join(c if c.isalnum() else "_" for c in str(s).lower()).strip("_")


def auto_tags(rec):
    """由 sidecar + auto_qc 推導結構化標籤。"""
    tags = set()
    if rec.get("style"):
        tags.add(f"style:{rec['style']}")
    if rec.get("key"):
        tags.add(f"key:{slug(rec['key'])}")
    try:
        bpm = float(rec.get("bpm"))
        tags.add("bpm:" + ("slow" if bpm < 70 else "mid" if bpm < 85 else "up"))
    except (TypeError, ValueError):
        pass
    aq = rec.get("auto_qc") or {}
    rms = aq.get("rms_db")
    if isinstance(rms, (int, float)):
        tags.add("energy:" + ("loud" if rms > -15 else "medium" if rms > -22 else "quiet"))
    hb = aq.get("high_band_db")
    if isinstance(hb, (int, float)):
        tags.add("brightness:" + ("bright" if hb > -18 else "neutral" if hb > -28 else "warm"))
    if aq.get("verdict"):
        tags.add(f"verdict:{aq['verdict']}")
    rv = rec.get("review") or {}
    if rv.get("rating"):
        tags.add(f"rating:{rv['rating']}")
    try:
        dur = float((rec.get("gen") or {}).get("duration") or aq.get("duration") or 0)
        if dur:
            tags.add("duration:" + ("short" if dur < 60 else "standard" if dur < 200 else "long"))
    except (TypeError, ValueError):
        pass
    p = (rec.get("prompt") or "").lower()
    if ("no vocals" in p) or ("instrumental" in p) or ("vocal" not in p):
        tags.add("instrumental")
    return tags


def all_tags(rec):
    stored = set(rec.get("tags") or [])
    return stored | auto_tags(rec)


# ----------------------- DB -----------------------

def connect():
    cat.CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS tracks (
        track_id TEXT PRIMARY KEY, file TEXT, style TEXT, style_version INT,
        mood TEXT, instruments TEXT, key TEXT, bpm REAL, rating INT, status TEXT,
        verdict TEXT, score INT, lufs REAL, true_peak REAL, bpm_est REAL,
        sha1 TEXT, seed INT, duration REAL, created_at TEXT, run_id TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS tags (track_id TEXT, tag TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tags ON tags(tag)")
    return con


def cmd_build(args):
    con = connect()
    con.execute("DELETE FROM tracks")
    con.execute("DELETE FROM tags")
    catalog = cat.load_catalog()
    for tid, r in catalog.items():
        aq = r.get("auto_qc") or {}
        rv = r.get("review") or {}
        gen = r.get("gen") or {}
        con.execute("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            tid, r.get("file"), r.get("style"), r.get("style_version"),
            r.get("mood"), r.get("instruments"), r.get("key"), r.get("bpm"),
            rv.get("rating"), r.get("status"), aq.get("verdict"), aq.get("score"),
            aq.get("lufs"), aq.get("true_peak"), aq.get("bpm_est"),
            r.get("sha1"), gen.get("seed"), gen.get("duration"), r.get("created_at"),
            r.get("run_id")))
        for t in all_tags(r):
            con.execute("INSERT INTO tags VALUES (?,?)", (tid, t))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    nt = con.execute("SELECT COUNT(DISTINCT tag) FROM tags").fetchone()[0]
    con.close()
    print(f"✅ 已重建 {cat.relpath(DB_PATH)}（{n} 首、{nt} 種標籤）")


def build_where(args):
    where, params = [], []
    for t in (args.tag or []):
        where.append("EXISTS (SELECT 1 FROM tags tg WHERE tg.track_id=t.track_id AND tg.tag=?)")
        params.append(t)
    for col, op, val in (
        ("style", "=", args.style), ("status", "=", args.status),
        ("verdict", "=", args.verdict),
    ):
        if val:
            where.append(f"t.{col} {op} ?")
            params.append(val)
    if args.min_score is not None:
        where.append("t.score >= ?"); params.append(args.min_score)
    if args.min_rating is not None:
        where.append("t.rating >= ?"); params.append(args.min_rating)
    if args.bpm_min is not None:
        where.append("t.bpm >= ?"); params.append(args.bpm_min)
    if args.bpm_max is not None:
        where.append("t.bpm <= ?"); params.append(args.bpm_max)
    return (" AND ".join(where) or "1=1"), params


def cmd_query(args):
    con = connect()
    where, params = build_where(args)
    sql = f"SELECT t.track_id,t.style,t.bpm,t.verdict,t.score,t.status,t.rating,t.mood FROM tracks t WHERE {where} ORDER BY t.score DESC"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = con.execute(sql, params).fetchall()
    con.close()
    if args.json:
        print(json.dumps([dict(zip(
            ["track_id", "style", "bpm", "verdict", "score", "status", "rating", "mood"], r)) for r in rows],
            ensure_ascii=False, indent=2))
        return
    print(f"符合 {len(rows)} 首")
    for r in rows:
        print(f"  {r[0]:<34} [{r[1] or '-':<14}] {str(r[2]):>4}bpm {str(r[3]):>4} score={r[4]}"
              f" {r[5]:<10} {r[7] or ''}")


def cmd_summary(args):
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    def rows(sql):
        return con.execute(sql).fetchall()
    print(f"◆ 總計 {total} 首")
    print("\n◆ 狀態"); 
    for s, n in rows("SELECT status,COUNT(*) FROM tracks GROUP BY status ORDER BY 2 DESC"):
        print(f"  {s or '-':<12} {n}")
    print("\n◆ 品檢結果")
    for v, n, avg in rows("SELECT verdict,COUNT(*),ROUND(AVG(score),1) FROM tracks GROUP BY verdict ORDER BY 2 DESC"):
        print(f"  {v or '-':<8} {n:>5}  平均分 {avg}")
    print("\n◆ 各風格（首數 / 淘汰率 / 平均分）")
    for st, n, fails, avg in rows(
        "SELECT style,COUNT(*),SUM(CASE WHEN verdict='fail' THEN 1 ELSE 0 END),ROUND(AVG(score),1) "
        "FROM tracks GROUP BY style ORDER BY 2 DESC"):
        rate = (fails / n * 100) if n else 0
        print(f"  {st or '-':<20} {n:>5}  淘汰 {rate:4.1f}%  平均 {avg}")
    print("\n◆ 最常出現的旗標（自動品檢）")
    # flags 不在 DB，改由 sidecar 統計
    from collections import Counter
    flags = Counter()
    for r in cat.load_catalog().values():
        for f in (r.get("auto_qc") or {}).get("flags", []):
            flags[f] += 1
    for f, n in flags.most_common(12):
        print(f"  {f:<22} {n}")
    print("\n◆ 標籤 Top 20")
    for t, n in rows("SELECT tag,COUNT(*) FROM tags GROUP BY tag ORDER BY 2 DESC LIMIT 20"):
        print(f"  {t:<24} {n}")
    con.close()


def track_paths():
    """回傳 {track_id: Path}（依 sidecar 的 track_id 為準）。"""
    m = {}
    for p in cat.iter_audio_files():
        sc = cat.load_sidecar(p)
        tid = (sc or {}).get("track_id") or p.stem
        m[tid] = p
    return m


def cmd_tag(args):
    con = connect()
    where, params = build_where(args)
    ids = [r[0] for r in con.execute(f"SELECT t.track_id FROM tracks t WHERE {where}", params)]
    con.close()
    if not ids:
        sys.exit("沒有符合的曲目")
    add = [t.strip() for t in (args.add or "").split(",") if t.strip()]
    remove = [t.strip() for t in (args.remove or "").split(",") if t.strip()]
    paths = track_paths()
    changed = 0
    for tid in ids:
        audio = paths.get(tid)
        if not audio:
            continue
        sc = cat.load_sidecar(audio) or {"track_id": tid, "file": cat.relpath(audio)}
        tags = set(sc.get("tags") or [])
        if args.auto:
            tags = {t for t in tags if not t.startswith(AUTO_PREFIXES)}
            tags |= auto_tags(sc)
        tags |= set(add)
        tags -= set(remove)
        sc["tags"] = sorted(tags)
        cat.upsert_track(audio, sc, event="tag")
        changed += 1
    print(f"✅ 已更新 {changed} 首標籤")
    cmd_build(args)


def cmd_set_status(args):
    con = connect()
    where, params = build_where(args)
    ids = set(r[0] for r in con.execute(f"SELECT t.track_id FROM tracks t WHERE {where}", params))
    con.close()
    paths = track_paths()
    changed = 0
    for tid in ids:
        audio = paths.get(tid)
        if not audio:
            continue
        sc = cat.load_sidecar(audio)
        if not sc:
            continue
        sc["status"] = args.status
        cat.upsert_track(audio, sc, event="set-status")
        changed += 1
    print(f"✅ 已把 {changed} 首設為 status={args.status}")
    cmd_build(args)


def add_filters(ap):
    ap.add_argument("--tag", action="append", default=[], help="可重複，例如 --tag style:rainy_lofi")
    ap.add_argument("--style")
    ap.add_argument("--status")
    ap.add_argument("--verdict")
    ap.add_argument("--min-score", type=float)
    ap.add_argument("--min-rating", type=int)
    ap.add_argument("--bpm-min", type=float)
    ap.add_argument("--bpm-max", type=float)


def main():
    ap = argparse.ArgumentParser(description="音樂圖書館 / 自動標籤 / QC 檢視")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build")

    q = sub.add_parser("query"); add_filters(q)
    q.add_argument("--limit", type=int, default=30); q.add_argument("--json", action="store_true")

    sub.add_parser("summary")

    t = sub.add_parser("tag"); add_filters(t)
    t.add_argument("--auto", action="store_true", help="重算結構化自動標籤")
    t.add_argument("--add", default="", help="加標籤（逗號分隔）")
    t.add_argument("--remove", default="", help="移除標籤（逗號分隔）")

    s = sub.add_parser("set-status"); add_filters(s)
    s.add_argument("status", help="keep / reject / hold / unreviewed")

    args = ap.parse_args()
    {"build": cmd_build, "query": cmd_query, "summary": cmd_summary,
     "tag": cmd_tag, "set-status": cmd_set_status}[args.cmd](args)


if __name__ == "__main__":
    main()
