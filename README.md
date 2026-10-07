# lofi-studio

本機（Mac M4 / 16GB）AI lofi 產線：**ComfyUI + ACE-Step 生音樂 → 品檢/目錄 → 拼長片 + 視覺 → 自動產生 YouTube 上片資訊**。目標是穩定產出 1 小時以上長片，並可延伸 24/7 直播。

**目前狀態**

- ✅ 音樂目錄 / 自動品檢 / 回填（P0–P1）
- ✅ 量產工具：唯一命名＋續傳、批次 runner、SQLite 圖書館＋自動標籤
- ✅ 上片資訊（title/描述/章節/tags）與上片佇列
- ✅ 程式重構為 `src/lofi` 套件 + `lofi` CLI（舊 `scripts/*.py` 仍可用）
- ✅ 長片拼接、視覺循環、影片合成、容量管理
- ⏳ 24/7 直播（OBS 手動設定）

## 前置需求

| 需要 | 說明 |
|---|---|
| ffmpeg / ffprobe | `brew install ffmpeg`（P1 全靠它） |
| ComfyUI + ACE-Step 1.5 | 生音樂用；見下方「P2：生成音樂」 |
| Python 3.9+ | 系統 `python3` 即可（套件只用標準庫）；Demucs 走 `.venv` |
| 外接碟（建議） | `output/` 指到外接；見「容量管理與外接碟」 |

安裝成 `lofi` 指令（可選）：`pip install -e .`
未安裝時用 `PYTHONPATH=src python3 -m lofi.cli <command>`，或直接 `python3 scripts/<指令>.py`。

## 使用流程（端到端）

**Step 0 — 啟動 ComfyUI（生音樂 / 生圖才需要）**
```bash
./scripts/launch_comfyui.sh          # 瀏覽器開 http://127.0.0.1:8188
```

**Step 1 — 選風格、展開曲目清單**
```bash
python3 scripts/expand_style.py --style rainy_lofi --count 20 --seed 42
#   → prompts/generated/rainy_lofi.csv
```
可用風格見 `prompts/styles/`（含參考頻道風格 `dusk_jazz` / `coastal_guitar` / `forest_piano`）。

**Step 2 — 生成音樂**
```bash
# 少量／試水溫
python3 scripts/batch_generate.py --csv prompts/generated/rainy_lofi.csv --limit 3 --clean-raw

# 大量（例如 1000 首）：分批、自動續傳、散熱
./scripts/batch_run.sh --csv prompts/generated/rainy_lofi.csv \
    --chunk 50 --sleep 60 --run-id rl01 --clean-raw
```
- 檔名含批次碼（`rainy_lofi_01_rl01_00001.mp3`），不會覆蓋舊曲；**同 `--run-id` 重跑＝續傳**。
- 每首自動寫 sidecar + `catalog/tracks.jsonl`，並跑自動品檢。

**Step 3 — 品檢 + 建圖書館**
```bash
python3 scripts/auto_qc.py --all --index      # 響度/削波/靜音/時長
python3 scripts/library.py build              # SQLite + 自動標籤
python3 scripts/library.py summary            # 各風格淘汰率、旗標排行
```

**Step 4 — 挑 keeper 拼長片**
```bash
# 例：品檢 pass 且分數 ≥ 90 的標為 keeper
python3 scripts/library.py set-status keep --verdict pass --min-score 90
python3 scripts/library.py query --tag style:rainy_lofi --status keep

# 拼成長片音訊（-14 LUFS 正規化）
./scripts/build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/rainy_lofi/*/*.mp3
```

**Step 5 — 做視覺並合成影片（最省電）**
```bash
# 生一張場景圖 → 30 分鐘影片（唯一動 GPU 的是生圖）
./scripts/make_long_lofi.sh --generate --minutes 30 \
    --tracks assets/tracks/rainy_lofi/*/*.mp3 --style rainy_lofi
#   加 --style 會在渲染後自動產生上片資訊（Step 6）
```

**Step 6 — 產生上片資訊**
```bash
python3 scripts/make_meta.py --video output/videos/lofi_30min.mp4 \
    --style rainy_lofi --tracks assets/tracks/rainy_lofi/*/*.mp3 --xfade 8
#   → publish/lofi_30min.json + .md（title/描述/章節/tags，可直接複製到 YouTube）
```

**Step 7 — 上片佇列**
```bash
python3 scripts/upload_status.py --ready
python3 scripts/upload_status.py --mark-uploaded lofi_30min --url https://youtu.be/xxxx
```

> 每一步的細節、參數與疑難排解見下面各節；完整操作手冊見 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)，**可直接照抄的成品配置見 [`docs/RECIPES.md`](docs/RECIPES.md)**。

---

## 目錄結構

```
lofi-studio/
├── src/lofi/                  # ★ Python 套件（主要邏輯，`from lofi import ...`）
│   ├── cli.py                 # ★ 單一入口：lofi generate/qc/library/meta/publish/backfill
│   ├── paths.py               # 路徑解析（ROOT）
│   ├── catalog.py             # 音樂目錄（sidecar + JSONL）
│   ├── qc.py                  # 自動品檢
│   ├── generate.py            # 批次生成（ComfyUI）
│   ├── backfill.py            # 回填既有音檔
│   ├── meta.py                # YouTube 上片資訊
│   ├── publish.py             # 上片佇列
│   └── library.py             # SQLite 圖書館 + 自動標籤
├── scripts/                   # bash 管線 + Python 相容 shim（呼叫 src/lofi）
│   ├── check_env.sh           # 環境檢查
│   ├── setup_python_env.sh    # 建立 .venv 並裝 demucs
│   ├── make_demo_tracks.sh    # 產生測試用示範音檔/圖片
│   ├── build_long_mix.sh      # ★ 交叉淡入 + -14 LUFS 正規化
│   ├── make_visual_loop.sh    # 靜圖 -> 無縫循環動態影片（平移+顆粒+暗角）
│   ├── make_cinemagraph.py    # ★ 靜圖 -> 局部微動無縫循環（Cinemagraph，零新依賴）
│   ├── generate_visual.py     # 用 ComfyUI + SD1.5 生 lofi 場景圖（--ckpt 可換日系模型）
│   ├── render_video.sh        # ★ 視覺 + 音訊 -> 最終 mp4
│   ├── make_long_lofi.sh      # ★ 低負載長片：短 loop + 複製（避免 CPU/GPU 過熱）
│   ├── batch_run.sh           # ★ 量產 runner（分塊 + 續傳 + log）
│   ├── separate_stems.sh      # Demucs 分軌 / 去人聲
│   ├── download_p2_models.sh  # 下載 ACE-Step 1.5 + SD1.5 模型
│   ├── launch_comfyui.sh      # 用 MPS 啟動 ComfyUI
│   ├── expand_style.py        # ★ 用風格骨架展開同風格曲目清單（CSV）
│   ├── auto_qc.py / batch_generate.py / make_meta.py / upload_status.py / library.py / backfill_catalog.py  # 相容 shim
│   └── collect_comfy_outputs.sh # 把 ComfyUI 輸出收進 assets/tracks
├── tests/                     # python3 -m unittest discover -s tests -v
├── docs/                      # RUNBOOK.md（手冊）、RECIPES.md（成品配置範本）
├── AGENTS.md                  # 給 AI coding agent 的專案說明
├── pyproject.toml             # 可選：pip install -e . 後有 `lofi` 指令
├── prompts/
│   ├── prompt_library.csv     # 起始 prompt 庫（10 種氛圍）
│   ├── styles/                # 風格骨架（固定 tag + 可變維度），如 rainy_lofi.json
│   ├── youtube/               # 上片 metadata 範本（title/描述/tags），對應同名 style
│   └── generated/             # expand_style.py 產出的曲目清單
├── assets/
│   ├── tracks/<style>/<run>/  # 生成的曲子（分層）；每首附 .json 側錄（來源/品檢/標籤）
│   └── visuals/               # AI 生成的場景圖放這裡
├── catalog/                   # 音樂目錄（P0/P1）
│   ├── tracks.jsonl           # 事件流：每次生成/品檢/… 一行
│   └── index.csv              # 由側錄重建的查詢用索引
├── publish/                   # 上片資訊（make_meta.py 產出：title/描述/章節/狀態）
├── logs/                      # batch runner 的 log（內接，不進版控）
├── output/                    # symlink → 外接碟
│   ├── mixes/                 # 長片音訊
│   ├── videos/                # 最終影片
│   └── stems/                 # Demucs 輸出
└── models/                    # ACE-Step 權重放置說明
```

## 架構與 CLI

Python 邏輯都在 `src/lofi/`（套件）；`scripts/*.py` 只是**相容 shim**，兩者等價：

```bash
lofi qc --all --index              # 等同 python3 scripts/auto_qc.py --all --index
lofi library build
lofi library query --tag style:rainy_lofi --status keep
lofi meta --video output/videos/x.mp4 --style cozy_morning
lofi publish --ready
lofi generate --csv prompts/generated/x.csv --run-id b01
```

（可選）安裝成指令：`pip install -e .`。未安裝時用 `PYTHONPATH=src python3 -m lofi.cli <command>`，或直接用 `scripts/*.py`。
ffmpeg 重流程（`build_long_mix` / `make_long_lofi` / `render_video`）仍以 bash 執行。

## 示範管線（不需 ComfyUI，先測流程）

先用內建的示範素材把 P1 管線跑一遍（生示範音檔 → 拼長片 → 視覺循環 → 合成影片），確認 ffmpeg 環境沒問題。

```bash
cd ai-music/lofi-studio

# 0) 環境檢查
./scripts/check_env.sh

# 1) 產生示範素材（還沒接 ACE-Step 前先測管線）
./scripts/make_demo_tracks.sh

# 2) 交叉淡入成一首長片（3 首 8 秒、淡入 2 秒 -> 約 20 秒）
./scripts/build_long_mix.sh output/mixes/demo_mix.wav 2 assets/tracks/misc/demo/demo_a.wav assets/tracks/misc/demo/demo_b.wav assets/tracks/misc/demo/demo_c.wav

# 3) 做視覺循環（15 秒）
./scripts/make_visual_loop.sh assets/visuals/demo_visual.png output/visual_loop.mp4 15 30

# 3b)（更推薦）局部微動無縫循環：只有窗雨/葉片/燈光在動
#     換圖記得用 --window / --plant / --lamp 調分區（比例座標）
python3 scripts/make_cinemagraph.py assets/visuals/demo_visual.png output/visual_loop.mp4

# 4) 合成影片
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/demo_mix.wav output/videos/demo_lofi.mp4

# 5)（可選）去人聲
./scripts/separate_stems.sh assets/tracks/misc/demo/demo_a.wav vocals
```

> 記得先 `chmod +x scripts/*.sh`。

## 腳本參數速查

> 下表 Python 指令都有 `lofi` 別名：`python3 scripts/<x>.py` ≡ `lofi <x>`（`<x>` = `generate` / `qc` / `library` / `meta` / `publish` / `backfill`）。`scripts/*.sh` 仍為 bash。

| 腳本 | 用法 |
|---|---|
| `build_long_mix.sh` | `OUTPUT CROSSFADE_SECONDS TRACK1 TRACK2 ...`（lofi 建議 6–10 秒淡入） |
| `make_visual_loop.sh` | `INPUT_IMAGE OUTPUT.mp4 [DURATION=15] [FPS=30]` |
| `make_cinemagraph.py` | `IMAGE OUT.mp4 [--duration 15] [--fps 30] [--check-loop] [--zoom .02] [--drift .004] [--temp .02] [--glow .55] [--flicker 0] [--carlight 0] [--steam 0] [--drops 0] [--dust .5] [--sway .012] [--curtain-sway 0] [--sheen .22] [--rain 0]` + 各分區 `--window/--lamp/--plant/--curtain-region/--steam-pos/--drop-region/--dust-region/--sheen-region`（強度設 0 即關；`--zoom 0 --drift 0` = 嚴格局部；完整參數見 `--help`） |
| `generate_visual.py` | `[--count N] [--size 768x512] [--prompt P] [--negative N] [--ckpt NAME] [--dry-run]` |
| `render_video.sh` | `VISUAL_LOOP AUDIO OUTPUT.mp4 [FPS=30] [CRF=20]` |
| `make_long_lofi.sh` | `[--generate \| --image PATH] [--minutes 10] [--loop 20] [--tracks ...] [--xfade 8] [--vbitrate 9] [--cg-args "..."] [--no-video-fade] [--keep-temp] [--out PATH]`（低負載：短 loop + `-c copy` 複製成長片，硬體編碼） |
| `separate_stems.sh` | `INPUT [four\|vocals] [OUTDIR]` |
| `download_p2_models.sh` | `[--ace\|--sd\|all]`（下載到 `~/ComfyUI/models`） |
| `launch_comfyui.sh` | 啟動 ComfyUI（MPS + fallback） |
| `batch_generate.py` | `[--csv PATH] [--limit N] [--offset N] [--run-id NAME] [--no-resume] [--duration 120] [--seed N] [--sleep S] [--clean-raw] [--no-catalog] [--no-qc] [--dry-run]`（生成後自動寫入目錄+品檢；檔名含 run-id、可續傳） |
| `batch_run.sh` | `--csv PATH [--chunk N] [--sleep S] [--rest S] [--total N] [--run-id NAME] [--clean-raw] [--gen-args "..."]`（量產 runner：分塊+log+續傳） |
| `library.py` | `build \| query [--tag T] [--style S] [--verdict V] [--min-score N] \| summary \| tag --auto \| set-status keep ...`（SQLite 圖書館+自動標籤） |
| `expand_style.py` | `--style NAME [--count 20] [--seed N] [--out PATH] [--dry-run]` |
| `collect_comfy_outputs.sh` | `[--move] [--rename]` |
| `backfill_catalog.py` | `[--dry-run] [--force]`（把既有音檔補進目錄） |
| `auto_qc.py` | `[FILE...] [--all] [--force] [--dedupe] [--index] [--json] [--set KEY=VAL]`（自動品檢） |
| `make_meta.py` | `--video PATH --style NAME [--tracks ...] [--xfade N] [--tracklist JSON] [--title T] [--print]`（產生上片資訊） |
| `upload_status.py` | `[--ready] [--json] [--min-duration S] [--mark-uploaded STEM] [--mark-scheduled STEM] [--url URL]`（上片佇列） |
| `disk_report.sh` | （無參數）內接/外接容量、各目錄大小、可清理殘留一覽 |
| `cleanup_outputs.sh` | `[--keep N] [--older-than DAYS] [--archive DIR] [--stems] [--apply]`（**預設只預覽**） |

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

### 同風格量產（建議流程）

YouTube 的長 lofi 電台多半是「**少數幾首同風格曲子 + 重新排序**」。要讓每首聽起來像同一個品牌，關鍵是**固定骨架、只轉旋鈕**：

1. **風格骨架**放 `prompts/styles/<name>.json`：固定的核心 tag（`backbone`）+ 可變維度（`keys` / `bpm_range` / `instruments` / `moods`）。
2. `expand_style.py` 依骨架展開成不重複的曲目清單（同一 `style` 的曲子骨架完全相同，只換調性/BPM/樂器/情境）。
3. 用生成的清單批次生成、篩選出 keeper pool，再靠**不同順序**拼成長片（每支片換順序/視覺/標題，避免模板化）。

```bash
# 1) 展開 20 首同風格清單（可重現）
python3 scripts/expand_style.py --style rainy_lofi --count 20 --seed 42
#    → prompts/generated/rainy_lofi.csv

# 2) 批次生成（建議 120 秒安全區）
python3 scripts/batch_generate.py --csv prompts/generated/rainy_lofi.csv --limit 20 --clean-raw

# 3) 換個順序就變成長片（見下方「給 1 小時長片」）
```

想換風格只要複製 `prompts/styles/rainy_lofi.json`，改 `backbone` 與維度即可，其餘流程不變。

## 音樂目錄與品管（P0/P1，已完成）

生成時就自動記錄來源與參數，之後才能「調閱出品質不好的 prompt」。每次 `batch_generate.py` 生成成功會：

- 在 `assets/tracks/<style>/<run>/<檔名>.json` 寫一份**側錄**（prompt / seed / steps / cfg / temperature / style 版本 / 時長…）。
- 追加一行到 `catalog/tracks.jsonl`（事件流，失敗也記）。
- 立刻跑 `auto_qc.py` 自動品檢，結果寫回側錄。

### 曲目檔案佈局

生成檔放在 `assets/tracks/<style>/<run_id>/`（`<run_id>` 由 `--run-id` 決定，沒有則 `legacy`），每首音檔旁有一份同名 `.json` 側錄。**分層讓每個資料夾最多一個批次**，瀏覽與歸檔都容易。

- 批次選曲（bash glob）：`assets/tracks/<style>/*/*.mp3`
- 最穩健：用圖書館查路徑
  ```bash
  ./scripts/build_long_mix.sh output/mixes/mix.wav 8 \
      $(python3 scripts/library.py query --style rainy_lofi --status keep --limit 30 --paths)
  ```
- 舊版扁平結構遷移：`python3 scripts/migrate_tracks_layout.py`（預設 dry-run；`--apply` 才執行，會寫 manifest，`--revert` 可還原）。

### 回填既有曲子

```bash
python3 scripts/backfill_catalog.py --dry-run   # 先看會補什麼
python3 scripts/backfill_catalog.py             # 補進目錄（會從 output/logs 撈回 seed）
```

### 自動品檢

```bash
python3 scripts/auto_qc.py                  # 只檢查尚未品檢的
python3 scripts/auto_qc.py --all --force    # 全部重算
python3 scripts/auto_qc.py --dedupe --index # 找完全相同內容 + 重建 index.csv
python3 scripts/auto_qc.py --json > qc.json # 給程式用
```

判定（`--set key=value` 可調閾值）：

| 等級 | 條件 |
|---|---|
| ❌ fail | 靜音／近乎無聲、大量靜音、時長短少（截斷）、真峰值 > +2 dB |
| ⚠️ warn | 真峰值過衝、響度超出 -18~-10 LUFS、DC offset、高頻過亮、動態過大 |
| ✅ pass | 以上皆無 |

> ACE-Step 輸出普遍偏燙（真峰值常 > 0 dB），所以只列 warn、不加重扣分；mix 階段用 `-14 LUFS / -1 dBTP` 正規化會處理掉。只有 `fail` 會自動標 `reject`，`warn` 仍進人工複審。
> 概略 BPM（`bpm_est`）純參考，免依賴估測不準，**不列入判定**。近似重複比對需 chromaprint/librosa，目前只做 sha1 完全相同偵測。

### 側錄長相（節錄）

```json
{
  "track_id": "cozy_morning_16_00001",
  "style": "cozy_morning", "style_version": 1,
  "mood": "spring morning breeze", "key": "F major", "bpm": 87,
  "prompt": "lofi hip hop, warm and cozy, ... 87 BPM, F major ...",
  "gen": {"seed": 1703219544, "steps": 8, "cfg_scale": 2.0,
          "temperature": 0.85, "duration": 120, "model": "acestep_v1.5_turbo"},
  "auto_qc": {"verdict": "warn", "score": 93, "lufs": -12.0, "true_peak": 0.4},
  "status": "unreviewed", "tags": [], "review": {}, "usage": []
}
```

> 修改 `prompts/styles/*.json` 時記得把 `version` +1，否則舊曲的 `style_version` 會跟新 prompt 對不上，之後查不出「當時用的是哪版」。`status` 之後會用來篩出 keeper pool（`keep`）再拼接長片。

## 大量生產（量產 1000 首）

- **容量**：mp3 成品約 3.7MB/首 → 1000 首約 **3.7GB**（內接可）；Demucs stems 全做約 85GB（外接，且只對 keeper 做）。
- **時間**：約 4–6 分鐘/首 → 1000 首約 **83 小時**（連跑），加散熱更久，務必分批。
- **檔名唯一**：`batch_generate.py --run-id` 會把批次碼寫進檔名（`style_id_runid_seq.mp3`），跨批次不會覆蓋；**同一個 `--run-id` 重跑會自動續傳**、跳過已完成。

```bash
# 1) 展開 1000 首清單（可重現）
python3 scripts/expand_style.py --style dusk_jazz --count 1000 --seed 42
# 2) 分批量產（每批 50、每首散熱 60s、刪原始檔、可續傳）
./scripts/batch_run.sh --csv prompts/generated/dusk_jazz.csv \
    --chunk 50 --sleep 60 --run-id dusk01 --clean-raw
# 3) 品檢 + 圖書館（自動標籤）
python3 scripts/auto_qc.py --all --index
python3 scripts/library.py build
# 4) 挑 keeper（例：品檢 pass 且分數 ≥ 90）
python3 scripts/library.py set-status keep --verdict pass --min-score 90
python3 scripts/library.py query --tag style:dusk_jazz --status keep
```

> `batch_run.sh` 的 log 寫在**內接 `logs/`**（外接碟掉線也不會丟）；生成音檔在內接 `assets/tracks`。

## P2：生成視覺（SD1.5 場景圖 → 局部微動循環）

音樂之外，視覺同樣用 ComfyUI 生靜圖，再交給 `make_cinemagraph.py` 做**無縫循環的局部微動**（Cinemagraph：90% 靜止、只有極小局部在動）。這是在 Mac M4 / 16GB 上最輕量、最可控的做法（不需 AnimateDiff / SVD）。

### 1. 生場景圖（預設：靠窗書桌的動漫女生）
```bash
# 預設用 SD1.5；prompt/負向詞已內建 1girl（不會再被 people/face 擋掉）
python3 scripts/generate_visual.py --count 3 --size 768x512

# 日系動漫風 checkpoint（本流程已下載 MeinaMix）
python3 scripts/generate_visual.py --ckpt meinamix_meinaV11.safetensors --count 3 --size 768x512

# 想純風景（不要人物）
python3 scripts/generate_visual.py --ckpt meinamix_meinaV11.safetensors --count 3 --size 768x512 \
  --prompt "scenery, no humans, indoors, cozy study room at night, warm desk lamp, open book, \
potted plant, lo-fi anime illustration, soft warm lighting, muted colors, masterpiece, best quality" \
  --negative "lowres, bad hands, text, watermark, people, 1girl, face"
```
> 建議模型：**MeinaMix v11**（本流程已下載）、AnythingV5、Counterfeit — 都是 SD1.5 架構，M4 16GB 完全跑得動（實測約 40 秒／張）。

### 2. 做局部微動循環
```bash
# 先驗證頭尾無縫（必做）
python3 scripts/make_cinemagraph.py assets/visuals/scene.png --check-loop

# 預設輸出：只有「顆粒 + 靜態調色（暗角/對比/飽和）」，畫面其餘完全靜止
python3 scripts/make_cinemagraph.py assets/visuals/scene.png output/visual_loop.mp4

# 要局部微動，再逐一開啟，並把分區指到圖上正確位置（例：檯燈 + 盆栽）
python3 scripts/make_cinemagraph.py assets/visuals/scene.png output/visual_loop.mp4 \
  --glow 0.5 --lamp 0.42,0.27,0.22 --sway 0.012 --plant 0.18,0.10,0.32,0.34
```

效果模組與對應分區（座標為 0~1 比例，換圖只要重調這裡）：

| 類別 | 效果 | 參數 | 說明 |
|---|---|---|---|
| 光線 | 檯燈呼吸 | `--glow` `--lamp cx,cy,r` | 暖光緩慢明滅 |
| 光線 | 燈光微閃 | `--flicker` `--flicker-region` | 燈泡/燭火/霓虹高頻微閃 |
| 光線 | 車燈掃過 | `--carlight` `--carlight-region` 同 `--window` | 兩道暖光橫越窗面 |
| 光線 | 色溫呼吸 | `--temp` | 整體暖↔冷緩慢漂移（預設 0=關） |
| 物體 | 蒸氣上升 | `--steam` `--steam-pos cx,cy,r` | 咖啡杯/湯的熱氣（背景要暗才明顯）|
| 物體 | 玻璃水珠 | `--drops` `--drop-region` | 貼在玻璃上的水珠緩慢滑落 |
| 物體 | 灰塵微粒 | `--dust` `--dust-region` | 光束中飄浮、微微閃爍的光點 |
| 物體 | 盆栽微晃 | `--sway` `--plant x0,y0,x1,y1` | 葉片輕擺，越靠葉尖擺幅越大 |
| 物體 | 窗簾輕擺 | `--curtain-sway` `--curtain-region` | 布幔緩慢擺動 |
| 表面 | 書頁光暈 | `--sheen` `--sheen-region cx,cy,r` | 柔光緩慢掃過 |
| 基礎 | 全域呼吸 | `--zoom` `--drift` | 極慢縮放+微飄移（**預設 0=關**）|
| 基礎 | 顆粒／暗角／對比／飽和 | `--grain .012` `--vignette .35` `--contrast 1.04` `--saturation 1.08` | **預設開**（靜態，不會動）|
| 選用 | 窗內雨絲 | `--rain` `--window` | **預設關閉**：易被看成室內下雨 |

> **預設只開顆粒＋靜態調色**；所有「會動的」效果（全域呼吸、燈光、灰塵、盆栽、書頁、色溫，以及需要分區的蒸氣/水珠/車燈/窗簾/微閃/雨）預設都關閉，避免換圖時分區錯位。要用時逐一開啟，並把座標指到你圖上的實際位置。

> 所有時變項都以循環秒數 D 為週期（`sin/cos(2πt/D)` 或整數倍），**數學上保證頭尾無縫**，`--check-loop` 可驗證（實測差值 ≈ 0）。

### 3. 合成最終影片
```bash
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
```

## 低負載輸出長片（避免過熱）

直接算很長的 cinemagraph（例如 600 秒 = 18,000 格）會讓 CPU 滿載數分鐘、明顯發燙。
`make_long_lofi.sh` 用「**短 loop + 複製**」避開：只算一個短 loop（預設 20s），再用 `concat -c copy` 複製成整部片
（**零重編碼**），升頻與淡入淡出交給硬體編碼（`h264_videotoolbox`），重步驟之間還會 sleep 降溫。

```bash
# 生一張新圖 → 10 分鐘影片（唯一會動 GPU 的步驟是生圖，約 40s）
./scripts/make_long_lofi.sh --generate --minutes 10

# 沿用既有圖（完全不開 ComfyUI，最不熱）
./scripts/make_long_lofi.sh --image assets/visuals/scene.png

# 檔案更小：lofi 用 4~5 Mbps 就很夠（預設 9）
./scripts/make_long_lofi.sh --image assets/visuals/scene.png --vbitrate 5

# 想更保守：限制執行緒、提高 nice
THREADS=4 NICE=15 ./scripts/make_long_lofi.sh --image assets/visuals/scene.png
```

- `--loop 20` 必須整除總長（600 / 20 = 30 次）。
- 成功後自動刪中間檔（`--keep-temp` 可保留）；這次實測省下約 1GB。
- 其他選項：`--tracks ...`、`--xfade 8`、`--cg-args "--steam 1.2 --carlight 0.5 ..."`、`--no-video-fade`（最省電）。
- 散熱監控請用 `pmset -g therm`（`pmset -g thermlog` 會持續輸出，別用）。

## 容量管理與外接碟

專案本身不大（音樂素材 115MB、圖片 1.7MB、文字 <1MB），真正吃容量的是**成片**與**模型**：

| 項目 | 大小 | 成長 |
|---|---|---|
| `output/videos` | 1.6GB（每分鐘 ≈ 37MB、每 30min ≈ 1.1GB、每小時 ≈ 2.2GB） | ★★★ 一直長 |
| `~/ComfyUI/models` | 13GB | 固定 |
| `~/ComfyUI/venv` / 專案 `.venv` | 1.8GB / 787MB | 固定 |

策略：**素材與程式留內接，成片輸出指到外接。** 現況可跑 `./scripts/disk_report.sh` 查看。

### 外接碟（ExFAT / USB）注意事項

- ✅ 可以放：影片、模型等大檔（生得回來、不怕壞）。
- ❌ **不要放**：git repo、`.venv`（ExFAT 無權限、無 symlink/hardlink，且大量小檔很慢）。
- ⚠️ USB 可能被拔或休眠斷線 → 跑 ComfyUI／生成時確保插著；模型外移後若斷線會找不到模型。
- 建議用獨立子目錄 `/Volumes/WJ_SATA/lofi-studio/`，別和既有備份混在一起。

### 把 output 遷移到外接（腳本完全無感）

```bash
# 專案目錄下執行；遷移後 output/ 照舊用，實際寫在外接
mkdir -p /Volumes/WJ_SATA/lofi-studio/output
rsync -a output/ /Volumes/WJ_SATA/lofi-studio/output/
rm -rf output && ln -s /Volumes/WJ_SATA/lofi-studio/output output
```

> `assets/`（音樂／圖片）**不需搬**——它很小且常被讀取，留內接最快。

（可選）把 ComfyUI 模型也移出以釋放 13GB（**先關閉 ComfyUI**）：

```bash
mkdir -p /Volumes/WJ_SATA/lofi-studio/models
rsync -a ~/ComfyUI/models/ /Volumes/WJ_SATA/lofi-studio/models/
rm -rf ~/ComfyUI/models && ln -s /Volumes/WJ_SATA/lofi-studio/models ~/ComfyUI/models
```

### 監控與清理

```bash
./scripts/disk_report.sh                        # 容量一覽 + 可清理殘留
./scripts/cleanup_outputs.sh                    # 預覽（預設保留最新 3 支影片）
./scripts/cleanup_outputs.sh --apply            # 真的刪
./scripts/cleanup_outputs.sh --keep 5 --apply   # 只留最新 5 支影片
./scripts/cleanup_outputs.sh --older-than 14 --apply
./scripts/cleanup_outputs.sh --archive /Volumes/WJ_SATA/lofi-studio/output/videos --apply
```

- 會一併清中間殘留檔（`lofi_loop*`、`lofi_video_*`、`*_raw.wav`、`_loop_list.txt`；可重生）。
- `make_long_lofi.sh` 開頭有**空間 preflight**：可用不足會直接中止（`SKIP_DISK_CHECK=1` 可略過）。
- 省空間小技巧：`make_long_lofi.sh --vbitrate 5`（影片約砍半）、`batch_generate.py --clean-raw`（刪原始音檔）。

## 上片：YouTube metadata 與佇列

比照本頻道既有影片的格式，`make_meta.py` 會自動產生 title / 描述（含章節）/ tags / hashtags，寫到 `publish/<影片>.json` 與 `.md`。

- 標題格式：`[詩意短句] | [曲風描述]`
- 描述結構：詩意開場 → `This ... mix features ...` → `🎧` 要點 → 沉浸段 → `☕` → `💬` 互動 → `🔔` Subscribe → **Tracklist（章節時間戳）** → hashtags
- 章節由「選用的音軌 + crossfade 秒數」推算；每首曲名取自 catalog 側錄的 `mood`。

```bash
# 產生上片資訊（章節由軌道清單推算）
python3 scripts/make_meta.py --video output/videos/lofi_30min_cozy.mp4 \
    --style cozy_morning --tracks assets/tracks/cozy_morning/*/*.mp3 --xfade 8

# 或渲染時就自動產生
./scripts/make_long_lofi.sh --image assets/visuals/scene.png --minutes 30 \
    --tracks assets/tracks/cozy_morning/*/*.mp3 --style cozy_morning
```

### 哪些是今天可以上傳的？

```bash
python3 scripts/upload_status.py           # 佇列總覽
python3 scripts/upload_status.py --ready    # 只看可上傳
python3 scripts/upload_status.py --mark-uploaded lofi_30min_cozy --url https://youtu.be/xxxx
```

判定：有 `publish/<影片>.json`、狀態 `pending`、長度 ≥ 300s（`--min-duration` 可調）＝可上傳。狀態分 `pending / scheduled / uploaded`，記錄在每個 json 並彙整到 `publish/index.csv`。

### 上片 metadata 範本（per style）

`prompts/youtube/<style>.json` 定義該風格的 emoji、標題池、描述段落、hashtags、tags、縮圖文字；沒有範本的風格會用通用模板。已附：`rainy_lofi`、`cozy_morning`、`tokyo_night`、`dusk_jazz`、`coastal_guitar`、`forest_piano`。

> 上傳時記得勾 YouTube 的 **AI 揭露**（紀錄中 `ai_disclosure: true`）。

## 參考頻道風格（Drifted LoFi）

比照參考影片新增三個可直接生成的風格骨架，音樂 prompt 與上片 metadata 都齊了：

| 風格 (`prompts/styles/`) | 對應參考影片 | 特色 |
|---|---|---|
| `dusk_jazz` | Soft Steps into Dusk | 氈鋼琴、刷鼓、低音提琴、vibraphone（A 小調） |
| `coastal_guitar` | Sunset Serenity / Coastal Daydreams | 海岸木吉他、海浪、海鷗 |
| `forest_piano` | Golden Hour Lofi Piano in the Forest | 森林黃昏鋼琴、自然環境音 |

用法與既有風格完全相同：

```bash
python3 scripts/expand_style.py --style dusk_jazz --count 20 --seed 42
python3 scripts/batch_generate.py --csv prompts/generated/dusk_jazz.csv --clean-raw
```

## 給 1 小時長片

約需 30 首 2 分鐘的曲子：

```bash
# 生成的檔是 .mp3；示範檔是 .wav，一起帶入
./scripts/build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/*/*/*.mp3 assets/tracks/*/*/*.wav
./scripts/render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
```

第一次建議先做 30 分鐘試水溫。

> 低負載做法：`./scripts/make_long_lofi.sh --image assets/visuals/scene.png --minutes 60`（3600 / 20 = 180 次，`--loop 20` 可整除），全程幾乎不重編碼。

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
