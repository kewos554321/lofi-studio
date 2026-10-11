# AGENTS.md — 給 AI coding agent 的專案說明

本檔供 Claude Code / OpenCode 等 agent 快速上手。**先讀這裡再看程式。**

## 專案是什麼

本機（Mac M4 / 16GB）AI **lofi 音樂 → 長片影片**的產線，最終上傳 YouTube 頻道 **Drifted LoFi**。
ComfyUI + ACE-Step 生音樂、SD1.5 生場景圖、ffmpeg 做視覺循環與合成。沒有伺服器、沒有雲端。

## 架構（重點）

```
src/lofi/          # Python 套件（所有邏輯；import 用 `from lofi import ...`）
  paths.py         # ROOT 解析（env LOFI_ROOT 或往上找 prompts/styles）
  catalog.py       # 音樂目錄：sidecar assets/tracks/<style>/<run>/*.json + catalog/tracks.jsonl
  qc.py            # 自動品檢（ffmpeg/ffprobe，純標準庫）
  generate.py      # 批次生成（ComfyUI API）
  backfill.py      # 回填既有音檔
  meta.py          # YouTube title/描述/章節/tags
  publish.py       # 上片佇列
  library.py       # SQLite 圖書館(catalog/library.db) + 自動標籤
  expand.py        # 風格骨架 -> 曲目清單 CSV
  visual.py        # ComfyUI + SD1.5 生場景圖
  cinemagraph.py   # 靜圖 -> 無縫局部微動循環（唯一需 numpy 的模組，用 .venv）
  cli.py           # 單一入口 `lofi <command>`（延遲 import，`lofi qc` 不會被 numpy 拖累）
  upload.py        # 上傳成片到 YouTube（唯一需 Google 套件的模組，用 .venv 與 .secrets/ 憑證）
scripts/*.py       # 相容 shim（呼叫 src/lofi/*.main），舊指令仍可用
scripts/*.sh       # ffmpeg/ComfyUI 重流程（make_episode 五段產線 / build_long_mix / make_long_lofi / archive_episode …）
prompts/styles/     # 音樂風格骨架（生成用）
prompts/youtube/    # 上片 metadata 範本（對應同名 style）
assets/tracks/<style>/<run>/   # 生成音檔（分層）+ 每首 sidecar（音檔 gitignore，sidecar 進版控）
catalog/            # tracks.jsonl + index.csv（library.db 為衍生物，gitignore）
publish/            # 上片資訊 + 狀態
output/             # symlink → 外接碟；episodes/<name>/ 為一集一包（video.mp4 + mix.wav + visual_loop.mp4）
tests/              # python3 -m unittest discover -s tests -v
docs/RUNBOOK.md     # 操作手冊
```

## 常用指令

```bash
lofi generate --csv prompts/generated/x.csv --run-id b01   # 或 python3 scripts/batch_generate.py …
lofi qc --all --index
lofi library build && lofi library summary
lofi meta --video output/videos/x.mp4 --style cozy_morning
lofi publish --ready
lofi expand --style rainy_lofi --count 20 --seed 42
lofi visual --count 3 --size 768x512
./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40   # 五段產線一條龍
./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01   # 只跑單一段（music/image/video/upload/cleanup）
./scripts/make_long_lofi.sh --image assets/visuals/x.png --minutes 60 --episode rainy-01 --style rainy_lofi
./scripts/batch_run.sh --csv prompts/generated/x.csv --chunk 50 --sleep 60 --run-id b01 --clean-raw
python3 -m unittest discover -s tests -v
```

## 不變的約定（改動請遵守）

- **可追溯**：任何生成都必須寫 sidecar + `catalog/tracks.jsonl`（prompt/seed/params）。沒記錄等於沒做。
- **唯一命名**：生成檔名含 `--run-id`，禁止讓不同批次產生同名檔（會被覆蓋）。
- **只加不刪**：`catalog/tracks.jsonl` 是 append-only 事件流。
- **不進版控**：`assets/**/*.mp3|wav|flac`、`output/`、`logs/`、`catalog/*.db`、`.venv`、models。
- **風格版本**：改 `prompts/styles/*.json` 要把 `version` +1，否則舊曲來源對不上。
- **繁中註解/輸出**：本專案文件與 CLI 訊息用繁體中文。
- **README / RUNBOOK 要同步**：新增指令要更新 `README.md` 與 `docs/RUNBOOK.md`。

## 環境注意

- `output/` 是**外接碟 symlink**；USB/ExFAT 可能掉線 → 寫影片/清理前先 `./scripts/disk_report.sh`。
  生成音檔寫內接 `assets/tracks`，log 寫內接 `logs/`，掉線不受影響。
- ComfyUI 在 `~/ComfyUI`，需執行中（`127.0.0.1:8188`）才能生成。
- 系統 `python3` 無 numpy；`src/lofi` 的程式**幾乎只用標準庫**（`cinemagraph.py` 例外，需 numpy，用 `.venv`），勿引入需 numpy/librosa 的相依（除非另開 venv 並說明）。

## 資源限制（RAM / 磁碟，每次都要先想）

機器是 **Mac M4 / 16GB 統一記憶體**，CPU 與 GPU（MPS）**共用同一塊 RAM**，內部碟也只有 228GB。
生成、批次、影片流程全都在這台上跑，**記憶體與磁碟是硬限制**——任何新模型、新流程、新參數，動手前先問「這樣會不會爆」。

### 記憶體（16GB 共用）
- ComfyUI 載入模型會整塊佔用 RAM（**載入中與生成中都算**）：
  - SD1.5 fp16 ≈ 2.5GB
  - SDXL fp16 ≈ 6.5GB
  - ACE-Step 音訊 ≈ 9GB（`text_encoders` 4.6G + `diffusion_models` 4.5G）
- **絕對不要同時跑音樂生成與圖片生成**（ACE-Step + SDXL 必爆）。長流程要分階段、分開跑。
- 一次只跑一個 ComfyUI 大工作；批次生成設合理 `--limit`，不要一次丟幾十個 job。多模型疊加（IPAdapter + ControlNet + 放大）要往上加估。
- 爆掉的徵兆：ComfyUI 卡死、job 被系統砍、整台變慢開始狂 swap。寧可小批多跑，不要一次梭哈。

### 磁碟（內部碟 228GB）
- 模型放內部碟 `~/ComfyUI/models`，目前約 25GB（含 ACE-Step 音訊模型）；**單一 SDXL checkpoint 就 6.5GB**。
- 內部碟可用空間**建議維持 >20GB**（macOS 與 swap 需要），逼近就清。
- 大檔（成片、mix、模型）能放外接（`output/` 已在 `WJ_SATA`）就別放內部。
- **外接 `WJ_SATA` 是 USB/ExFAT、實測讀取僅 ~86MB/s，不適合放模型**：內部 NVMe 是 ~2.3GB/s，載入 SDXL（6.5GB）內部約 3 秒、外接要 ~80 秒。推理本身吃 RAM 不吃碟，但**載入會重複付**（16GB 下 ACE-Step 9GB 與 SDXL 6.5GB 互斥，切換就會重載）。
- 所以模型留內部碟，**該控制的是模型數量**（用完的 checkpoint 清掉），不是把模型搬去慢碟。要擴充請換 USB 3.2 / Thunderbolt SSD。
- **測試性的大模型用完就刪**；長期可考慮 `extra_model_paths.yaml` 把模型指到外接碟（僅限快碟）。

### 隨手檢查
```bash
df -h / | tail -1          # 內部碟還剩多少
memory_pressure           # 記憶體壓力（看 free percentage）
./scripts/disk_report.sh  # 外接碟狀態
```

## 提交

小步提交，訊息用英文一行摘要 + 條列重點（參考 git log）。跑過 `tests/` 再提交。
