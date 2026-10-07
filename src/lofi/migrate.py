#!/usr/bin/env python3
"""
migrate.py — 把 assets/tracks 舊的扁平檔案搬到 <style>/<run_id>/ 分層。

- 預設只 dry-run（印出計畫）。
- --apply 才真的搬；會寫一份 manifest（catalog/migrate_layout_<時間>.json）。
- --revert MANIFEST 可搬回原狀（音檔被 gitignore，這是主要的還原手段）。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from lofi import catalog as cat


def plan_moves():
    moves = []
    for audio in cat.iter_audio_files():
        sc = cat.load_sidecar(audio) or {}
        dest = cat.track_subdir(sc.get("style"), sc.get("run_id")) / audio.name
        if dest.resolve() == audio.resolve():
            continue
        moves.append((audio, dest, sc))
    return moves


def _move_sidecar(src, dest):
    sj, dj = cat.sidecar_path(src), cat.sidecar_path(dest)
    if not sj.exists():
        return
    data = json.loads(sj.read_text(encoding="utf-8"))
    data["file"] = cat.relpath(dest)
    dj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sj.unlink()


def cmd_apply(moves):
    manifest = {"created": cat.now_iso(), "moves": []}
    for src, dest, _ in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        _move_sidecar(src, dest)
        manifest["moves"].append([cat.relpath(src), cat.relpath(dest)])
    mpath = cat.CATALOG_DIR / f"migrate_layout_{cat.now_iso().replace(':', '').replace('+', '_')}.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mpath, len(moves)


def cmd_revert(manifest_path):
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    n = 0
    for src_rel, dest_rel in reversed(m["moves"]):
        src, dest = cat.ROOT / src_rel, cat.ROOT / dest_rel
        if not dest.exists():
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(src))
        _move_sidecar(dest, src)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="遷移 assets/tracks 到 <style>/<run_id>/ 分層")
    ap.add_argument("--apply", action="store_true", help="真的搬移（預設只 dry-run）")
    ap.add_argument("--revert", metavar="MANIFEST", help="依 manifest 搬回原狀")
    args = ap.parse_args()

    if args.revert:
        n = cmd_revert(args.revert)
        print(f"✅ 已還原 {n} 首")
        cat.reconstruct_index()
        return

    moves = plan_moves()
    if not moves:
        print("✅ 沒有需要搬移的檔案（可能已分層）")
        return

    print(f"將搬移 {len(moves)} 首到 <style>/<run_id>/：")
    for src, dest, _ in moves[:30]:
        print(f"  {cat.relpath(src)}\n     -> {cat.relpath(dest)}")
    if len(moves) > 30:
        print(f"  …另 {len(moves) - 30} 首")

    if not args.apply:
        print("\n（dry-run；確認後加 --apply 執行）")
        return

    mpath, n = cmd_apply(moves)
    print(f"\n✅ 已搬移 {n} 首")
    print(f"   manifest（可 --revert 還原）: {cat.relpath(mpath)}")
    idx, cnt = cat.reconstruct_index()
    print(f"   已重建索引: {cat.relpath(idx)}（{cnt} 首）")
    print("   建議再跑: python3 scripts/library.py build")


if __name__ == "__main__":
    main()
