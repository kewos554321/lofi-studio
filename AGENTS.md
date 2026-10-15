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
| `docs/NAMING.md` | 規則 | 檔名/資料夾命名（音樂 + 視覺）與通用原則 |
| `docs/DISTRIBUTION.md` | 變現 | 多平台上架地圖、自動化天花板、release 上架包規劃 |
| `docs/LORA.md` | 訓練 | 主角 LoRA 的雲端訓練流程（RunPod + AI Toolkit / Z-Image Turbo）與疑難排解 |
| `docs/SECURITY.md` | 安全 | 憑證清單、已知風險與決策紀錄（接受什麼風險、升級路徑、何時重啟） |

> 原則：**新增指令只需改 CLI 的 `--help` + RUNBOOK 一處**；README 只放「怎麼跑」，不重抄參數表。

## 專案是什麼

本機（Mac M4 / 16GB）AI **lofi 音樂 → 長片影片**的產線，最終上傳 YouTube 頻道 **Moni on the Journey**
（2026-10 由舊名 `Drifted LoFi` 改名：**頻道名與音樂藝名統一**，見 `docs/DISTRIBUTION.md` §6-2）。
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
  runs.py          # 產線 run 帳本（catalog/runs.jsonl：run × stage × 進度；長流程要記帳）
  status.py        # 產線狀態（lofi status）＋本機監控 UI（--serve；唯讀 http.server）
  notify.py        # 狀態通知（Email/Gmail SMTP；fail-soft，寄失敗不影響產線）
  license.py       # 商用授權稽核（掃 sidecar/manifest 的模型授權狀態）
  catalog.py       # 音樂目錄：sidecar assets/tracks/<style>/<run>/*.json + catalog/tracks.jsonl
  qc.py            # 自動品檢（ffmpeg/ffprobe，純標準庫）
  generate.py      # 批次生成（ComfyUI API）
  backfill.py      # 回填既有音檔
  meta.py          # YouTube title/描述/章節/tags
  publish.py       # 上片佇列
  library.py       # SQLite 圖書館(catalog/library.db) + 自動標籤
  review.py        # 人工複審：聽感評分(1-5)寫進 sidecar（keeper 篩選用；library --min-rating）
  expand.py        # 風格骨架 -> 曲目清單 CSV
  visual.py        # ComfyUI + SDXL 生場景圖
  cinemagraph.py   # 靜圖 -> 無縫局部微動循環（唯一需 numpy 的模組，用 .venv）
  cli.py           # 單一入口 `lofi <command>`（延遲 import，`lofi qc` 不會被 numpy 拖累）
  upload.py        # 上傳成片到 YouTube（唯一需 Google 套件的模組，用 .venv 與 .secrets/ 憑證）
  release.py       # 把生成 run 包成上架包（母帶+封面+metadata+清單+LISTING 欄位+交付 ZIP；DSP/Bandcamp 無 API，故只產包）
  gumroad.py       # 把上架包送到 Gumroad（API v2：建 draft→上傳→封面→enable；唯一可全自動的商店）
  character.py     # 主角定裝（prompts/character/）＋ LoRA 訓練集一致性檢查／字幕／RunPod 打包
scripts/lofi       # 統一 CLI 入口 wrapper（免安裝；= `python -m lofi.cli`，shell 產線與人都用它）
scripts/check.sh   # 提交前自檢（compile + unittest；CI 跑同一套）
scripts/*.py       # 相容 shim（呼叫 src/lofi/*.main），舊指令仍可用；新流程請走 `lofi <cmd>`
scripts/*.sh       # ffmpeg/ComfyUI 重流程（make_episode 五段產線 / build_long_mix / make_long_lofi / archive_episode …）
scratch/           # 實驗／一次性腳本（不進版控）
prompts/styles/     # 音樂風格骨架（生成用）
prompts/youtube/    # 上片 metadata 範本（對應同名 style）
prompts/character/  # 主角定裝與品牌資料（moni.json：設計/LoRA 方向/封面判別條件）
assets/tracks/<style>/<run>/   # 生成音檔（分層）+ 每首 sidecar（整個 assets/ 不進版控，紀錄看 catalog/tracks.jsonl）
                               # ★ 內接只留工作用 mp3；無損母帶在 output/masters/（外接，--format flac）
assets/visuals/<kind><NN>/      # 生成場景圖（一 run 一夾）＋ manifest.json（一 run 一份：config 共用 + images 逐檔 prompt/seed；不進版控）
assets/refs/                    # 參考素材（人物照片、哼唱錄音、轉檔）—不進版控，且不在任何工具掃描路徑
catalog/            # tracks.jsonl + visuals.jsonl + releases.jsonl + runs.jsonl 事件流（進版控）；index.csv / library.db 為衍生物（gitignore）
publish/            # 上片資訊 + 狀態
output/             # symlink → 外接碟；episodes/<name>/ 為一集一包（video.mp4 + mix.wav + visual_loop.mp4）；releases/<run>/ 為上架包（audio/ 預設 FLAC + 方形封面 + 16:9 4K 圖 + LISTING.md + download/<album>.zip…）；masters/<style>/<run>/ 為無損母帶（FLAC）
tests/              # python3 -m unittest discover -s tests -v
docs/RUNBOOK.md     # 操作手冊
docs/SECURITY.md    # 憑證清單與已知風險（接受什麼風險、升級路徑、何時重啟）
```

## 常用指令

```bash
./scripts/lofi generate --csv prompts/generated/x.csv --run-id b01   # 單一入口（免安裝；= lofi generate）
./scripts/lofi qc --all --index
./scripts/lofi library build && ./scripts/lofi library summary
./scripts/lofi review --list                                          # 看哪些曲還沒人工評分
./scripts/lofi review assets/tracks/<style>/<run>/ --rating 5        # 聽感評分（keeper 篩選用）
./scripts/lofi meta --video output/videos/x.mp4 --style cozy_morning
./scripts/lofi publish --ready
./scripts/lofi expand --style rainy_lofi --count 20 --seed 42
./scripts/lofi visual --count 3 --size 768x512
./scripts/lofi release plan --run coastal01                            # 看某 run 會包成什麼專輯
./scripts/lofi release package --run coastal01 --limit 12              # 產出上架包（母帶+封面+清單）
./scripts/lofi release gumroad --run coastal01                        # 送到 Gumroad（API v2；唯一可全自動的商店）
./scripts/lofi character check                                         # LoRA 訓練集一致性（訓練前必跑）
./scripts/lofi visual --count 3 --clean-raw               # 生圖後刪 ComfyUI 原檔（省內接碟）
./scripts/lofi config                                  # 目前模型檔名／ComfyUI 位址
./scripts/lofi status                                  # 產線狀態（現在跑到哪／佇列／資源）
./scripts/lofi status --serve                          # 本機監控 UI（http://127.0.0.1:8787）
./scripts/lofi notify --test                           # Email 通知測試（.secrets/notify.json）
./scripts/lofi runs ls                                 # 產線 run 帳本總表
./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40   # 五段產線一條龍
                                                                      # 音樂段預設 --format flac（無損母帶放外接 output/masters；
                                                                      # 外接離線會 fail-closed，臨時可 --format mp3）
./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01   # 只跑單一段（music/image/video/upload/cleanup）
./scripts/make_long_lofi.sh --image assets/visuals/x.png --minutes 60 --episode rainy-01 --style rainy_lofi
./scripts/batch_run.sh --csv prompts/generated/x.csv --chunk 50 --sleep 60 --run-id b01 --clean-raw
./scripts/cleanup_outputs.sh --comfy                   # 清 ComfyUI 原始輸出（預覽；加 --apply 才刪）
./scripts/cleanup_outputs.sh --apply                   # 清專案 output（中間檔＋舊成片，保留最新 3 支）
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
- **只加不刪**：`catalog/tracks.jsonl` 與 `catalog/releases.jsonl` 是 append-only 事件流（狀態變更用新事件，不要改舊行）。
- **長流程要記帳**：新的長流程／腳本請用 `runs.stage_run(...)`（Python）或 `lofi runs begin/progress/end`（shell）把每個階段寫進 `catalog/runs.jsonl`（run × stage × 進度；append-only，一筆狀態轉移一行、progress 只在批次邊界）。`lofi status`／`lofi notify` 都讀這份帳本——**沒記帳就等於「現在跑到哪」查不到**。階段記得起跑行程 pid（Python 用 `os.getpid()`；一次性 CLI `runs begin` 用呼叫者 pid），監控才判得出「還在跑」vs「中斷」。
  `make_episode.sh`（每階段）與 `batch_run.sh`（每批）**已經接好**：階段失敗自動寄 error 信、整段跑完寄摘要；`make_episode.sh` 會 export `LOFI_RUNS_NESTED=1`，讓 `batch_run.sh` 只報進度、不重複開段。
- **通知要 fail-soft**：`notify.send()` 永不拋例外（寄失敗只印 stderr + 記 `logs/notifications.jsonl`），且設定不全時自動視為關閉——**加通知不可讓產線新增失敗模式**。憑證放 `.secrets/notify.json`（`chmod 600`，不進版控），設定一律經 `config.NOTIFY_*`／`LOFI_*`。
- **不進版控**：整個 `assets/`（含音檔、sidecar、圖、動畫、字幕）；`output/`、`logs/`、`catalog/*.db`、`.venv`、models。原則：**git 只收程式與紀錄（`catalog/`），不收素材**。
- **憑證只放 `.secrets/`**：`chmod 600`、不進版控、不放在雲端同步資料夾；**不要**把 token／密碼寫進 sidecar／manifest／`publish/*.json`／事件流（那些會進版控）。新增或更換憑證機制時，更新 `docs/SECURITY.md`（憑證清單＋已知風險）。
- **風格版本**：改 `prompts/styles/*.json` 要把 `version` +1，否則舊曲來源對不上。
- **商用授權**：產出要能商用。換模型前先在 `config.MODEL_LICENSES` 登錄 `{license, commercial, source}`；`generate`/`visual`/`release` 對 `commercial!=True` 的模型 **fail-closed**（中止，除非 `--allow-unknown-license`）。生成時把 `model_license`/`model_source` 寫進 sidecar/manifest，`meta` 彙整成 `publish.credits`、`release` 彙整成 `release.json` 的 `credits`。稽核用 `./scripts/lofi license check`；矩陣見 `docs/LICENSING.md`。**別用非商用模型**（例：MusicGen CC-BY-NC）。
- **上架欄位只有一處來源**：`release.json`（`genre`／`explicit`／`songwriter`／`ai_credits`／`listing`）＋同資料夾的 `LISTING.md`（給人複製貼上）與 `credits.txt`（隨交付 ZIP）。AI 揭露（2026 起 DistroKid AI Credits 強制）由 `config.RELEASE_AI_CREDITS` 決定（本產線＝`all_audio`）；`songwriter` 必填**真名**、`tags` 遵守 Gumroad 規則（每 2–20 字、≤10 個）。新平台要填的欄位就加在這裡，別在各處硬編。
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
- **自訂節點（產線相依）**：`scripts/comfy_nodes/lofi_ace_negative.py` 開放 ACE-Step 1.5 的 **negative caption**（內建節點沒有）。安裝：`mkdir -p ~/ComfyUI/custom_nodes/lofi_ace_negative && cp scripts/comfy_nodes/lofi_ace_negative.py ~/ComfyUI/custom_nodes/lofi_ace_negative/__init__.py`，**裝完／改完必須重啟 ComfyUI**。`lofi generate` 預設會用它送每列的 `negative`；節點不在時 `--no-negative` 可退回內建節點。

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
- **無損母帶（FLAC）一定要放外接**：FLAC 是同長度 mp3 的 **3.4 倍**（實測 48kHz 立體 16-bit ≈14.7MB/首 vs mp3 4.3MB）。
  內接只有 228GB，1000 首 FLAC 約吃掉 15GB。`generate --format flac`（**產線音樂段已預設**）只把**工作用 mp3**
  留 `assets/tracks`（內接），母帶寫 `output/masters/<style>/<run>/`（外接）；`release package` 自動優先採用無損母帶。
  flac 模式下 `--clean-raw` 更重要（ComfyUI 原始 FLAC 也十幾 MB、先落內接）。詳見 `docs/DISTRIBUTION.md` §6-3。
- **外接 `WJ_SATA` 是 USB/ExFAT、實測讀取僅 ~86MB/s，不適合放模型**：內部 NVMe 是 ~2.3GB/s，載入 SDXL（6.5GB）內部約 3 秒、外接要 ~80 秒。推理本身吃 RAM 不吃碟，但**載入會重複付**（16GB 下 ACE-Step 9GB 與 SDXL 6.5GB 互斥，切換就會重載）。
- 所以模型留內部碟，**該控制的是模型數量**（用完的 checkpoint 清掉），不是把模型搬去慢碟。要擴充請換 USB 3.2 / Thunderbolt SSD。
- **測試性的大模型用完就刪**；長期可考慮 `extra_model_paths.yaml` 把模型指到外接碟（僅限快碟）。
- **ComfyUI 原始輸出不會自己消失**：成圖／音檔先落在 `~/ComfyUI/output`（在**內接碟**）。生音樂 `batch_run --clean-raw`、生圖 `lofi visual --clean-raw`（`make_episode.sh` 兩段都已預設帶）會在複製走之後刪原檔；漏掉的用 `./scripts/cleanup_outputs.sh --comfy --apply` 補清。

### 隨手檢查
```bash
df -h / | tail -1          # 內部碟還剩多少
memory_pressure           # 記憶體壓力（看 free percentage）
./scripts/disk_report.sh  # 外接碟狀態
```

## 提交

小步提交，訊息用英文一行摘要 + 條列重點（參考 git log）。跑過 `tests/` 再提交。
