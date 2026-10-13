# AGENTS.md — 給 AI coding agent 的專案說明

本檔供 Claude Code / OpenCode 等 agent 快速上手。**先讀這裡再看程式。**

## 文件定位（改動時只更新該更新的那份）

| 文件 | 定位 | 什麼該放這裡 |
|---|---|---|
| `README.md` | 上手 | 前置、最小可行流程、指令總覽（**參數一律指向 `--help`，不手抄**） |
| `docs/RUNBOOK.md` | 操作手冊 | 逐步流程、疑難排解、維運細節 |
| `AGENTS.md`（本檔） | agent 速查 | 架構、不變的約定、踩過的坑 |
| `docs/RECIPES.md` | 配方 | 量產用的錨定參數／成品配置 |
| `docs/LICENSING.md` | 合規 | 模型授權矩陣、商用守門、換模型 SOP |
| `docs/NAMING.md` | 規則 | 視覺檔名/資料夾命名 |

> 原則：**新增指令只需改 CLI 的 `--help` + RUNBOOK 一處**；README 只放「怎麼跑」，不重抄參數表。

## 專案是什麼

本機（Mac M4 / 16GB）AI **lofi 音樂 → 長片影片**的產線，最終上傳 YouTube 頻道 **Drifted LoFi**。
ComfyUI + ACE-Step 生音樂、SDXL 生場景圖、ffmpeg 做視覺循環與合成。沒有伺服器、沒有雲端。

## 架構（重點）

```
src/lofi/          # Python 套件（所有邏輯；import 用 `from lofi import ...`）
  paths.py         # ROOT 與所有目錄的唯一來源（env LOFI_ROOT 或往上找 prompts/styles）
  config.py        # 集中設定：模型檔名／ComfyUI 位址（LOFI_* 環境變數可覆寫；`lofi config` 可查）
  media.py         # ffmpeg/ffprobe 小工具（ffprobe_duration…，純標準庫）
  comfy.py         # ComfyUI API 小客戶端（api/system_stats/submit/wait_done）
  lock.py          # 生成互斥鎖（fcntl，避免音樂/圖片同時跑爆 16GB RAM）
  io.py            # 原子寫入（atomic_write_text/json）+ JSONL 檔案鎖 + 壞檔隔離（quarantine）
  license.py       # 商用授權稽核（掃 sidecar/manifest 的模型授權狀態）
  catalog.py       # 音樂目錄：sidecar assets/tracks/<style>/<run>/*.json + catalog/tracks.jsonl
  qc.py            # 自動品檢（ffmpeg/ffprobe，純標準庫）
  generate.py      # 批次生成（ComfyUI API）
  backfill.py      # 回填既有音檔
  meta.py          # YouTube title/描述/章節/tags
  publish.py       # 上片佇列
  library.py       # SQLite 圖書館(catalog/library.db) + 自動標籤
  expand.py        # 風格骨架 -> 曲目清單 CSV
  visual.py        # ComfyUI + SDXL 生場景圖
  cinemagraph.py   # 靜圖 -> 無縫局部微動循環（唯一需 numpy 的模組，用 .venv）
  cli.py           # 單一入口 `lofi <command>`（延遲 import，`lofi qc` 不會被 numpy 拖累）
  upload.py        # 上傳成片到 YouTube（唯一需 Google 套件的模組，用 .venv 與 .secrets/ 憑證）
scripts/lofi       # 統一 CLI 入口 wrapper（免安裝；= `python -m lofi.cli`，shell 產線與人都用它）
scripts/check.sh   # 提交前自檢（compile + unittest；CI 跑同一套）
scripts/*.py       # 相容 shim（呼叫 src/lofi/*.main），舊指令仍可用；新流程請走 `lofi <cmd>`
scripts/*.sh       # ffmpeg/ComfyUI 重流程（make_episode 五段產線 / build_long_mix / make_long_lofi / archive_episode …）
scratch/           # 實驗／一次性腳本（不進版控）
prompts/styles/     # 音樂風格骨架（生成用）
prompts/youtube/    # 上片 metadata 範本（對應同名 style）
assets/tracks/<style>/<run>/   # 生成音檔（分層）+ 每首 sidecar（整個 assets/ 不進版控，紀錄看 catalog/tracks.jsonl）
assets/visuals/<kind><NN>/      # 生成場景圖（一 run 一夾）＋ manifest.json（一 run 一份：config 共用 + images 逐檔 prompt/seed；不進版控）
catalog/            # tracks.jsonl + visuals.jsonl 事件流（進版控）；index.csv / library.db 為衍生物（gitignore）
publish/            # 上片資訊 + 狀態
output/             # symlink → 外接碟；episodes/<name>/ 為一集一包（video.mp4 + mix.wav + visual_loop.mp4）
tests/              # python3 -m unittest discover -s tests -v
docs/RUNBOOK.md     # 操作手冊
```

## 常用指令

```bash
./scripts/lofi generate --csv prompts/generated/x.csv --run-id b01   # 單一入口（免安裝；= lofi generate）
./scripts/lofi qc --all --index
./scripts/lofi library build && ./scripts/lofi library summary
./scripts/lofi meta --video output/videos/x.mp4 --style cozy_morning
./scripts/lofi publish --ready
./scripts/lofi expand --style rainy_lofi --count 20 --seed 42
./scripts/lofi visual --count 3 --size 768x512
./scripts/lofi config                                  # 目前模型檔名／ComfyUI 位址
./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40   # 五段產線一條龍
./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01   # 只跑單一段（music/image/video/upload/cleanup）
./scripts/make_long_lofi.sh --image assets/visuals/x.png --minutes 60 --episode rainy-01 --style rainy_lofi
./scripts/batch_run.sh --csv prompts/generated/x.csv --chunk 50 --sleep 60 --run-id b01 --clean-raw
./scripts/check.sh                                     # 提交前自檢（compile + unittest）
python3 -m unittest discover -s tests -v
```

## 不變的約定（改動請遵守）

- **可追溯**：任何生成都必須寫 sidecar + `catalog/tracks.jsonl`（prompt/seed/params）；圖片寫在該批 `assets/visuals/<kind><NN>/manifest.json`。沒記錄等於沒做。**紀錄是本機檔、不進版控**——git 只收 `catalog/*.jsonl`（音樂的事件流）。
  manifest 是「**一個 run 一份**」：該批固定的模型/尺寸/steps/cfg/negative… 放 `config`（所有圖共用，只寫一次），逐檔會變的 `prompt`/`seed` 列在 `images`。同一個 run 再跑只會**併寫**（新檔 append、舊檔就地更新），不會蓋掉既有紀錄；改某張的 prompt 就改 `images` 裡那一筆。寫入用 `visual.update_manifest()`，不要自己開檔覆寫。
  `tracks.jsonl` 每筆是**精簡事件**（`catalog.summarize_event()`：只記發生什麼、帶 `schema_version`，**不含整包快照或 fingerprint**）——完整狀態在 sidecar。要避免事件流被撐大，別改成整包寫入。
- **執行資訊要清楚**：任何生成前先用 `visual.print_run_header()` 印出工具、run-id、**哪個模型在跑**、參數與輸出路徑（逐張輸出也帶著模型短標籤），別讓 log 只剩下看不懂的進度行。
- **生成互斥**：`generate`/`visual`/`cinemagraph` 取同一把鎖（`logs/.gpu.lock`），第二個程序會直接中止並印出持有者（避免 ACE-Step + SDXL 同時跑爆 RAM）。要排隊設 `LOFI_LOCK_WAIT=秒`；硬要同時跑設 `LOFI_NO_LOCK=1`。新模組若也吃 GPU/RAM，請照樣用 `from lofi.lock import gpu_lock`。
- **設定集中**：模型檔名／ComfyUI 位址不要硬編散落，一律經 `src/lofi/config.py`（可用 `LOFI_*` 環境變數覆寫）。換模型只改一處，也可用 `lofi config` 查目前值。
- **寫入要原子**：sidecar / manifest / publish / token 等一律用 `lofi.io.atomic_write_*`（同目錄 tmp → `os.replace`），不要直接 `write_text`；append 事件流用 `lofi.io.append_jsonl`（自帶 flock）。讀到壞 JSON 先 `io.quarantine()` 隔離成 `*.corrupt-*`，**不可**當新檔覆蓋。理由：半截檔會被 `load_sidecar→None` 抹掉人工評分/標籤。
- **生成前查空間**：會寫內接碟的生成流程先 `media.require_free(...)`（門檻 `config.MIN_FREE_GB`／`LOFI_MIN_FREE_GB`）；量測類工具（ffmpeg）先 `media.require("ffmpeg","ffprobe")`，且量測不到時 `qc` 要判 `error`，不可假通過。
- **唯一命名**：生成檔名／目錄含 run 代號（音樂 `assets/tracks/<style>/<run>/`；圖片 `assets/visuals/<run-id>/`），禁止讓不同批次產生同名檔（會被覆蓋）。
  圖片命名固定為「資料夾 `<kind><NN>`、檔名 `<run-id>_<NN>[_<slug>].<ext>`」——`kind`：`ref` 主角定裝參考／`ds` LoRA 訓練集／`sc` 場景風格測試；`NN` 兩位數照生成順序；`slug` 小寫英數短標籤。例：`ref04/ref04_01_gold-socks.png`、`ds02_07_walk-side-fireplace.png`。衍生檔的 slug 用保留字首 `anim-`（動畫）/`cmp-`（對照合成）/`mask-`（遮罩）/`fix-`（後處理）。未給 `--run-id` 時工具會自動編號（`ref05` 之後給 `ref06`）。規則全文與舊→新對照表在 `docs/NAMING.md`。
  音樂新 run 命名標準：`<run-id>_<NNNN>[_<slug>].mp3`（run-id 不可變、序號固定 4 位；`row_id`/`prompt`/`seed` 放 sidecar）。**`track_id` 記在 sidecar、不從檔名推算**；舊 run 檔名維持原樣不動。通用原則與遷移建議詳見 `docs/NAMING.md`。
- **只加不刪**：`catalog/tracks.jsonl` 是 append-only 事件流。
- **不進版控**：整個 `assets/`（含音檔、sidecar、圖、動畫、字幕）；`output/`、`logs/`、`catalog/*.db`、`.venv`、models。原則：**git 只收程式與紀錄（`catalog/`），不收素材**。
- **風格版本**：改 `prompts/styles/*.json` 要把 `version` +1，否則舊曲來源對不上。
- **商用授權**：產出要能商用。換模型前先在 `config.MODEL_LICENSES` 登錄 `{license, commercial, source}`；`generate`/`visual` 對 `commercial!=True` 的模型 **fail-closed**（中止，除非 `--allow-unknown-license`）。生成時把 `model_license`/`model_source` 寫進 sidecar/manifest，`meta` 彙整成 `publish.credits`。稽核用 `./scripts/lofi license check`；矩陣見 `docs/LICENSING.md`。**別用非商用模型**（例：MusicGen CC-BY-NC）。
- **繁中註解/輸出**：本專案文件與 CLI 訊息用繁體中文。
- **README / RUNBOOK 要同步**：新增指令要更新 `README.md` 與 `docs/RUNBOOK.md`。

## 環境注意

- **盡量不安裝新東西**：優先用系統內建或已裝好的工具（例：長時任務用內建 `screen`，不要為此裝 tmux）。真的必要才裝，並在 commit/說明裡寫明為什麼。
- `output/` 是**外接碟 symlink**；USB/ExFAT 可能掉線 → 寫影片/清理前先 `./scripts/disk_report.sh`。
  生成音檔寫內接 `assets/tracks`，log 寫內接 `logs/`，掉線不受影響。
- ComfyUI 在 `~/ComfyUI`，需執行中（`127.0.0.1:8188`）才能生成。
- 系統 `python3` 無 numpy；`src/lofi` 的程式**幾乎只用標準庫**（`cinemagraph.py` 例外，需 numpy，用 `.venv`），勿引入需 numpy/librosa 的相依（除非另開 venv 並說明）。

### ComfyUI 操作要點（踩過的坑）

- **啟動一定要用 venv 的 python**：`cd ~/ComfyUI && nohup ./venv/bin/python main.py --force-fp16 > comfyui.log 2>&1 &`
  用系統 `python3` 會因為缺 `filelock` 等套件直接掛掉。注意 `ps aux` 顯示的 python 路徑是 venv 解析後的**真實路徑**，別被騙以為是系統 python。
- 重啟前先確認佇列是空的：`curl -s 127.0.0.1:8188/queue`（避免中斷正在跑的工作）。
- 新增模型／custom node 後**必須重啟** ComfyUI 才會生效。
- **GGUF 量化在 Mac MPS 上遠快於 int8**：Z-Image Turbo 同一張圖，int8 要 **920 秒**、GGUF（Q5_K_M）只要 **221 秒**（快 4 倍）。
  原因是 int8 沒有 Metal 加速路徑，會退回超慢的模擬運算。**在 Mac 上優先用 GGUF。**
- GGUF 用法：裝 `ComfyUI-GGUF`（在 `custom_nodes/`）＋ `gguf` pip 套件到 venv → 用 `UnetLoaderGGUF` / `CLIPLoaderGGUF` 節點。
- **（實驗註記，非預設）z-image GGUF**：`unet/z-image-turbo-Q5_K_M.gguf` + `text_encoders/Qwen3-4B-Q8_0.gguf` + `vae/ae.safetensors`，CLIP type 選 `lumina2`。產線**預設圖片模型仍是 SDXL `DreamShaperXL-v1.0-Alpha2`**（見 `config.py`）；這組只在個別視覺實驗用過。

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
- 模型放內部碟 `~/ComfyUI/models`，目前約 37GB（含 ACE-Step 音訊與 SDXL）；**單一 SDXL checkpoint 就 6.5GB**。
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
