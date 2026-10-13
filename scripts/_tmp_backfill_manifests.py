#!/usr/bin/env python3
"""一次性：整理 assets/visuals/ 下所有 run 的 manifest。

- 舊的扁平 manifest -> 升級成 v2（config 共用 + images 逐檔）
- 有圖卻沒 manifest 的 run -> 從 PNG 內嵌的 workflow 反推補回
用法: PYTHONPATH=src python3 scripts/_tmp_backfill_manifests.py [--dry-run]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lofi.visual import (VIS_DIR, MANIFEST_NAME, load_manifest,   # noqa: E402
                         update_manifest, backfill_manifest)

dry = "--dry-run" in sys.argv


def main():
    rows = []
    for d in sorted(VIS_DIR.iterdir()):
        if not d.is_dir():
            continue
        pngs = sorted(d.glob("*.png"))
        existed = (d / MANIFEST_NAME).exists()
        if dry:
            m = load_manifest(d)
            filled = len(m.get("images", []))
            upgraded = existed and m.get("manifest_version") == 2
        else:
            filled = backfill_manifest(d) if pngs else 0
            if not pngs:
                update_manifest(d, run_id=d.name)      # 只把舊格式升級寫回
            m = load_manifest(d)
            upgraded = existed
        cfg = m.get("config", {})
        model = cfg.get("ckpt") or cfg.get("unet") or "-"
        rows.append((d.name, m.get("tool", "-"), model,
                     len(m.get("images", [])), len(pngs),
                     "有" if upgraded else "**新補**", filled))
    print(f"{'run-id':<16}{'tool':<20}{'模型':<34}{'manifest圖數':>8}{'實際png':>8}  {'更新':<8}")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<16}{r[1]:<20}{str(r[2])[:32]:<34}{r[3]:>8}{r[4]:>8}  {r[5]:<8}")


if __name__ == "__main__":
    main()
