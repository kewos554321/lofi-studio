# 命名規則（Naming）

產物分成兩大類：**音樂（tracks）** 與 **視覺（visuals）**。共同原則是「**身份唯一、可排序、好讀、不編可變狀態**」。

## 通用原則

1. **ASCII、小寫、無空白**。
2. **分隔符不可出現在欄位內**（若用 `_` 分隔，欄位本身就不能含 `_`——這點音樂現況有瑕疵，見下）。
3. **零填滿、固定寬度**的序號（可字串排序）。
4. **批次前綴（run-id）**：glob 時同批排在一起，看名知批。
5. **人看的標籤（slug）獨立、可選**，`-` 連接，**不當身份**。
6. **衍生檔用保留字首**：`anim-`（動畫）/`cmp-`（對照合成）/`mask-`（遮罩）/`fix-`（後處理）。
7. **檔名不放可變狀態**（status、rating、"final"、一直 +1 的 v2…）。
8. **內容雜湊（sha1）放 metadata**，不放檔名。
9. **副檔名小寫**、與內容一致。
10. **身份與檔名脫鉤**：真正的 ID 記在 sidecar/manifest，檔名只是「方便路徑」。

---

## 音樂（tracks）

### 位置
```
assets/tracks/<style>/<run>/<檔名>.mp3
assets/tracks/<style>/<run>/<檔名>.mp3.json   # sidecar（唯一真相）
```

### 目前檔名（跨時期，兩種都有）
```
rainy_lofi/legacy/rainy_lofi_01_00001.mp3                          # <style>_<row:2>_<seq:5>
coastal_guitar/coastal01/coastal_guitar_01_coastal01_00002.mp3     # <style>_<row:2>_<run>_<seq:5>
```

**已知瑕疵**（新 run 應避免）：
- **分隔符衝突**：`_` 同時當分隔與出現在 `style`（`rainy_lofi`、`cozy_morning_v2`、`rnb_soul`）→ 無法可靠拆欄。
- **重複**：`style` 在目錄也在檔名；`run`（`coastal01`）在目錄**又**在檔名。
- **兩個像的數字**：`_01_`（CSV row）vs `_00002`（ComfyUI 序號）。
- **跨時期不一致**（有 run／沒 run），程式需特判。

### 標準（建議；套用於**新 run**，舊檔不動）
```
assets/tracks/<style>/<run>/<run-id>_<NNNN>[_<slug>].mp3
```
- `<run-id>`：批次碼，**不可變**（例 `coastal01`）。
- `<NNNN>`：該批第幾首，**固定 4 位**零填滿（可排序）。
- `<slug>`：可選短標籤，kebab-case（例 `seaside-afterglow`）。
- `row_id`／`prompt`／`seed` 等一律放 **sidecar**，不塞檔名。
- 例：`coastal_guitar/coastal01/coastal01_0001_seaside-afterglow.mp3`

### 身份與 ID（重要）
`track_id` 現在 = 檔名 stem。**標準做法**：`track_id` 明確記在 sidecar（=`<run-id>_<NNNN>`），**不要從檔名推算**——否則改名會打斷 `publish` 章節、`usage`、git 對應。

---

## 視覺（visuals）

### 位置
```
assets/visuals/<kind><NN>/<檔名>.<ext>
assets/visuals/<kind><NN>/manifest.json      # 一 run 一份（config 共用 + images 逐檔 prompt/seed）
```

### 規則（現行）
```
資料夾： <kind><NN>                     ref=主角定裝參考 / ds=LoRA 訓練集 / sc=場景風格測試
檔名　： <run-id>_<NN>[_<slug>].<ext>     例 ds02_07_walk-side-fireplace.png
```
- **run-id 開頭**：同批排在一起，看名知批。
- **NN 兩位數**：照生成順序。
- **slug**：小寫英數短標籤（`-` 連接），一眼看出內容。
- **衍生檔保留字首**：`anim-`／`cmp-`／`mask-`／`fix-`。
- 未給 `--run-id` 時工具自動編號（`ref05` 之後給 `ref06`）。
- 例外：五段產線（`make_episode.sh` / `make_long_lofi.sh`）在 `--episode` 有值時，**直接用 episode 名當 run-id**（例 `assets/visuals/rl01/`），一集對一集。

### 標準補強（建議）
- **序號位數**：`digits` 目前 2，一個 run 超過 99 張時字串排序會錯（`100` < `98`）→ 新 run 建議改 **3 或 4 位**。
- 其餘（run-id 前綴、kebab slug、衍生保留字首）**已符合標準**。

---

## 歷史遷移對照（已套用，2026-10；保留備查）

> 由一次性遷移腳本產生（已移至 `scratch/`，不進版控）。下列對照僅為歷史紀錄。

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
- `ref05/` 是「眼睛偏大」版。

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
