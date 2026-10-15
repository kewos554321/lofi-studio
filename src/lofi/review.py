#!/usr/bin/env python3
"""review.py — 人工複審：把「聽感評分」寫進 sidecar，讓 keeper 篩得出來。

qc 只看客觀量測（響度/削波/時長…）；「這首好不好聽」只有人判得出來。
評分寫進 sidecar 的 `review`（{rating, note, reviewed_at}），library 會把它
轉成 `rating:N` 標籤，之後就能：

  lofi library set-status keep --min-rating 4     # 留 4 星以上
  lofi library query --style nujabes_night --status keep --paths

用法:
  python3 scripts/review.py FILE [FILE ...] --rating 5 [--note "sax 最像"]
  python3 scripts/review.py assets/tracks/<style>/<run>/ --rating 4    # 整個資料夾
  python3 scripts/review.py FILE --status keep                          # 只改狀態
  python3 scripts/review.py FILE --clear                                # 清掉評分
  python3 scripts/review.py --list                                      # 看哪些還沒評

沒有 sidecar 的檔案不予評分（先跑 `lofi qc` 或 `lofi backfill`）。
"""
import argparse
import sys
from pathlib import Path

from lofi import catalog as cat

RATING_MIN, RATING_MAX = 1, 5
STATUSES = ["keep", "reject", "hold", "unreviewed"]


def expand_targets(paths):
    """把檔案／資料夾展開成音檔清單（資料夾遞迴取音檔）。"""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*")) if f.is_file() and cat.is_audio(f)]
        else:
            out.append(p)
    return out


def review_file(audio, rating=None, note=None, status=None, clear=False):
    """寫入一筆人工複審到 sidecar + 事件流。回傳 sidecar dict。"""
    audio = Path(audio)
    sc = cat.load_sidecar(audio)
    if not sc:
        sys.exit(f"找不到 sidecar（先跑 `lofi qc` 建檔）：{audio}")
    if clear:
        sc.pop("review", None)
    else:
        rv = dict(sc.get("review") or {})
        if rating is not None:
            rv["rating"] = int(rating)
        if note is not None:
            rv["note"] = note
        rv["reviewed_at"] = cat.now_iso()
        sc["review"] = rv
    if status:
        sc["status"] = status
    cat.upsert_track(audio, sc, event="review")
    return sc


def cmd_list():
    rows = []
    for p in cat.iter_audio_files():
        sc = cat.load_sidecar(p) or {}
        rv = sc.get("review") or {}
        rows.append((sc.get("track_id") or p.stem, p, rv.get("rating"),
                     sc.get("status", ""), rv.get("note", "")))
    # 未評分排前面（＝還要做的事），再依 track_id
    rows.sort(key=lambda r: (r[2] is not None, r[0]))
    n_un = sum(1 for r in rows if r[2] is None)
    print(f"共 {len(rows)} 首｜未評分 {n_un} 首\n")
    for tid, p, rating, status, note in rows:
        star = f"{rating}★" if rating else "—"
        extra = f"  {note}" if note else ""
        print(f"  {star:>3}  {status or '-':<10}  {cat.relpath(p)}{extra}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="人工複審：把聽感評分寫進 sidecar（keeper 篩選用）")
    ap.add_argument("files", nargs="*", help="音檔或資料夾（資料夾會遞迴取音檔）")
    ap.add_argument("--rating", type=int, choices=range(RATING_MIN, RATING_MAX + 1),
                    metavar=f"{RATING_MIN}-{RATING_MAX}", help="聽感評分 1–5")
    ap.add_argument("--note", help="簡短備註（為何留／為何丟）")
    ap.add_argument("--status", choices=STATUSES, help="同時設定狀態")
    ap.add_argument("--clear", action="store_true", help="清掉這首的評分")
    ap.add_argument("--list", action="store_true", help="列出曲目與目前評分（未評分優先）")
    args = ap.parse_args()

    if args.list:
        sys.exit(cmd_list())

    targets = expand_targets(args.files)
    if not targets:
        sys.exit("請指定音檔或資料夾（或用 --list 看目前狀態）")
    if not args.clear and args.rating is None and not args.status:
        sys.exit("至少給 --rating 或 --status（要清掉評分用 --clear）")

    ok = 0
    for f in targets:
        if not Path(f).exists():
            print(f"  ⚠️  找不到檔案，略過：{f}")
            continue
        sc = review_file(f, rating=args.rating, note=args.note,
                         status=args.status, clear=args.clear)
        rv = sc.get("review") or {}
        show = f"{rv.get('rating','—')}★" if rv else "（無評分）"
        print(f"  ✅ {Path(f).name}  →  {show}"
              + (f"  status={sc.get('status')}" if args.status else ""))
        ok += 1

    print(f"\n完成 {ok} 首。下一步：lofi library build && lofi library summary")


if __name__ == "__main__":
    main()
