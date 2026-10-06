# lofi-studio

本地 AI Lofi 製作工作流（Mac M4 / 16GB）。目標：用開源模型穩定產出 1 小時以上的 lofi 長片，並可延伸成 24/7 直播。

本專案把流程拆成兩層：

- **P1 自動化管線（已完成）**：音軌交叉淡入 → 長片正規化 → 視覺循環 → 合成影片 → Demucs 分軌。全部可用 CLI 跑。
- **P2 生成（待安裝）**：ComfyUI + ACE-Step 產生音樂；AI 生圖做視覺。需要 GUI 與數 GB 下載。

---

## 目錄結構

```
lofi-studio/
├── scripts/
│   ├── check_env.sh           # 環境檢查
│   ├── setup_python_env.sh    # 建立 .venv 並裝 demucs
│   ├── make_demo_tracks.sh    # 產生測試用示範音檔/圖片
│   ├── build_long_mix.sh      # ★ 交叉淡入 + -14 LUFS 正規化
│   ├── make_visual_loop.sh    # ★ 靜圖 -> 無縫循環動態影片
│   ├── render_video.sh        # ★ 視覺 + 音訊 -> 最終 mp4
│   ├── separate_stems.sh      # Demucs 分軌 / 去人聲
│   ├── download_p2_models.sh  # 下載 ACE-Step 1.5 + SD1.5 模型
│   ├── launch_comfyui.sh      # 用 MPS 啟動 ComfyUI
│   ├── batch_generate.py      # ★ 用 API 批次生成多首（讀 prompt_library.csv）
│   └── collect_comfy_outputs.sh # 把 ComfyUI 輸出收進 assets/tracks
├── prompts/prompt_library.csv # 起始 prompt 庫（10 種氛圍）
├── assets/
│   ├── tracks/                # 生成的曲子放這裡（輸入）
│   └── visuals/               # AI 生成的場景圖放這裡
├── output/
│   ├── mixes/                 # 長片音訊
│   ├── videos/                # 最終影片
│   └── stems/                 # Demucs 輸出
└── models/                    # ACE-Step 權重放置說明
```

## 快速開始

```bash
cd ai-music/lofi-studio

# 0) 環境檢查
./scripts/check_env.sh

# 1) 產生示範素材（還沒接 ACE-Step 前先測管線）
./scripts/make_demo_tracks.sh

# 2) 交叉淡入成一首長片（3 首 8 秒、淡入 2 秒 -> 約 20 秒）
./scripts/build_long_mix.sh output/mixes/demo_mix.wav 2 assets/tracks/demo_a.wav assets/tracks/demo_b.wav assets/tracks/demo_c.wav

# 3) 做視覺循環（15 秒）
./scripts/make_visual_loop.sh assets/visuals/demo_visual.png output/visual_loop.mp4 15 30

# 4) 合成影片
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/demo_mix.wav output/videos/demo_lofi.mp4

# 5)（可選）去人聲
./scripts/separate_stems.sh assets/tracks/demo_a.wav vocals
```

> 記得先 `chmod +x scripts/*.sh`。

## 腳本參數速查

| 腳本 | 用法 |
|---|---|
| `build_long_mix.sh` | `OUTPUT CROSSFADE_SECONDS TRACK1 TRACK2 ...`（lofi 建議 6–10 秒淡入） |
| `make_visual_loop.sh` | `INPUT_IMAGE OUTPUT.mp4 [DURATION=15] [FPS=30]` |
| `render_video.sh` | `VISUAL_LOOP AUDIO OUTPUT.mp4 [FPS=30] [CRF=20]` |
| `separate_stems.sh` | `INPUT [four\|vocals] [OUTDIR]` |
| `download_p2_models.sh` | `[--ace\|--sd\|all]`（下載到 `~/ComfyUI/models`） |
| `launch_comfyui.sh` | 啟動 ComfyUI（MPS + fallback） |
| `batch_generate.py` | `[--limit N] [--duration 120] [--seed N] [--dry-run]` |
| `collect_comfy_outputs.sh` | `[--move] [--rename]` |

## P2：生成音樂（ACE-Step 1.5）與視覺（SD1.5）

ComfyUI 安裝在 `~/ComfyUI`，模型放在 `~/ComfyUI/models`。首次設定：

```bash
# 1) 下載模型（ACE-Step 1.5 分檔版 + SD1.5，約 12GB，可續傳）
./scripts/download_p2_models.sh

# 2) 啟動 ComfyUI
./scripts/launch_comfyui.sh
#    瀏覽器開 http://127.0.0.1:8188

# 3) 在 ComfyUI 左側 Templates 找：
#    「ACE-Step 1.5 Music Generation Workflow」（分檔版）
#    或直接開 user/default/workflows/audio_ace_step_1_5_split.json
```

生成設定（16GB Mac 建議）：
| 項目 | 值 |
|---|---|
| DiT | `acestep_v1.5_turbo`（2B，turbo 8 steps） |
| Text encoder | `qwen_0.6b_ace15` + `qwen_1.7b_ace15`（架構需要兩個） |
| VAE | `ace_1.5_vae` |
| 長度 | 120 秒 / 首 |
| 輸出 | 存到 `~/ComfyUI/output/audio`，再複製到 `assets/tracks/` |

> 官方另有 **AIO 單檔 10GB**（`ace_step_1.5_turbo_aio.safetensors`），內容與分檔完全相同。
> 分檔不會比較省空間；只是檔案數量不同。

### 批次生成（建議）

不用手動一首首點，直接用腳本讀 `prompts/prompt_library.csv` 逐首生成：

```bash
# ComfyUI 需保持執行中
python3 scripts/batch_generate.py --limit 3        # 先生 3 首試水溫（約 15 分鐘）
python3 scripts/batch_generate.py                  # CSV 全部 10 首（約 50 分鐘）
python3 scripts/batch_generate.py --seed 12345     # 固定 seed 可重現
```

每首在 M4 / 16GB 上約 **4–5 分鐘**，生成後自動複製到 `assets/tracks/`。

## 給 1 小時長片

約需 30 首 2 分鐘的曲子：

```bash
# 生成的檔是 .mp3；示範檔是 .wav，一起帶入
./scripts/build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/*.mp3 assets/tracks/*.wav
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
```

第一次建議先做 30 分鐘試水溫。

## 合規重點（發布前）

- 使用可商用授權的模型（本流程以 **ACE-Step（MIT）** 為主；授權請於使用前再次確認官方 repo）。
- 上傳時勾選 **AI 揭露（變造或合成內容）**。
- 視覺原創、每支片不同；影片要有 chapters / 曲目 credits。
- 不要同一模板換首歌大量上傳（YouTube 2025/7 起以頻道層級偵測 inauthentic content）。
- 音訊響度約 **-14 LUFS**。

詳細步驟見 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。

## 常見問題

| 症狀 | 處理 |
|---|---|
| MPS 報錯不支援運算子 | 啟動前 `export PYTORCH_ENABLE_MPS_FALLBACK=1` |
| 生成到一半變慢 | M3/M4 過熱降頻；分批、讓機器休息 |
| 音訊有金屬感雜音 | ACE-Step 已知 metallic shimmer；後製加磁帶飽和 + EQ 修掉 |
| 生出來的曲子太像 | 改 prompt 的調性/BPM/樂器，別只換 seed |
