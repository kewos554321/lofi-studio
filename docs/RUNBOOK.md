# Lofi 製作 RUNBOOK

完整操作手冊。P1（自動化管線）已可執行；P2（AI 生成）需另外安裝並用 GUI 操作。
> 這份文件是把你提供的 SOP 整理、補齊缺漏後的版本。標記 **[待確認]** 的地方請以官方文件為準。

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
./scripts/build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/*.wav
```

- `8` 是交叉淡入秒數（lofi 建議 6–10 秒）。
- 腳本會逐首 crossfade，並做 **-14 LUFS** 兩段式正規化。

> 要湊滿 1 小時：約 30 首 2 分鐘的曲子。第一次先做 30 分鐘版本試水溫。

---

## 6. 原創視覺

1. 用 AI 生圖（例如本地 Stable Diffusion）做一張 lofi 場景（書桌、雨窗、咖啡）。
2. 做**細微循環動畫**（雨滴、蒸氣、燈光閃爍）——這是你的「原創性」來源。

本專案提供 ffmpeg 版本，從一張靜圖做無縫循環：

```bash
./scripts/make_visual_loop.sh assets/visuals/desk.png output/visual_loop.mp4 15 30
```

### 6.1 合成影片（P1 已完成）

```bash
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
```

視覺會無限循環到音訊結束，輸出 1080p30 H.264 + AAC。

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
