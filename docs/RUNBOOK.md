# Lofi 製作 RUNBOOK

完整操作手冊。P1（自動化管線）已可執行；P2（AI 生成）需另外安裝並用 GUI 操作。
> 這份文件是把你提供的 SOP 整理、補齊缺漏後的版本。標記 **[待確認]** 的地方請以官方文件為準。

> 程式結構：Python 邏輯在 `src/lofi/`（單一入口 `lofi <command>`）；`scripts/*.py` 為相容 shim；ffmpeg/ComfyUI 重流程仍為 `scripts/*.sh`。詳見 `AGENTS.md` 與 `README.md`。
> 量產用的成品配置（錨定參數、影片長度／視覺／上片配方）見 [`RECIPES.md`](RECIPES.md)。

---

## 0. 前提與合規

| 項目 | 重點 |
|---|---|
| 模型授權 | 本流程以 **ACE-Step** 為主。官方標示之授權請於使用前再次確認；保留來源紀錄。 **[待確認]** |
| 可替代 | Stable Audio Open Small / Stable Audio 3.0（Small）採 Stability AI Community License，年營收 < 1M 美元可商用，需註冊。 |
| 不要用 | MusicGen（CC-BY-NC 非商用）、YuE / 記憶體需求過大的 XL / Large 版本。 |
| YouTube 政策 | 2025/7 起「inauthentic content」以**頻道層級**偵測，打擊模板化量產。上傳要勾 **AI 揭露**，視覺要原創，每支片要有實質變化與策展痕跡。 |
| 本機建議 | 生成時關掉瀏覽器、OBS 等吃資源的 App，讓 CPU/GPU/記憶體盡量留給模型。 |

---

## 1. 安裝 ComfyUI（P2）

三種方式擇一：

**選項 A：ComfyUI Manager 一鍵腳本**
1. 安裝 ComfyUI（官方安裝器或 git）。
2. 用 ComfyUI Manager 搜尋並安裝 `ComfyUI-ACE-Step` 節點。

**選項 B：手動安裝（可控性高）**
```bash
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# 啟動（MPS 加速 + 對不支援的運算子回退 CPU）
PYTORCH_ENABLE_MPS_FALLBACK=1 python main.py --force-fp16
```
預設開啟 `http://127.0.0.1:8188`。

**選項 C：ComfyUI Desktop**（官方桌面版，介面最友善）。

### 1.5 安裝 ACE-Step 1.5 模型（ComfyUI 內建，不需自訂節點）

ComfyUI 已**原生支援** ACE-Step 1.5。本專案用分檔版（約 10GB，含兩個 text encoder）：

```bash
cd ai-music/lofi-studio
./scripts/download_p2_models.sh --ace
```

檔案放到 `~/ComfyUI/models/`：

```
📂 models/
├── diffusion_models/acestep_v1.5_turbo.safetensors   (4.79 GB)
├── text_encoders/qwen_0.6b_ace15.safetensors          (1.19 GB)
├── text_encoders/qwen_1.7b_ace15.safetensors          (3.71 GB)
└── vae/ace_1.5_vae.safetensors                        (0.34 GB)
```

工作流：ComfyUI 左側 **Templates** → 搜尋 `ACE-Step 1.5 Music Generation Workflow`（分檔版），
或點 **Workflows → Open** 選 `audio_ace_step_1_5_split.json`（本流程已下載到 `~/ComfyUI/user/default/workflows/`）。

> 16GB 統一記憶體請用 **2B turbo** 系列；XL(4B) 不適合。詳見 `models/README.md`。

---

## 2. 生成音樂（P2）

### 2.1 Prompt 範例
```
lofi hip hop, warm tape saturation, rainy night mood, study beats,
70 BPM, D minor, mellow piano chords, brushed drums, sidechain compression
```
避免：`no vocals, no EDM drop, no harsh synths`

起始 prompt 庫見 `prompts/prompt_library.csv`（10 種氛圍，含調性/BPM）。

### 2.2 建議參數（起點）

| 參數 | 建議值 |
|---|---|
| 長度 | 120 秒（2 分鐘） |
| Steps | 30–50 |
| CFG | 4–7 |
| Seed | 每首換一個；滿意的記下來微調 |
| 輸出 | 44.1 kHz 立體聲 WAV |

### 2.3 批次生成
- 目標一次做 **6–10 首**同氛圍但**互不重複**的曲子。
- 每首稍微改 prompt（樂器、調性、BPM、情境），避免「同模板」。
- 生成空檔去做別的事，別讓機器滿載連續跑好幾小時。

### 2.4 挑選
只留「能撐 2 分鐘不無聊」的。淘汰的不要省，硬用會讓長片變難聽。

### 2.5 音樂目錄與品管（P0/P1）

目的是讓每一首都能回溯到「當時送出的完整 prompt 與參數」，這樣品質差的才好回頭檢討改 prompt。

- `batch_generate.py` 成功後會自動寫側錄（`assets/tracks/<檔>.json`）+ 事件流（`catalog/tracks.jsonl`）+ 跑自動品檢。
- 既有舊曲用 `python3 scripts/backfill_catalog.py` 補（會從 `output/logs/*.log` 撈回 seed）。
- 自動品檢：`python3 scripts/auto_qc.py --all --index`。`fail`（無聲／截斷／嚴重削波）會自動標 `reject`；`warn` 進人工複審。
- 詳細判定與側錄格式見 `README.md` 的「音樂目錄與品管」一節。

> 之後（P2/P3，尚未實作）會在此基礎上做：試聽評分工具、`prompt_report.py` 把壞 prompt 的維度統計出來、標籤與由標籤重組歌單。

---

## 3. 後製（Reaper + 免費外掛）

1. 安裝 **Reaper**（約 60 美元，可永久試用）。
2. 免費外掛：
   - `iZotope Vinyl`（黑膠雜訊、唱盤味）
   - `Chow Tape Model`（磁帶飽和、wobble）
3. 每首處理：
   - 高通濾掉 **30 Hz** 以下的低頻垃圾。
   - 輕微磁帶飽和，修掉 ACE-Step 的金屬感。
   - 用 Reaper 的 loudness meter 確認約 -14 LUFS。

---

## 4. 去人聲 / 分軌（Demucs，P1 已附腳本）

lofi 通常要「去人聲」版本。本專案已封裝：

```bash
# 四軌分離（drums / bass / vocals / other）
./scripts/separate_stems.sh assets/tracks/track.wav

# 只抽人聲 / 伴奏
./scripts/separate_stems.sh assets/tracks/track.wav vocals
```

輸出在 `output/stems/htdemucs/<曲名>/`。

---

## 5. 拼接成長片（P1 已完成）

```bash
./scripts/build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/*/*/*.wav
```

- `8` 是交叉淡入秒數（lofi 建議 6–10 秒）。
- 腳本會逐首 crossfade，並做 **-14 LUFS** 兩段式正規化。

> 要湊滿 1 小時：約 30 首 2 分鐘的曲子。第一次先做 30 分鐘版本試水溫。

---

## 6. 原創視覺

視覺分兩步：**AI 生靜圖** → **程序化做局部微動循環**。這是「原創性」的來源。
> 為什麼不用 AnimateDiff / SVD？Cinemagraph 需求是「90% 靜止、只有極小局部在動」，這需要逐像素座標控制；在 M4 16GB 上 AI 影片模型又慢又易爆記憶體，且無法只鎖定「窗上的雨、檯燈的光暈」。程序化做法秒級完成、零額外下載、逐像素精準。

### 6.1 生場景圖（SD1.5 / 日系模型）

**預設 prompt 已改成「靠窗書桌的動漫女生」**（含 `1girl, solo`，負向詞不再擋人物）：

```bash
python3 scripts/generate_visual.py --count 3 --size 768x512          # 預設 SD1.5
```

想更接近 lofi 動漫風，換 SD1.5 架構的日系 checkpoint（`--ckpt`）：

```bash
# 下載（本流程實測可用，2.1GB）
curl -L -o ~/ComfyUI/models/checkpoints/meinamix_meinaV11.safetensors \
  https://huggingface.co/Xiero/Meinamix/resolve/main/meinamix_meinaV11.safetensors

python3 scripts/generate_visual.py --ckpt meinamix_meinaV11.safetensors \
  --count 3 --size 768x512
```
> 建議模型：**MeinaMix v11**、AnythingV5、Counterfeit。皆為 SD1.5 架構，M4 16GB 可跑（實測約 40 秒／張）。
> 要**純風景**（不要人）：`--prompt "scenery, no humans, indoors, ..."`。原本負向詞含 `people, face, hands` 會讓人出不來，已移除；`1girl, solo, ...` 就會有女生。

### 6.2 做局部微動循環（Cinemagraph）

```bash
# 先用 --check-loop 驗證頭尾無縫（必做）
python3 scripts/make_cinemagraph.py assets/visuals/scene.png --check-loop

# 預設輸出＝只有顆粒＋靜態調色（其餘完全靜止）
python3 scripts/make_cinemagraph.py assets/visuals/scene.png output/visual_loop.mp4

# 要局部微動再逐一開啟（分區要對準圖上位置）
python3 scripts/make_cinemagraph.py assets/visuals/scene.png output/visual_loop.mp4 \
  --glow 0.5 --lamp 0.42,0.27,0.22 --sway 0.012 --plant 0.18,0.10,0.32,0.34

# 先看有哪些效果參數
python3 scripts/make_cinemagraph.py --help
```

效果模組與分區（比例 0~1，換圖只調這裡）：

| 類別 | 效果 | 參數 | 預設強度 | 預設分區 |
|---|---|---|---|---|
| 光線 | 檯燈呼吸 | `--glow` `--lamp cx,cy,r` | `0`（關） | `0.88,0.48,0.22` |
| 光線 | 燈光微閃 | `--flicker` `--flicker-region` | `0`（關） | `0.88,0.48,0.22` |
| 光線 | 車燈掃過 | `--carlight` `--carlight-region` | `0`（關） | 同 window |
| 光線 | 色溫呼吸 | `--temp` | `0`（關） | 全域 |
| 物體 | 蒸氣上升 | `--steam` `--steam-pos cx,cy,r` | `0`（關） | `0.62,0.72,0.10` |
| 物體 | 玻璃水珠 | `--drops` `--drop-region` | `0`（關） | 同 window |
| 物體 | 灰塵微粒 | `--dust` `--dust-region` | `0`（關） | 同 window |
| 物體 | 盆栽微晃 | `--sway` `--plant` | `0`（關） | `0.04,0.52,0.70,0.82` |
| 物體 | 窗簾輕擺 | `--curtain-sway` `--curtain-region` | `0`（關） | `0.51,0.15,0.63,0.66` |
| 表面 | 書頁光暈 | `--sheen` `--sheen-region` | `0`（關） | `0.55,0.87,0.30` |
| 基礎 | 全域呼吸 | `--zoom` `--drift` | `0` / `0`（關） | 全域 |
| 基礎 | 顆粒/暗角/對比/飽和 | `--grain` `--vignette` `--contrast` `--saturation` | `.012`/`.35`/`1.04`/`1.08`（**開**） | 全域 |
| 選用 | 窗內雨絲 | `--rain` `--window` | **`0`（預設關）** | `0.33,0.045,0.70,0.585` |

> - **預設只開顆粒＋靜態調色（暗角/對比/飽和）**；所有「會動的」效果（全域呼吸、燈光、灰塵、盆栽、書頁、色溫，以及需要分區的蒸氣/水珠/車燈/窗簾/微閃/雨）預設全部關閉，避免換圖時分區錯位。要用時逐一開啟並把座標指到圖上正確位置。
> - 雨滴預設關閉的原因：雨絲畫在畫面空間，若沒精準貼合玻璃會被誤看成「室內下雨」。貼在玻璃上的水痕請改用 `--drops`（玻璃水珠滑落）。
> - 蒸氣要**背景偏暗**才明顯；若杯子後面是明亮的窗，熱氣會看不清楚。
> - 車燈用**兩道光帶**模擬車頭燈（`--carlight-sep` 控制間距，設 0 變單道）。

**無縫原理**：所有時變項週期都等於循環秒數 D（`sin/cos(2πt/D)`；雨滴速度取「整數倍窗高 / D」），因此 t=D 與 t=0 完全相同，`--check-loop` 實測差值 ≈ 0。
**注意**：`--window` 等分區要對準你圖上的實際位置；分區錯了效果會落在錯的地方（用內建的視覺檢查：抽出兩格做差異圖即可確認）。

> 舊版 `make_visual_loop.sh`（純 ffmpeg 慢平移+顆粒+暗角）仍保留，適合快速、不需分區控制的場合。

### 6.3 合成影片（P1 已完成）

```bash
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
```

視覺會無限循環到音訊結束，輸出 1080p30 H.264 + AAC。

### 6.4 低負載輸出長片（避免過熱）

**問題**：`make_cinemagraph.py --duration 600` 會逐格算 18,000 格，CPU 滿載數分鐘、明顯發燙；`render_video.sh` 用 libx264 medium 重編整段也會讓 CPU 長時間滿載。

**對策**：`make_long_lofi.sh` 只算**一個短 loop**，再用 `concat -c copy` 複製成整部片（**零重編碼**），升頻/淡入淡出用硬體 `h264_videotoolbox`：

```bash
./scripts/make_long_lofi.sh --generate --minutes 10            # 生圖 + 10 分鐘（只有生圖動 GPU）
./scripts/make_long_lofi.sh --image assets/visuals/scene.png   # 沿用既有圖，完全不開 ComfyUI
./scripts/make_long_lofi.sh --image assets/visuals/scene.png --vbitrate 5      # 檔案更小
THREADS=4 NICE=15 ./scripts/make_long_lofi.sh --image assets/visuals/scene.png # 更保守

# 一集一包（推薦）：輸出集中在 output/episodes/<名稱>/，並產生 publish/<名稱>.json
./scripts/make_long_lofi.sh --image assets/visuals/scene.png \
    --minutes 60 --episode rainy-01 --style rainy_lofi
```

流程：生圖(可選) → 20s 無縫 loop → 1080p 硬體升頻 → 6 首 crossfade(-14 LUFS) 裁到 N 分 → concat copy → 封裝。

- `--episode NAME` → `output/episodes/NAME/{video.mp4, mix.wav, visual_loop.mp4}`（只留這三個；其餘中間檔自動清）。`upload_status.py` 會把每個 episode 資料夾視為一支成片。
- `--loop` 必須整除總長（600/20=30、3600/20=180）。
- 成功後自動刪中間檔（`--keep-temp` 可保留），一次約省 1GB。
- 重步驟之間會 sleep 降溫；`--no-video-fade` 可再省一次重編碼。
- 散熱監控：`pmset -g therm`（**勿用** `pmset -g thermlog`，它會持續輸出）。

### 6.5 容量管理

成片會累積（每分鐘約 37MB、每小時約 2.2GB），內接容易吃爆。策略：素材與程式留內接，`output/` 指到外接碟。

```bash
./scripts/disk_report.sh              # 容量一覽
./scripts/cleanup_outputs.sh          # 預覽要清什麼（預設保留最新 3 支影片）
./scripts/cleanup_outputs.sh --apply  # 真的刪
```

- 遷移與 ExFAT 注意事項詳見 `README.md` 的「容量管理與外接碟」。
- `output/episodes/<name>/` 整包視為一支成片，`cleanup_outputs.sh --keep/--older-than` 以資料夾為單位處理；中間檔（`_loop_1080.mp4`、`_video_copy.mp4`、`_mix_raw.wav`）也會一併列入清理。
- `make_long_lofi.sh` 有空間 preflight，不足會中止。

### 6.6 五段式產線（一條龍 / 分段測試）

把「音樂 → 圖片 → 影片 → 上傳 → 整理」包成一支 orchestrator，`--stage` 控制要跑哪一段：

```bash
# 一條龍（五段全跑）
./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40

# 分段單獨跑（測試/接續）
./scripts/make_episode.sh --style rainy_lofi --stage music --count 20
./scripts/make_episode.sh --style rainy_lofi --stage image --images 3
./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01 --image assets/visuals/rl01_00001_.png
./scripts/make_episode.sh --style rainy_lofi --stage upload --episode rl01
./scripts/make_episode.sh --style rainy_lofi --stage cleanup --episode rl01

# 只印指令、不執行
./scripts/make_episode.sh --style rainy_lofi --episode rl01 --dry-run
```

| 段 | 指令本體 | 產物 |
|---|---|---|
| `music` | `expand_style` → `batch_run` → `auto_qc` → `library` | `assets/tracks/<style>/<run>/` |
| `image` | `generate_visual`（ComfyUI + SD1.5） | `assets/visuals/<episode>*.png` |
| `video` | `make_long_lofi`（cinemagraph + 混音 + 合成 + metadata） | `output/episodes/<episode>/`、`publish/<episode>.json` |
| `upload` | `yt_upload`（YouTube Data API，預設 private） | `publish/<episode>.json` 寫回 youtube_url |
| `cleanup` | `archive_episode.sh` | 外接 `archive/<episode>/`（並刪中間檔） |

- 影片段預設從 `library` 挑 `keep` 曲目（`--limit 16`、`--tracks` 覆寫）；未給 `--image` 就挑 `assets/visuals/` 最新圖。
- `--count 0`（預設）＝音樂段不生成、沿用既有曲目。
- `all` 時可用 `--no-upload` / `--no-cleanup` 略過第 4/5 段；上傳可見性用 `--privacy private|unlisted|public`（預設 private）。

#### 上傳段（第 4 段）一次性設定

1. Google Cloud 專案 → 啟用 **YouTube Data API v3**。
2. OAuth consent screen：External，把要上傳的帳號加進 Test users，scope 加 `.../auth/youtube.upload`。
3. Credentials → OAuth client ID → **Desktop app** → 下載 JSON，放到 `.secrets/client_secret.json`（或設 `YT_CLIENT_SECRET`）。
4. 首次 `--stage upload` 會開瀏覽器授權，token 快取在 `.secrets/yt_token.json`（可先單獨跑 `.venv/bin/python scripts/yt_upload.py --auth-only`）。
5. 上傳後影片為 **private**；請到 Studio 手動公開/排程、勾 **AI 揭露**、上傳縮圖、加播放清單。

> 依賴：`google-api-python-client` / `google-auth-oauthlib` / `google-auth-httplib2`（已列入 `requirements.txt`，裝在 `.venv`）。

---

## 7. OBS 24/7 直播

1. OBS 新增「媒體來源」載入 `lofi_1hr.mp4`，勾選**循環**。
2. 輸出設定：1080p30、位元率 4500–6000 kbps。
3. 直播標題固定（例如 `lofi radio 24/7`），描述放曲目與社群連結。
4. 直播 Content ID 處理比上傳寬鬆，但仍受 inauthentic 政策管——別整台只放同一支死板模板。

---

## 8. 上傳與合規

| 項目 | 做法 |
|---|---|
| AI 揭露 | 上傳時勾「變造或合成內容」（整首 AI 生成要勾）。 |
| 標題 | 含關鍵字：`lofi hip hop radio / study beats / [情境]`。 |
| 描述 | 曲目清單（credits）、時間軸、訂閱連結。 |
| Chapters | 每首曲子的時間戳，讓 YouTube 看到策展痕跡。 |
| 縮圖 | 每支片都要不同，別用同一模板換字。 |
| Content ID | 只用你有權利的音訊；本流程產出沒問題。 |

### 8.1 自動產生上片資訊

```bash
# 產生 title / 描述（含章節）/ tags → publish/<影片>.json + .md
python3 scripts/make_meta.py --video output/videos/xxx.mp4 --style cozy_morning \
    --tracks assets/tracks/cozy_morning/*/*.mp3 --xfade 8

python3 scripts/upload_status.py --ready     # 今天可上傳的
python3 scripts/upload_status.py --mark-uploaded xxx --url https://youtu.be/...
```

- metadata 範本在 `prompts/youtube/<style>.json`（title 池/描述段落/tags/hashtags/縮圖文字）。
- 章節時間戳是 YouTube 需要的「策展痕跡」，務必帶上。
- `make_long_lofi.sh --style <name>` 會在渲染後自動產生上片資訊。

---

## 9. 每週產出節奏（建議）

| 日 | 工作 |
|---|---|
| 一 | 批次生成 6–10 首（一次生一首） |
| 二 | 挑選 + 後製 |
| 三 | 拼接 30–60 分鐘長片 |
| 四 | 做視覺 + 合成影片 |
| 五 | 上傳 + 排程 / 開直播 |
| 六日 | 回覆留言、觀察數據、微調 prompt |

---

## 10. 疑難排解

| 症狀 | 可能原因 | 處理 |
|---|---|---|
| 生成到一半變慢 | M3/M4 過熱降頻 | 分批、讓機器休息（Mac mini 有風扇較耐） |
| MPS 報錯不支援的運算子 | 某些運算子沒 MPS 實作 | 啟動前 `export PYTORCH_ENABLE_MPS_FALLBACK=1` |
| 音訊有金屬感雜音 | ACE-Step 已知 metallic shimmer | 後製加磁帶飽和、EQ 修掉 |
| 生出來的曲子太像 | prompt 太接近 | 改調性/BPM/樂器，長期靠 LoRA 拉開差異 |
| 影片接點有跳動 | 視覺非無縫 | 用 `make_visual_loop.sh`（sin 週期保證頭尾相接） |

---

## 附錄：發布前合規檢查表

- [ ] 使用可商用授權的模型（本流程以 ACE-Step 為主）
- [ ] 上傳勾選 AI 揭露
- [ ] 視覺原創、每支片不同
- [ ] 影片有 chapters / 曲目 credits（策展痕跡）
- [ ] 不是同一模板換首歌的大量上傳
- [ ] 音訊響度約 -14 LUFS
- [ ] 標題與描述有實質內容，不是純關鍵字堆砌
