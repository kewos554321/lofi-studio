# 視覺產物命名規則與對照表

> 由 `scripts/_tmp_rename_visuals.py` 產生（一次性遷移）。

## 規則

```
資料夾： <kind><NN>                    ref=主角定裝參考 / ds=LoRA 訓練集 / sc=場景風格測試
檔名　： <run-id>_<NN>[_<slug>].<ext>   例 ds02_07_walk-side-fireplace.png
```

- **run-id 開頭**：同批排在一起，看名字就知道哪批。
- **NN 兩位數**：照生成順序，一眼看出是第幾張。
- **slug**：小寫英數短標籤，一眼看出內容。
- **衍生檔 slug 保留字首**：`anim-`（動畫）、`cmp-`（對照合成）、`mask-`（遮罩）、`fix-`（後處理）。
- 未給 `--run-id` 時工具會自動編號（`ref05` 之後給 `ref06`）。
- 例外：五段產線（`make_episode.sh` / `make_long_lofi.sh`）在 `--episode` 有值時，會**直接用 episode 名當 run-id**（例 `assets/visuals/rl01/`），方便一集對一集。

## 對照表

| 新資料夾 | 舊資料夾 | 新檔名 | 舊檔名 |
|---|---|---|---|
| `ref01/` | `mascot_z01/` | `ref01_01_watercolor-sit.png` | `ghibli_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_02_3dtoon-sit.png` | `pixar_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_03_robotcat-blue.png` | `robotcat_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_04_watercolor-eyes-closed.png` | `ghibli_cat_closed_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_05_watercolor-eyes-shut.png` | `ghibli_cat_shut_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_06_watercolor-sleep.png` | `ghibli_cat_sleep_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_07_watercolor-blink-closed.png` | `ghibli_blink_closed_00001_.png` |
| `ref01/` | `mascot_z01/` | `ref01_08_cmp-eyes-open.png` | `_cmp_base_原圖(睜眼).png` |
| `ref01/` | `mascot_z01/` | `ref01_09_cmp-eyes-closed.png` | `_cmp_sleep_重繪(閉眼).png` |
| `ref01/` | `mascot_z01/` | `ref01_10_cmp-composite.png` | `_cmp_composite_合成.png` |
| `ref01/` | `mascot_z01/` | `ref01_11_anim-blink.gif` | `ghibli_blink.gif` |
| `ref01/` | `mascot_z01/` | `ref01_11_anim-blink.mp4` | `ghibli_blink.mp4` |
| `ref02/` | `mascot_z02/` | `ref02_01_watercolor-sleep.png` | `ghibli_v2_00002_.png` |
| `ref02/` | `mascot_z02/` | `ref02_02_3dtoon-sit.png` | `pixar_v2_00001_.png` |
| `ref02/` | `mascot_z02/` | `ref02_03_robotcat-cream.png` | `robotcat_a_00001_.png` |
| `ref02/` | `mascot_z02/` | `ref02_04_robotcat-grey.png` | `robotcat_b_00001_.png` |
| `ref02/` | `mascot_z02/` | `ref02_05_robotcat-mint.png` | `robotcat_c_00001_.png` |
| `ref03/` | `mascot_z03/` | `ref03_01_bakery.png` | `blackcat_bakery_00001_.png` |
| `ref03/` | `mascot_z03/` | `ref03_02_leaping-wind.png` | `blackcat_wind_00001_.png` |
| `ref03/` | `mascot_z03/` | `ref03_03_window.png` | `blackcat_window_00001_.png` |
| `ref04/` | `mascot_z04/` | `ref04_01_gold-socks.png` | `gold_socks_00001_.png` |
| `ref04/` | `mascot_z04/` | `ref04_02_amber-chest.png` | `amber_chest_00001_.png` |
| `ref04/` | `mascot_z04/` | `ref04_03_green-scarf.png` | `green_scarf_00001_.png` |
| `ref04/` | `mascot_z04/` | `ref04_04_heterochromia.png` | `heterochromia_00001_.png` |
| `sc01/` | `mascot_ip01/` | `sc01_01_books.png` | `scene_books_00001_.png` |
| `sc01/` | `mascot_ip01/` | `sc01_02_forest.png` | `scene_forest_00001_.png` |
| `sc01/` | `mascot_ip01/` | `sc01_03_rain.png` | `scene_rain_00001_.png` |
| `sc02/` | `mascot_ip02/` | `sc02_01_books.png` | `scene_books_00002_.png` |
| `sc02/` | `mascot_ip02/` | `sc02_02_forest.png` | `scene_forest_00002_.png` |
| `sc02/` | `mascot_ip02/` | `sc02_03_rain.png` | `scene_rain_00002_.png` |
| `sc03/` | `mip03/` | `sc03_01_desk.png` | `cozy_desk_mip03_00001_.png` |
| `sc03/` | `mip03/` | `sc03_02_rain-night.png` | `rain_night_mip03_00001_.png` |
| `sc03/` | `mip03/` | `sc03_03_autumn-path.png` | `autumn_path_mip03_00001_.png` |
| `sc04/` | `mip04/` | `sc04_01_desk-socks.png` | `socks_desk_mip04_00001_.png` |
| `sc04/` | `mip04/` | `sc04_02_forest-socks.png` | `socks_forest_mip04_00001_.png` |
| `sc04/` | `mip04/` | `sc04_03_rain-socks.png` | `socks_rain_mip04_00001_.png` |
| `sc05/` | `mip05/` | `sc05_01_desk-w60.png` | `w60_desk_mip05_00001_.png` |
| `sc05/` | `mip05/` | `sc05_02_forest-w60.png` | `w60_forest_mip05_00001_.png` |
| `sc05/` | `mip05/` | `sc05_03_rain-w60.png` | `w60_rain_mip05_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_01_walk-side-fireplace.png` | `cat08_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_01_walk-side-fireplace.txt` | `cat08_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_02_sleep-rain-window.png` | `cat14_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_02_sleep-rain-window.txt` | `cat14_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_03_sleep-rain-window.png` | `cat15_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_03_sleep-rain-window.txt` | `cat15_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_04_stretch-basket.png` | `cat17_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_04_stretch-basket.txt` | `cat17_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_05_stretch-basket.png` | `cat18_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_05_stretch-basket.txt` | `cat18_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_06_face-bookshelf.png` | `cat22_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_06_face-bookshelf.txt` | `cat22_lora_ds01_00001_.txt` |
| `ds01/` | `lora_ds01/` | `ds01_07_paw-up-garden.png` | `cat30_lora_ds01_00001_.png` |
| `ds01/` | `lora_ds01/` | `ds01_07_paw-up-garden.txt` | `cat30_lora_ds01_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_01_sit-front-forest.png` | `cat02_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_01_sit-front-forest.txt` | `cat02_lora_ds02_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_02_sit-front-bookshelf.png` | `cat03_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_02_sit-front-bookshelf.txt` | `cat03_lora_ds02_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_03_lie-forest.png` | `cat10_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_03_lie-forest.txt` | `cat10_lora_ds02_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_04_lie-meadow.png` | `cat11_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_04_lie-meadow.txt` | `cat11_lora_ds02_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_05_sleep-rain-window.png` | `cat13_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_05_sleep-rain-window.txt` | `cat13_lora_ds02_00001_.txt` |
| `ds02/` | `lora_ds02/` | `ds02_06_sleep-desk.png` | `cat15_lora_ds02_00001_.png` |
| `ds02/` | `lora_ds02/` | `ds02_06_sleep-desk.txt` | `cat15_lora_ds02_00001_.txt` |
| `sc06/` | `(根目錄散落)/` | `sc06_01_watercolor-orange.png` | `ghibli_orange_DreamShaperXL_00001_.png` |
| `sc06/` | `(根目錄散落)/` | `sc06_02_watercolor-orange.png` | `ghibli_orange_DreamShaperXL_00002_.png` |
| `sc06/` | `(根目錄散落)/` | `sc06_03_ukiyoe-style.png` | `style_ukiyoe_styles01_00001_.png` |
| `sc06/` | `(根目錄散落)/` | `sc06_04_zimage-cat-desk.png` | `zgguf_cat_g01_00001_.png` |

## 選定

- 目前的主角定裝參考：`ref04/ref04_01_gold-socks.png`
  （金眼黑貓＋白襪；另複製一份到 `~/ComfyUI/input/mascot_blackcat_ref.png` 供 IPAdapter 使用）
- `ref05/` 是「眼睛偏大」版，尚未產出。

## 命名後各 run 的用途

- `ref01/`（舊 `mascot_z01`）：manifest 6 筆
- `ref02/`（舊 `mascot_z02`）：manifest 5 筆
- `ref03/`（舊 `mascot_z03`）：manifest 3 筆
- `ref04/`（舊 `mascot_z04`）：manifest 4 筆
- `ref05/`（舊 `mascot_z05`）
- `sc01/`（舊 `mascot_ip01`）：manifest 3 筆
- `sc02/`（舊 `mascot_ip02`）：manifest 3 筆
- `sc03/`（舊 `mip03`）：manifest 3 筆
- `sc04/`（舊 `mip04`）：manifest 3 筆
- `sc05/`（舊 `mip05`）：manifest 3 筆
- `ds01/`（舊 `lora_ds01`）：manifest 7 筆
- `ds02/`（舊 `lora_ds02`）：manifest 6 筆
- `sc06/`（舊 `(根目錄散落)`）：manifest 4 筆

