#!/usr/bin/env python3
"""一次性：把 assets/visuals/ 的資料夾與檔名改成命名規則，並留下對照表。

規則（見 docs/VISUAL_NAMING.md）：
  資料夾 <kind><NN>              ref=定裝參考 / ds=訓練集 / sc=場景風格測試
  檔名   <run-id>_<NN>[_<slug>].<ext>
  衍生檔 slug 用保留字首 anim-（動畫）cmp-（對照合成）mask-（遮罩）fix-（後處理）

用法: PYTHONPATH=src python3 scripts/_tmp_rename_visuals.py [--dry-run]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lofi.visual import (VIS_DIR, MANIFEST_NAME, load_manifest,   # noqa: E402
                         write_manifest, backfill_manifest, output_filename,
                         sanitize_slug, read_png_workflow, manifest_from_workflow)

DRY = "--dry-run" in sys.argv
ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "VISUAL_NAMING.md"

# 舊資料夾 -> 新資料夾（ref=主角定裝參考候選、ds=LoRA 訓練集、sc=場景/風格測試）
DIR_MAP = {
    "mascot_z01": "ref01",   # 第一批吉祥物候選（橘貓：水彩/3D/機器貓）+ 眨眼實驗
    "mascot_z02": "ref02",   # 橘貓候選 v2
    "mascot_z03": "ref03",   # 黑貓候選（定案方向）
    "mascot_z04": "ref04",   # 黑貓特徵候選（金眼/白襪/綠圍巾/異色瞳）← 選定 gold-socks
    "mascot_z05": "ref05",   # 大眼版（重跑中，先前中斷）
    "mascot_ip01": "sc01",   # IPAdapter 鎖角色測試（橘貓）
    "mascot_ip02": "sc02",   # IPAdapter 鎖角色測試 v2（橘貓）
    "mip03": "sc03",         # IPAdapter 場景測試（黑貓）
    "mip04": "sc04",         # IPAdapter 白襪測試 w0.65
    "mip05": "sc05",         # IPAdapter 白襪測試 w0.60
    "lora_ds01": "ds01",     # LoRA 訓練集 v1（自 30 張留下 7 張）
    "lora_ds02": "ds02",     # LoRA 訓練集 v2（留下 6 張）
}

# 手動排定的檔名：new_dir -> [(舊檔名 stem, slug)]，順序就是 NN
FILE_PLAN = {
    "ref01": [
        ("ghibli_00001_", "watercolor-sit"),
        ("pixar_00001_", "3dtoon-sit"),
        ("robotcat_00001_", "robotcat-blue"),
        ("ghibli_cat_closed_00001_", "watercolor-eyes-closed"),
        ("ghibli_cat_shut_00001_", "watercolor-eyes-shut"),
        ("ghibli_cat_sleep_00001_", "watercolor-sleep"),
        ("ghibli_blink_closed_00001_", "watercolor-blink-closed"),
        ("_cmp_base_原圖(睜眼)", "cmp-eyes-open"),
        ("_cmp_sleep_重繪(閉眼)", "cmp-eyes-closed"),
        ("_cmp_composite_合成", "cmp-composite"),
        ("ghibli_blink", "anim-blink"),
    ],
    "ref02": [
        ("ghibli_v2_00002_", "watercolor-sleep"),
        ("pixar_v2_00001_", "3dtoon-sit"),
        ("robotcat_a_00001_", "robotcat-cream"),
        ("robotcat_b_00001_", "robotcat-grey"),
        ("robotcat_c_00001_", "robotcat-mint"),
    ],
    "ref03": [
        ("blackcat_bakery_00001_", "bakery"),
        ("blackcat_wind_00001_", "leaping-wind"),
        ("blackcat_window_00001_", "window"),
    ],
    "ref04": [
        ("gold_socks_00001_", "gold-socks"),        # ← 目前選定的定裝參考
        ("amber_chest_00001_", "amber-chest"),
        ("green_scarf_00001_", "green-scarf"),
        ("heterochromia_00001_", "heterochromia"),
    ],
    "sc01": [("scene_books_00001_", "books"),
             ("scene_forest_00001_", "forest"),
             ("scene_rain_00001_", "rain")],
    "sc02": [("scene_books_00002_", "books"),
             ("scene_forest_00002_", "forest"),
             ("scene_rain_00002_", "rain")],
    "sc03": [("cozy_desk_mip03_00001_", "desk"),
             ("rain_night_mip03_00001_", "rain-night"),
             ("autumn_path_mip03_00001_", "autumn-path")],
    "sc04": [("socks_desk_mip04_00001_", "desk-socks"),
             ("socks_forest_mip04_00001_", "forest-socks"),
             ("socks_rain_mip04_00001_", "rain-socks")],
    "sc05": [("w60_desk_mip05_00001_", "desk-w60"),
             ("w60_forest_mip05_00001_", "forest-w60"),
             ("w60_rain_mip05_00001_", "rain-w60")],
}

# 訓練集：slug 從 prompt 反推（姿勢-場景），順序照原 catNN
POSE_KEYS = [("sitting upright", "sit-front"), ("three-quarter", "sit-3q"),
             ("walking sideways", "walk-side"), ("lying down", "lie"),
             ("curled up asleep", "sleep"), ("stretching", "stretch"),
             ("looking up", "lookup"), ("close-up portrait", "face"),
             ("from behind", "back"), ("paw raised", "paw-up")]
SCENE_KEYS = [("windowsill", "windowsill"), ("wooden desk", "desk"),
              ("fireplace", "fireplace"), ("autumn forest path", "forest"),
              ("rainy window", "rain-window"), ("wicker basket", "basket"),
              ("grassy meadow", "meadow"), ("bookshelf", "bookshelf"),
              ("kitchen counter", "kitchen"), ("mossy stone wall", "garden")]

# 不能對外的字眼 -> 中性描述（檔名會進版控／可能被看到）
NEUTRAL = {"ghibli": "watercolor", "pixar": "3dtoon", "doraemon": "robotcat"}


# assets/visuals/ 根目錄的散落檔（早期還沒有一批一夾的產物）-> 收進 sc06
LOOSE_ID = "sc06"
LOOSE = [
    ("ghibli_orange_DreamShaperXL_00001_.png", "watercolor-orange"),
    ("ghibli_orange_DreamShaperXL_00002_.png", "watercolor-orange"),
    ("style_ukiyoe_styles01_00001_.png", "ukiyoe-style"),
    ("zgguf_cat_g01_00001_.png", "zimage-cat-desk"),
]


def slug_from_prompt(prompt):
    p = (prompt or "").lower()
    pose = next((s for k, s in POSE_KEYS if k in p), "")
    scene = next((s for k, s in SCENE_KEYS if k in p), "")
    return "-".join(x for x in (pose, scene) if x)


def plan_for(old_dir, new_id, names):
    """回傳 [(舊 stem, slug)]，順序＝NN。訓練集沒手寫計畫，slug 從 prompt 推。"""
    if new_id in FILE_PLAN:
        return FILE_PLAN[new_id]
    rows = []
    for f in sorted(names):
        if f.suffix.lower() != ".png":
            continue
        wf = read_png_workflow(old_dir / f)
        _, entry = manifest_from_workflow(wf) if wf else ({}, {})
        m = re.search(r"(\d+)", f.stem)
        rows.append((int(m.group(1)) if m else 0, f.stem, slug_from_prompt(entry.get("prompt", ""))))
    rows.sort()
    return [(stem, slug) for _, stem, slug in rows]


def fallback_slug(stem, old_name):
    s = re.sub(r"_?\d{4,}_?$", "", stem)                 # 去掉 ComfyUI 的 _00001_
    s = s.replace(old_name, "").strip("_-")
    for bad, good in NEUTRAL.items():
        s = s.replace(bad, good)
    return sanitize_slug(s) or "img"


def main():
    report = []
    changed = 0
    for old_name, new_id in DIR_MAP.items():
        old = VIS_DIR / old_name
        if not old.is_dir():
            continue                                             # 已改過或不存在
        changed += 1
        names = [f for f in sorted(old.iterdir())
                 if f.name not in (MANIFEST_NAME, ".DS_Store")]
        plan = plan_for(old, new_id, names)
        planned = {stem for stem, _ in plan}

        renames = []                                     # (舊檔, 新檔名)
        for i, (stem, slug) in enumerate(plan, start=1):
            for f in names:
                if f.stem == stem:
                    renames.append((f, output_filename(new_id, i, slug, f.suffix)))
        seq = len(plan) + 1
        for f in names:                                  # 沒排到的（臨時檔等）接在後面
            if f.stem not in planned:
                renames.append((f, output_filename(new_id, seq, fallback_slug(f.stem, old_name), f.suffix)))
                seq += 1

        print(f"\n### {old_name} -> {new_id}  ({len(renames)} 檔)")
        for f, new in renames:
            print(f"    {f.name:<44} -> {new}")

        row = {"new_id": new_id, "old_id": old_name,
               "pairs": [(f.name, new) for f, new in renames]}

        if DRY:
            report.append(row)
            continue

        # 1) 改檔名
        for f, new in renames:
            target = old / new
            if target.exists():
                sys.exit(f"❌ 目標已存在，中止：{target}")
            f.rename(target)
        for junk in old.glob(".DS_Store"):
            junk.unlink()

        # 2) 把 manifest 裡的舊檔名換成新檔名（紀錄不能斷）
        fmap = {f.name: new for f, new in renames}
        m = load_manifest(old)
        if m:
            for e in m.get("images", []):
                if e.get("file") in fmap:
                    e["file"] = fmap[e["file"]]
            m["run_id"] = new_id
            m["renamed_from"] = old_name
            write_manifest(old, m)

        # 3) 改資料夾名
        new_dir = VIS_DIR / new_id
        old.rename(new_dir)

        # 4) 有圖卻沒紀錄的（例如 IPAdapter 早期沒寫 manifest）從圖檔反推補回
        added = backfill_manifest(new_dir)

        # 5) 驗收：manifest 指的檔案都要在
        m2 = load_manifest(new_dir)
        row["manifest"] = [e["file"] for e in m2.get("images", [])]
        missing = [f for f in row["manifest"] if not (new_dir / f).exists()]
        row["missing"] = missing
        report.append(row)
        tag = f"manifest {len(row['manifest'])} 筆"
        if added:
            tag += f"（回填 {added}）"
        if missing:
            tag += f" ⚠️ 有 {len(missing)} 筆找不到檔：{missing}"
        print(f"    ✅ {tag}")

    # 收攏 assets/visuals/ 根目錄的散落檔（早期還沒有一批一夾）
    pairs = [(n, output_filename(LOOSE_ID, i, s, Path(n).suffix))
             for i, (n, s) in enumerate(LOOSE, start=1) if (VIS_DIR / n).is_file()]
    if pairs:
        changed += 1
        print(f"\n### （根目錄散落檔）-> {LOOSE_ID}  ({len(pairs)} 檔)")
        for old_name, new_name in pairs:
            print(f"    {old_name:<44} -> {new_name}")
        row = {"new_id": LOOSE_ID, "old_id": "(根目錄散落)", "pairs": pairs}
        if not DRY:
            new_dir = VIS_DIR / LOOSE_ID
            new_dir.mkdir(parents=True, exist_ok=True)
            for old_name, new_name in pairs:
                (VIS_DIR / old_name).rename(new_dir / new_name)
            backfill_manifest(new_dir)
            row["manifest"] = [e["file"] for e in load_manifest(new_dir).get("images", [])]
            print(f"    ✅ manifest {len(row['manifest'])} 筆")
        report.append(row)
    stray = sorted(f.name for f in VIS_DIR.iterdir()
                   if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if stray:
        print("\n⚠️  以下不是生成物、也不是一批一夾的產物，請人工確認（未動）：")
        for s in stray:
            print(f"    assets/visuals/{s}")

    # 對照表
    if not changed:
        print("\n沒有需要改的（都已符合規則），未動對照表。")
        return
    lines = ["# 視覺產物命名規則與對照表", "",
             "> 由 `scripts/_tmp_rename_visuals.py` 產生（一次性遷移）。", "",
             "## 規則", "",
             "```",
             "資料夾： <kind><NN>                    ref=主角定裝參考 / ds=LoRA 訓練集 / sc=場景風格測試",
             "檔名　： <run-id>_<NN>[_<slug>].<ext>   例 ds02_07_walk-side-fireplace.png",
             "```", "",
             "- **run-id 開頭**：同批排在一起，看名字就知道哪批。",
             "- **NN 兩位數**：照生成順序，一眼看出是第幾張。",
             "- **slug**：小寫英數短標籤，一眼看出內容。",
             "- **衍生檔 slug 保留字首**：`anim-`（動畫）、`cmp-`（對照合成）、`mask-`（遮罩）、`fix-`（後處理）。",
             "- 未給 `--run-id` 時工具會自動編號（`ref05` 之後給 `ref06`）。", "",
             "## 對照表", "",
             "| 新資料夾 | 舊資料夾 | 新檔名 | 舊檔名 |",
             "|---|---|---|---|"]
    for row in report:
        for old_file, new_file in row["pairs"]:
            lines.append(f"| `{row['new_id']}/` | `{row['old_id']}/` | `{new_file}` | `{old_file}` |")
    lines += ["", "## 選定", "",
              "- 目前的主角定裝參考：`ref04/ref04_01_gold-socks.png`",
              "  （金眼黑貓＋白襪；另複製一份到 `~/ComfyUI/input/mascot_blackcat_ref.png` 供 IPAdapter 使用）",
              "- `ref05/` 是「眼睛偏大」版，尚未產出。",
              "",
              "## 命名後各 run 的用途", ""]
    for row in report:
        n = len(row.get("manifest", []))
        lines.append(f"- `{row['new_id']}/`（舊 `{row['old_id']}`）"
                     + (f"：manifest {n} 筆" if row.get("manifest") else ""))
    lines.append("")
    if not DRY:
        DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n對照表寫入 {DOC}")
    else:
        print("\n（dry-run，未實際改動）")


if __name__ == "__main__":
    main()
