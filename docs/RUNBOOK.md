# Lofi 製作 RUNBOOK

完整操作手冊。P1（自動化管線）已可執行；P2（AI 生成）需另外安裝並用 GUI 操作。
> 這份文件是把你提供的 SOP 整理、補齊缺漏後的版本。標記 **[待確認]** 的地方請以官方文件為準。

> 程式結構：Python 邏輯在 `src/lofi/`（單一入口 `./scripts/lofi <command>`，或 `pip install -e .` 後直接 `lofi <command>`）；`scripts/*.py` 為相容 shim；ffmpeg/ComfyUI 重流程仍為 `scripts/*.sh`。詳見 `AGENTS.md` 與 `README.md`。
> 模型檔名／ComfyUI 位址集中在 `src/lofi/config.py`（`./scripts/lofi config` 可查，`LOFI_*` 環境變數可覆寫）。**商用授權**登錄同檔（`MODEL_LICENSES`），生成會 fail-closed 擋非商用/未知模型，稽核用 `./scripts/lofi license check`（見 `docs/LICENSING.md`）。生成互斥：`generate`/`visual`/`cinemagraph` 同鎖，衝突即中止（`LOFI_LOCK_WAIT=秒` 排隊、`LOFI_NO_LOCK=1` 硬跑）。提交前跑 `./scripts/check.sh`。
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
| 輸出 | 預設 **48 kHz 立體聲 mp3**（ComfyUI `SaveAudioMP3`，≈248 kbps VBR）；`--format flac` 改存無損 FLAC |

### 2.3 批次生成
- 目標一次做 **6–10 首**同氛圍但**互不重複**的曲子。
- 每首稍微改 prompt（樂器、調性、BPM、情境），避免「同模板」。
- 生成空檔去做別的事，別讓機器滿載連續跑好幾小時。
- **要上架串流的曲請加 `--format flac`**：母帶（無損）寫外接 `output/masters/<style>/<run>/`，
  內接只留 ffmpeg 轉出的工作 mp3（流程不變）。FLAC 是 mp3 的 ~8 倍大，**不要**放內接（見 `DISTRIBUTION.md` §3.3）。
  批次範例：`./scripts/lofi generate --csv prompts/generated/x.csv --clean-raw --format flac`

### 2.4 挑選
只留「能撐 2 分鐘不無聊」的。淘汰的不要省，硬用會讓長片變難聽。

### 2.5 音樂目錄與品管（P0/P1）

目的是讓每一首都能回溯到「當時送出的完整 prompt 與參數」，這樣品質差的才好回頭檢討改 prompt。

- `batch_generate.py` 成功後會自動寫側錄（`assets/tracks/<檔>.json`）+ 事件流（`catalog/tracks.jsonl`）+ 跑自動品檢。
- 既有舊曲用 `./scripts/lofi backfill` 補（會從 `output/logs/*.log` 撈回 seed）。
- 自動品檢：`./scripts/lofi qc --all --index`。`fail`（無聲／截斷／嚴重削波）會自動標 `reject`；`warn` 進人工複審。
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

### 6.1 生場景圖（SDXL）

**預設 prompt 已改成「靠窗書桌的動漫女生」**（含 `1girl, solo`，負向詞不再擋人物）：

```bash
./scripts/lofi visual --count 3 --size 768x512      # 預設 DreamShaperXL（SDXL）
```

> **一批一夾＋命名規則**：輸出到 `assets/visuals/<run-id>/`，夾內寫一份 `manifest.json`。
>
> ```
> 資料夾： <kind><NN>                      ref=主角定裝參考 / ds=LoRA 訓練集 / sc=場景風格測試
> 檔名　： <run-id>_<NN>[_<slug>].<ext>     ref04_01_gold-socks.png、ds02_07_walk-side-fireplace.png
> ```
>
> - `--run-id` 沒給就自動編號（`ref05` 之後給 `ref06`），不必自己記數。
> - `--slug` 給短標籤（小寫英數），一眼看出內容；`--count 3` 會依序編號。
> - 衍生檔的 slug 用保留字首：`anim-`（動畫）、`cmp-`（對照合成）、`mask-`（遮罩）、`fix-`（後處理）。
> - 完整規則與舊→新對照表：`docs/NAMING.md`。
> - `--clean-raw`：複製到 `assets/visuals/` 且寫完 manifest 後，刪掉 `~/ComfyUI/output` 的原檔（生圖不刪會一直堆在內接碟；事後可用 `cleanup_outputs.sh --comfy` 補清）。

`manifest.json` 是「**一個 run 一份**」，分兩塊：

- **`config`**：這一 run 固定、所有圖共用的設定（`ckpt`/`size`/`steps`/`cfg`/`sampler`/`negative`…），只寫一次。
- **`images`**：逐檔清單，每筆 `{file, seed, prompt}`。prompt 放這裡才能「一張一張對應」。

```jsonc
{
  "manifest_version": 2, "run_id": "cat01", "tool": "lofi.visual",
  "created_at": "…", "updated_at": "…",
  "config": { "ckpt": "DreamShaperXL-v1.0-Alpha2.safetensors", "size": "832x1216",
              "steps": 28, "cfg": 6.5, "sampler": "dpmpp_2m", "negative": "…" },
  "images": [ { "file": "cat01_01_window.png", "seed": 12345, "prompt": "a cat on a windowsill…" } ]
}
```

好處：同一個 `--run-id` 再跑，只會**併寫**（新檔 append、舊檔就地更新），不會蓋掉舊紀錄；要改某一張的 prompt，就改 `images` 裡那一筆。舊的扁平格式 manifest 讀進來會自動升級成這個結構。

每次生成前會先印一段執行資訊，開頭就寫明**跑的是哪顆模型**：

```
────────────────────────────────────────────────────────────────
▶ lofi.visual（ComfyUI + SDXL）    run-id=sc07    共 3 張
  模型：DreamShaperXL-v1.0-Alpha2
  輸出：assets/visuals/sc07
  尺寸：832x1216   步數：28   CFG：6.5   取樣器：dpmpp_2m
────────────────────────────────────────────────────────────────
[1/3] DreamShaperXL-v1.0-Alpha2 832x1216 seed=123456
    ✅ 152s -> sc07_01_window.png
```

> 舊 run 有圖卻沒有 manifest（紀錄遺失）時可從圖檔內嵌的 workflow 反推補回：`visual.backfill_manifest()`（CLI 無對應指令，需自行呼叫；一次性回填腳本已移至 `scratch/`）。

換其他 SDXL 基底（`--ckpt`；模型須已放進 `~/ComfyUI/models/checkpoints/`）：

```bash
./scripts/lofi visual --ckpt sd_xl_base_1.0.safetensors --count 3
# 動漫 tag 系（NoobAI-XL / WAI-Illustrious）記得加 --clip-skip 2
```
> 建議模型：預設 **DreamShaperXL-v1.0-Alpha2**（SDXL，半寫實插畫）；動漫 tag 風可用 NoobAI-XL、WAI-Illustrious。
> ⚠️ SDXL 模型約 6.5GB，載入佔 RAM；生圖約 2.5 分鐘／張，生成期間勿同時跑音樂生成（見 AGENTS.md「資源限制」）。
> 要**純風景**（不要人）：`--prompt "scenery, no humans, indoors, ..."`。原本負向詞含 `people, face, hands` 會讓人出不來，已移除；`1girl, solo, ...` 就會有女生。

#### 鎖角色（IPAdapter，固定吉祥物用）

頻道吉祥物（黑貓）這種「**每次都要長一樣**」的角色，靠 prompt 描述鎖不住，要用參考圖注入特徵：

```bash
# 參考圖放 ~/ComfyUI/input/（本專案慣例：mascot_blackcat_ref.png ＝ 選定的定裝參考圖）
./scripts/lofi visual --ref mascot_blackcat_ref.png --weight 0.6 \
  --run-id ds03 --slug sit-window --size 832x1216 --steps 28 --cfg 6.5 \
  --write-caption                      # LoRA 訓練集要字幕：同名 .txt（內容＝prompt）
```

- **`--weight` 是主控旋鈕**：0.60 場景自由但小特徵（白襪）會漂；0.75 特徵回來了但風格變硬。
- IPAdapter 只注入 CLIP 特徵、**不搬像素**：小面積特徵（白襪）天生鎖不牢，要真正鎖死得走 img2img／局部重繪，或訓練 LoRA（`--write-caption` 就是為了餵 LoRA）。
- 授權：`--ref` 會一併查 IPAdapter 那兩支模型（`config.IPADAPTER_MODELS`），沒登錄會被 fail-closed 擋下。
- `manifest.json` 的 `tool` 自動記成 `sdxl-ipadapter`，`config` 多記 `ref`/`weight`/`weight_type`/`preset`/`embeds_scaling`/`start`/`end`（這樣回頭才看得出這批是用哪張參考圖、多重鎖的）。
- 生成會取 `logs/.gpu.lock` 互斥鎖：音樂生成跑著時會直接中止（保護 16GB RAM）。
- **鎖角色：LoRA 是正解**。IPAdapter 只注入 CLIP 特徵、不搬像素，白襪這種小面積特徵鎖不牢；
  定裝與訓練流程見 [`LORA.md`](LORA.md)（本機只生資料集，訓練在 RunPod）。
  訓好後用 `lofi zimage --lora mnxy_probe.safetensors --lora-weight 1.0`（**z-image 的 LoRA 只能走這條**；
  `lofi visual --lora` 是 SDXL 流程，架構不同掛不上——見 [`LORA.md`](LORA.md) §6）。
  ⚠️ **定裝以 `prompts/character/moni.json` 為唯一來源**（純黑毛＋琥珀金眼＋四腳白襪）。
- **訓練前先跑** `./scripts/lofi character check`：檢查訓練集（a）每張有沒有字幕、
  （b）字幕是否與定裝一致、（c）負向詞是否對抗錯誤特徵、（d）張數是否夠（建議 ≥20）。
  它把「manifest 記了但磁碟沒有」視為**正常的人工剔除**（不報錯），只報留下來那些圖的問題。
- **要訓 LoRA 請走 [`LORA.md`](LORA.md)**：本機 16GB 不可能訓練，訓練在 RunPod（**AI Toolkit**——
  kohya／sd-scripts **不支援 Z-Image**）。流程是「本機生資料集 → 寫**觸發詞承載角色**的字幕 →
  打包上 pod 訓練 → 下載並驗 sha256 → 放 `~/ComfyUI/models/loras/` → 登錄授權 → `lofi zimage --lora`」。
  ⚠️ **字幕寫法才是成敗關鍵**：角色只能由觸發詞承載，**不可以**再用底座聽得懂的詞描述角色——
  否則 LoRA 永遠學不會綁定（v1 就是這樣失敗的，見 `LORA.md` §2）。
  > ⚠️ **白襪 vs 金襪未確認**：`ref04_01_gold-socks.png` 檔名是「金襪」，但該圖 prompt、`ds03` 字幕
  > （`clearly white paws`／`white socks on all four feet`）與負向詞（`all black paws, black feet`）**全部指向白襪**。
  > 若定裝要改成金襪，`ds` 系列的訓練字幕與負向詞必須同步改，否則 LoRA 會學成白襪。

#### 局部重繪（inpaint：`--init-image` + `--mask`）

只重畫圖的某一塊、其餘保留原圖——把生成場景「換成定裝角色」或補小特徵（例：白襪）用這個：

```bash
# 遮罩＝黑底、要重畫的區域塗白，尺寸與起點圖相同；兩張都放 ~/ComfyUI/input/
./scripts/lofi visual --init-image sc10_01_room.png --mask cat-area.png \
  --run-id fix01 --slug fix-white-socks --prompt "…black cat with white socks…"
```

- **接縫旋鈕**：`--grow-mask 8`（遮罩先外擴像素）、`--grow-mask-by 6`（VAE 編碼前再擴的 latent 像素）。重繪區邊緣出現一圈髒縫就往上加。
- 可與 `--ref` **併用**（＝「這塊重畫成定裝角色」）：`--mask` 決定重畫哪裡，`--ref` 決定畫成誰。這是白襪在純 IPAdapter 下鎖不住時的正解。
- 只給 `--init-image`、不給 `--mask` 就是一般 **img2img**：`--denoise 0.3~0.45` 小幅改、`0.5~0.7` 大改、`1.0` 等於只受起點圖的構圖限制。
- 衍生檔的 slug 用保留字首 `fix-`（後處理）／`mask-`（遮罩），一眼看出不是原始生成圖。
- `--mask` 沒搭 `--init-image` 會直接報錯中止（遮罩必須相對於某張圖）。

**建議加 AI 放大**（768×512 直接升到 1080p 會軟糊）：

```bash
./scripts/download_p2_models.sh --upscale   # 一次性：Real-ESRGAN anime 6B（17MB）
./scripts/lofi visual --count 3 --size 768x512 \
  --upscale RealESRGAN_x4plus_anime_6B.pth  # 768×512 -> 1536×1024
```
> `make_episode.sh` 裝了模型後會**自動放大**（`--no-upscale` 可關）；`make_long_lofi.sh` 升頻已改用 lanczos。

**生完先品檢**（擋掉整張爛掉的圖，例如 VAE fp16 溢位造成的黑圖／灰圖）：

```bash
lofi visual-qc --expect 1536x1024    # 掃 assets/visuals，順便比對尺寸
lofi visual-qc --json > vqc.json     # 只看結果不寫檔：--no-write
```

> 用 ffmpeg `signalstats`/`entropy`（純標準庫）判定：fail = 解不開／全黑／全白／純色／無彩度；
> warn = 尺寸不符／近灰／對比過低。結果寫進圖片旁側錄 `<image>.png.json` 的 `visual_qc`，並 append 到
> `catalog/visuals.jsonl`。**只抓「整張爛掉」**；人臉融化、多手多腳這類內容崩壞需模型，暫不處理。
> 反覆出現黑圖時，改 ComfyUI 啟動參數把 VAE 走 fp32（`main.py --force-fp16 --fp32-vae`）通常就解了。

#### 主角 Moni 的封面（Z-Image Turbo GGUF ＋ LoRA）

主角 LoRA 是在 **Z-Image Turbo** 上訓練的，**只能走這條**（`lofi visual` 是 SDXL，掛不上）。

> **這也是 `make_episode.sh --stage image` 的預設引擎**：第 2 段預設就用 Z-Image ＋ Moni LoRA
> （`--size` 預設 `1472x832`＝16:9、放大到 `1920x1080`，所以影片端 scale+crop 不會裁到內容）。
> 要走舊流程加 `--image-engine sdxl`；換場景用 `--image-prompt "mnxy, <場景>"`；
> 整段不掛主角用 `--lora ""`。LoRA 由 `config.ZIMAGE_LORA` 決定（預設 `mnxy_probe.safetensors`）。

```bash
# 一次性：LoRA 放進 ComfyUI 並登錄授權（config.MODEL_LICENSES；本專案 fail-closed）
cp output/lora/probe_mnxy/run/mnxy_probe.safetensors ~/ComfyUI/models/loras/

# 生封面：prompt 含觸發詞；畫風句由 --style 自動接上（來自 config.ZIMAGE_STYLE）
./scripts/lofi zimage --lora mnxy_probe.safetensors --lora-weight 1.0 \
  --prompt "mnxy, sitting on a wooden bench on a seaside promenade at sunset" \
  --size 832x1216 --kind sc --slug seaside
```

| 參數 | 說明 |
|---|---|
| `--lora` / `--lora-weight` | 主角 LoRA 與強度（**1.0 就是在模型上**）|
| `--style` | 畫風句（預設 `config.ZIMAGE_STYLE`；**關掉會讓花色飄，別關**）|
| `--from-image` | **只做 AI 放大**（不生成）：把既有圖放大成高畫質衍生圖，交付 ZIP 的高畫質圖來源 |
| `--dry-run` | 只印設定與授權檢查（授權檢查先跑，乾跑就能知道會不會被擋）|

**高畫質交付圖（`--from-image`）**：商店頁顯示的是小尺寸（16:9 封面 1920×1080、方形縮圖 1080×1080），
但**買家拿到的要跟商店看到的一致、而且畫質更高**。所以 `release package` 會把該集橫式原圖
AI 放大成 4K 一起收進交付 ZIP：

```bash
# 把該集影片封面放大成 4K（衍生圖，命名 <run>_NN_fix-4k.png；約 15 秒）
./scripts/lofi zimage --from-image assets/visuals/cozy-1hr-01/cozy-1hr-01_01.png \
  --run-id cozy-1hr-01 --slug fix-4k --upscale-out 3840x2160 --clean-raw
# 之後 release package 自動挑「解析度最高的橫式圖」當來源：
#   商店封面 gumroad_cover_1920x1080.jpg（縮小）＋ ZIP 內 artwork_3840x2160.jpg（4K 原尺寸）
```

`pick_wide_cover()` 取的是**解析度最高**的橫式圖（不是最新那張），所以放了 4K 衍生圖就會用它。
影片／cinemagraph 仍走 1080p——不要為了畫質把影片封面生大，會白吃 RAM（見 `AGENTS.md` 資源限制）。
| `--init-image` / `--mask` | 局部重繪（與 `lofi visual` 同一套遮罩規矩）|

- **prompt 規矩**：一定要含觸發詞，並帶畫風句。少了畫風句，觸發詞只綁得住「是貓」，
  花色會掉（實測會變寫實乳牛貓）——見 [`LORA.md`](LORA.md) §6。
- 不給 `--lora` 就是純 Z-Image 場景圖（無主角），拿來做對照組很方便。
- 一張約 **4 分鐘**（832×1216、8 步、MPS）；`manifest.json` 會記 `lora` / `lora_weight`。
- 模型檔名集中在 `config.py`：`LOFI_ZIMAGE_UNET`／`CLIP`／`VAE`／`LORA`／`LORA_WEIGHT`／`STYLE`。

### 6.2 做局部微動循環（Cinemagraph）

```bash
# 先用 --check-loop 驗證頭尾無縫（必做）
./scripts/lofi cinemagraph assets/visuals/sc01/sc01_03_rain.png --check-loop

# 預設輸出＝只有顆粒＋靜態調色（其餘完全靜止）
./scripts/lofi cinemagraph assets/visuals/sc01/sc01_03_rain.png output/visual_loop.mp4

# 要局部微動再逐一開啟（分區要對準圖上位置）
./scripts/lofi cinemagraph assets/visuals/sc01/sc01_03_rain.png output/visual_loop.mp4 \
  --glow 0.5 --lamp 0.42,0.27,0.22 --sway 0.012 --plant 0.18,0.10,0.32,0.34

# 先看有哪些效果參數
./scripts/lofi cinemagraph --help
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
./scripts/make_long_lofi.sh --image assets/visuals/sc01/sc01_03_rain.png   # 沿用既有圖，完全不開 ComfyUI
./scripts/make_long_lofi.sh --image assets/visuals/sc01/sc01_03_rain.png --vbitrate 5      # 檔案更小
THREADS=4 NICE=15 ./scripts/make_long_lofi.sh --image assets/visuals/sc01/sc01_03_rain.png # 更保守

# 一集一包（推薦）：輸出集中在 output/episodes/<名稱>/，並產生 publish/<名稱>.json
./scripts/make_long_lofi.sh --image assets/visuals/sc01/sc01_03_rain.png \
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
./scripts/disk_report.sh              # 容量一覽（含 ComfyUI 原始輸出殘留）
./scripts/cleanup_outputs.sh          # 預覽要清什麼（預設保留最新 3 支影片）
./scripts/cleanup_outputs.sh --apply  # 真的刪
./scripts/cleanup_outputs.sh --comfy --apply   # 連 ComfyUI 原始輸出（~/ComfyUI/output）一起清
```

- 遷移與 ExFAT 注意事項詳見 `README.md` 的「容量管理與外接碟」。
- `output/episodes/<name>/` 整包視為一支成片，`cleanup_outputs.sh --keep/--older-than` 以資料夾為單位處理；中間檔（`_loop_1080.mp4`、`_video_copy.mp4`、`_mix_raw.wav`）也會一併列入清理。
- `--comfy` 清的是 **ComfyUI 自己的原始輸出**（圖 `.png/.jpg/.webp`、音 `.flac/.wav/.mp3`，深度 2 層；目錄可用 `LOFI_COMFY_ROOT` 覆寫）。`lofi visual` 沒有 `--clean-raw` 時這些檔會一直留在內接碟（實測曾累積 100+ 張、上百 MB），所以生圖後記得清。
- 生圖／生音樂當下就刪：`lofi visual --clean-raw`（複製到 `assets/visuals/` 後刪原檔）、`lofi generate --clean-raw`（同理，刪 `output/audio` 原檔）。`--format flac` 時原檔是 30MB 級 FLAC，**更要記得帶**。
- `make_long_lofi.sh` 有空間 preflight，不足會中止。

### 6.6 五段式產線（一條龍 / 分段測試）

把「音樂 → 圖片 → 影片 → 上傳 → 整理」包成一支 orchestrator，`--stage` 控制要跑哪一段：

```bash
# 一條龍（五段全跑）
./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40

# 分段單獨跑（測試/接續）
./scripts/make_episode.sh --style rainy_lofi --stage music --count 20
./scripts/make_episode.sh --style rainy_lofi --stage image --images 3
./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01 --image assets/visuals/sc01/sc01_03_rain.png
./scripts/make_episode.sh --style rainy_lofi --stage upload --episode rl01
./scripts/make_episode.sh --style rainy_lofi --stage cleanup --episode rl01

# 只印指令、不執行
./scripts/make_episode.sh --style rainy_lofi --episode rl01 --dry-run

# 小測試到「上傳前」：1 分鐘、生 2 首、1 張圖、不上傳、不整理
./scripts/make_episode.sh --style rainy_lofi --smoke
```

| 段 | 指令本體 | 產物 |
|---|---|---|
| `music` | `expand_style` → `batch_run` → `auto_qc` → `library` | `assets/tracks/<style>/<run>/` |
| `image` | `lofi zimage`（**Z-Image Turbo ＋ 主角 LoRA＝Moni**；`--image-engine sdxl` 退回舊 SDXL）＋ **AI 放大一張 4K 交付圖** | `assets/visuals/<kind><NN>/`（含 manifest.json；`--clean-raw` 刪 ComfyUI 原檔） |
| `video` | `make_long_lofi`（cinemagraph + 混音 + 合成 + metadata） | `output/episodes/<episode>/`、`publish/<episode>.json` |
| `upload` | `yt_upload`（YouTube Data API，預設 private）＋**自動設縮圖** | `publish/<episode>.json` 寫回 youtube_url |
| `cleanup` | `archive_episode.sh` | 外接 `archive/<episode>/`（並刪中間檔） |

- 影片段預設從 `library` 挑 `keep` 曲目（`--limit 16`、`--tracks` 覆寫）；未給 `--image` 就挑 `assets/visuals/<run-id>/` 最新圖。
- **`image` 段會多做一張 4K 交付圖**（`<run>_NN_fix-4k.png`，AI 放大，約 15 秒）：商店頁顯示 1080p、
  買家拿 4K，`release package` 會自動挑它（`pick_wide_cover` 取解析度最高者）。尺寸用 `--artwork-out`，
  不要就 `--no-artwork`。挑「影片要用的圖」時會**排除** `_fix-/anim-/cmp-/mask-` 衍生檔——否則 4K 圖
  會被當成影片來源，cinemagraph 跑 4K（慢又吃 RAM）。
- 清理：`music` 段的 `batch_run` 帶 `--clean-raw`（刪 `~/ComfyUI/output/audio` 原檔）、`image` 段帶 `--clean-raw`（刪原圖），ComfyUI 輸出不會殘留；`cleanup` 段則封存成片到外接 `archive/`。
- `--count 0`（預設）＝音樂段不生成、沿用既有曲目。
- `all` 時可用 `--no-upload` / `--no-cleanup` 略過第 4/5 段；上傳可見性用 `--privacy private|unlisted|public`（預設 private）。

#### 上傳段（第 4 段）一次性設定

1. Google Cloud 專案 → 啟用 **YouTube Data API v3**。
2. OAuth consent screen：External，把要上傳的帳號加進 Test users，scope 加 `.../auth/youtube.upload`。
3. Credentials → OAuth client ID → **Desktop app** → 下載 JSON，放到 `.secrets/client_secret.json`（或設 `YT_CLIENT_SECRET`）。
4. 首次 `--stage upload` 會開瀏覽器授權，token 快取在 `.secrets/yt_token.json`（可先單獨跑 `./scripts/lofi upload --auth-only`）。
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
# 產生 title / 描述（含章節）/ tags ＋ YouTube 縮圖（1280×720）
#   → publish/<影片>.json + .md + .thumbnail_1280x720.jpg
./scripts/lofi meta --video output/videos/xxx.mp4 --style cozy_morning \
    --tracks assets/tracks/cozy_morning/*/*.mp3 --xfade 8

./scripts/lofi publish --ready     # 今天可上傳的
./scripts/lofi publish --mark-uploaded xxx --url https://youtu.be/...
```

- **縮圖自動產生**：文字取自 `prompts/youtube/<style>.json` 的 `thumbnail_text`（本產線＝`COZY / MORNING / LOFI`，
  系列一致），底圖自動挑 `assets/visuals/<名稱>/` **解析度最高**的橫式圖（AI 放大過的 `_fix-4k` 會勝出）。
  本機沒有 Pillow、ffmpeg 也沒有 drawtext → 走 `qlmanage` 渲染 HTML 再裁切（`meta.make_thumbnail`）。
  想換底圖：`--thumb-art <圖>`；不想產生：`--no-thumbnail`。
- **上傳時自動設定縮圖**：`lofi upload` 讀 `publish/<名稱>.json` 的 `thumbnail` 並呼叫 `thumbnails.set`
  （這支 token 只有 `youtube.upload`，實測設縮圖可以）。要覆寫：`--thumbnail <檔>`；不要設：`--no-thumbnail`。
- 產線（`make_episode.sh --stage upload`）會連縮圖一起做完，不必手動上傳。

- metadata 範本在 `prompts/youtube/<style>.json`（title 池/描述段落/tags/hashtags/縮圖文字）；
  頻道層的文案（頻道描述/關鍵字/業務描述）在 `prompts/youtube/_channel.json`。
- 章節時間戳是 YouTube 需要的「策展痕跡」，務必帶上。
- **描述尾段會自動帶 AI 揭露與模型授權**（`meta.build_credits_block()`，由 sidecar 的模型彙整）：
  這樣「授權清單隨每次發行附上」在 YouTube 也成立（不再只有 Gumroad 的下載包有）。沒有模型紀錄時不加水段。
- `make_long_lofi.sh --style <name>` 會在渲染後自動產生上片資訊。

### 8.2 YouTube 以外的出路（上架包）

目前產線只有 YouTube 一個出口，`assets/tracks/<style>/<run>/`（**一個 run 就是一張專輯**）沒有被當成獨立資產。
多平台上架的地圖、自動化天花板與分期順序見 **[`DISTRIBUTION.md`](DISTRIBUTION.md)**。

重點：DSP 商（DistroKid / TuneCore / Amuse / RouteNote / CD Baby）與 Bandcamp **都沒有公開 API**，
所以自動化的正確形狀是「**產出上架包 ＋ 清單**」，不是「一鍵上架」。

```bash
# 先看這個 run 會變成什麼專輯（不寫檔）：曲序、長度、重複曲名、母帶估算
./scripts/lofi release plan --run coastal01
./scripts/lofi release plan --run coastal01 --limit 12        # 只取前 12 首
./scripts/lofi release plan --run coastal01 --min-score 95    # 只看品檢分數高的

# 選曲（策展用）：--tracks 吃 plan 印出的序號／track_id／檔名片段
./scripts/lofi release plan    --run forest-1hr-01 --tracks 32,10,4,7,36,11
./scripts/lofi release package --run forest-1hr-01 --tracks 32,10,4,7,36,11 --album "Into the Forest"

# 產出上架包
./scripts/lofi release package --run coastal01
./scripts/lofi release package --run coastal01 --limit 12 --album "The Windowsill"
./scripts/lofi release package --run coastal01 --style coastal_guitar --visual-run sc06
./scripts/lofi release package --run coastal01 --format flac  # 母帶用 FLAC（較小）
./scripts/lofi release package --run coastal01 --threads 2 --nice 15   # 與其他工作共存
```

> **一個 run 的 `mood` 通常只有 5–6 種**（同風格量產的必然結果），所以「取前 12 首」會產生一堆 `(pt. 2)`。
> 正確做法是 **策展**：用 `--tracks` 挑一組，並把重複的曲名改成有敘事的名字。
> 改法：先產出 → 編輯 `output/releases/<run>/release.json` 的 `tracks[].title` → **重跑**（會沿用你的名字，並清掉改名前留下的舊母帶）。
> 若只是要快，`--limit 6` 取每種 mood 一首可得到「零重複曲名」的短 EP。

輸出在 `output/releases/<run>/`（外接碟）：

| 檔案 | 內容 |
|---|---|
| `audio/NN_<slug>.wav` | 正規化母帶（**-14 LUFS / -1 dBTP**，保留取樣率）；內嵌標籤含 title/artist/album/track/genre/date/copyright/**comment（AI 揭露）**——標籤跟著檔案走，買家丟掉 README 也看得到。改標籤要 `--force` |
| `cover_3000x3000.jpg` | 封面（置中裁切＋放大；**純圖不疊字**）——也是商店縮圖（方形）用的同一套圖 |
| `artwork_3840x2160.jpg` | 交付 ZIP 用的 **4K 高畫質橫式圖**（商店 16:9 封面用的同一套圖）。來源優先取該集 AI 放大過的圖 |
| `gumroad_cover_1920x1080.jpg` / `gumroad_thumb_1080x1080.jpg` | 商店頁直接上傳用（橫式封面 ≥1280×720／方形縮圖 ≥600×600，72 DPI） |
| `download/<album>.zip` | 交付檔（`audio/`＋**方形與橫式兩張圖**＋`tracklist.csv`＋`credits.txt`）；Gumroad／Bandcamp 的「內容」直接傳這個。原則：**買家拿到的要和商店頁看到的一致**（商店顯小尺寸、ZIP 給高畫質版） |
| `release.json` | 機器用：album/artist/曲序/**genre/explicit/songwriter/AI credits**/`listing`/授權 provenance/每首母帶參數 |
| `tracklist.csv` | **買家視角**的曲目表：No/Title/Artist/Album/Year/Duration/Vibe/BPM/Key/Instrumental/Explicit/Format/File/Size（不含 ISRC——那是發行商欄位，見 `LISTING.md`） |
| `LISTING.md` | **上架欄位對照**：DistroKid／Gumroad 要填的值（複製貼上用）＋長版文案與 tags |
| `credits.txt` | 隨交付檔附上的 AI 揭露與模型授權（商用 provenance） |
| `CHECKLIST.md` | DSP／Bandcamp／stock／Gumroad 各平台的上架步驟 |

- **artist 名義**：已定案為 **`Moni on the Journey`**（`config.RELEASE_ARTIST`，可用 `LOFI_RELEASE_ARTIST` 或 `--artist` 覆寫）。
  **與 YouTube 頻道同名**（2026-10 頻道由 `Drifted LoFi` 改名統一）；單字名在 Apple Music／MusicBrainz 全部撞名，見 DISTRIBUTION §6-2。
  （2026-10 由舊名 `Moni's Journey` 改名；新名無撇號，原本的直／彎引號問題消失。）
- **曲名重複自動去重**：sidecar 的 `mood` 會重複，同名者自動加 `(pt. N)`。
  想改名字：編輯 `release.json` 的 `tracks[].title` 再**重跑**（會沿用你改過的曲名，並清掉改名前留下的舊母帶）。
- **母帶來源會自動分級**：若該曲有無損母帶（`output/masters/<style>/<run>/*.flac`），`release` **優先採用**它；
  沒有才退回 `assets/tracks` 的工作 mp3。`release.json` 的 `master_source` 與逐首 `lossless` 會明記，
  不會讓你誤以為來源是有損的。要產無損母帶：`generate --format flac`（**母帶寫外接**，見 §8.2 上方與 `DISTRIBUTION.md` §3.3）。
- **封面一律是主角（黑貓 Moni）**：`release` 會讀各 run 的 `manifest.json` 逐檔 prompt，只挑「黑貓」的圖，
  **排除橘虎斑的 `sc01/sc02/sc06`**；判別條件在 `prompts/character/moni.json` 的 `cover_match`。
  找不到主角圖會警告並要你用 `--cover` 指定。要換封面：`--cover <圖檔>` 或 `--visual-run <run>`。
- **選曲自動套品檢政策**：`--exclude-flags`（預設 `config.QC_BLOCKING_FLAGS` = `wide_dynamics,truncated,silent,clipped`）
  只擋「**母帶修不掉**」的 flag。`hot_true_peak`／`loudness_out_of_range` **放行**——`release` 的 loudnorm
  一定修得掉（實測：原始 warn ×30 的森林鋼琴，產出後 **12/12 pass**）。
  > **不要用 `--min-score 100`**：全庫 126 首只剩 11 首（9%），而丟掉的理由是產線自己會修的。
  > 用這份黑名單則可用 **123/126**，只有 3 首 `wide_dynamics` 被擋。
  > 沒有品檢紀錄的曲目一律排除（量測不到不可假通過）；硬要收用 `--exclude-flags ''`。
- **上架表單要填的值，一次湊齊**（`LISTING.md`）：DistroKid 與 Gumroad 都要 `genre`／`explicit`／
  **`songwriter` 真名**／**AI Credits**（2026 起強制揭露）等欄位，這些散在好幾頁表單上。
  `release` 會把值寫進 `release.json` 並同時印成 `LISTING.md`：
  ```bash
  ./scripts/lofi release package --run coastal01 --songwriter "你的本名"   # DistroKid 原創曲必填真名
  ./scripts/lofi release package --run coastal01 --ai-credits all_audio    # 本產線＝全生成（預設）
  ./scripts/lofi release package --run coastal01 --explicit                # 有歌詞且不雅才加（預設否）
  ./scripts/lofi release package --run coastal01 --price 5                 # Gumroad 定價；0＝名稱你的價錢
  ./scripts/lofi release package --run coastal01 --tags "lofi,rain,study music"
  ./scripts/lofi release package --run coastal01 --no-zip                  # 不產交付 ZIP
  ```
  預設值集中在 `config.py`（`LOFI_RELEASE_SONGWRITER`／`LOFI_RELEASE_AI_CREDITS`／
  `LOFI_RELEASE_PRICE_USD`…），可用 `./scripts/lofi config` 查。
  > **`songwriter` 一定要填真名**（DistroKid 原創曲必填、填藝名會被退）；沒填時 `LISTING.md`／`CHECKLIST.md`
  > 會標成「⚠️ 未填」逼你上架前處理。
- **genre 有兩個，別混用**：`--genre`（預設 `Lo-fi`）是**描述文案／ID3**用的；發行商**表單**要用
  `--dsp-genre`／`--dsp-subgenre`（預設 `Electronic`）。原因：**「Lo-fi」不是 DistroKid 的選項**
  （那是描述詞，不是頂層 genre；官方 help 說選最接近的）。實務分流：鋼琴／ambient → `Electronic`
  （＋ Chill 類子類）；有 boom-bap 鼓的 beat → `Hip-Hop/Rap`（＋ `Alternative`）。`LISTING.md` 會直接印出表單值。
- **`songwriter` ≠ 買家看到的 credits**：`release.json` 的 `songwriter` 是**平台必填欄位**（要真名），
  但交付 ZIP 裡的 `credits.txt` **不寫「Songwriter」**，而是「`Produced, edited and mastered by`」＋
  「`Music generated by AI`」——旋律是 AI 生成的，寫成詞曲作者等於誇大 authorship，也會跟同檔的
  AI 揭露自相矛盾。角色文案在 `release.CREDIT_ROLE`。
- **商用守門**：比照 `generate`/`visual`，模型授權未知／禁止商用的批會 **fail-closed**（`--allow-unknown-license` 可略過）。
- **可中斷續跑**：已存在的母帶會略過（`--force` 重做），所以改一首歌名只需重算那一首。
  ⚠️ 也因此**只改 artist／album 名（metadata）不會自動重算既有母帶**——ID3 會留舊值。這種情況要加 `--force`。
  ⚠️ 別忘了 `--tracks`／`--limit`：**不指定就是整個 run**（一個 run 可能有 30+ 首），
  會把策展好的專輯換成全部曲目、並把原本的母帶當孤兒刪掉（踩過）。
- **跑其他工作時請限流**：`release` 的母帶是 ffmpeg loudnorm（**純 CPU、不碰 GPU**），
  但不設限時 ffmpeg 會**吃滿所有核心**，會跟 ComfyUI 生成搶 CPU。加 `--threads 2 --nice 15`
  （或設 `LOFI_THREADS=2 LOFI_NICE=15`）就能溫和共存，比照 `make_long_lofi.sh` 的 `THREADS`/`NICE`。
  註：它**不取 GPU 鎖**——那不是 GPU 工作，沒必要互相阻擋。
- 每次產出 append 一筆事件到 `catalog/releases.jsonl`（append-only）。

### 8.3 Gumroad（唯一有 API、可全自動上架的商店）

DSP 商沒有公開 API，**Gumroad 有**（API v2），所以這條路可以真的自動化。
它消費 `release package` 的產物，不需要額外準備素材。

```bash
# 憑證（不進版控）：環境變數優先，其次 .secrets/gumroad_token.txt
printf '%s' 'YOUR_TOKEN' > .secrets/gumroad_token.txt && chmod 600 .secrets/gumroad_token.txt
export LOFI_GUMROAD_TOKEN=...        # 或用環境變數

./scripts/lofi release gumroad --run forest-1hr-01 --dry-run   # 只印會送出的參數
./scripts/lofi release gumroad --run forest-1hr-01             # 建立/更新 draft ＋上傳檔案與封面
./scripts/lofi release gumroad --run forest-1hr-01 --publish   # 檢查完再發佈
./scripts/lofi release gumroad --run forest-1hr-01 --no-upload # 只更新文案/價格
./scripts/lofi release gumroad --run forest-1hr-01 --price 5   # 指定售價（0＝pay-what-you-want）
```

實作與踩過的坑（`src/lofi/gumroad.py`）：

| 坑 | 說明 |
|---|---|
| **HTTP 200 不代表成功** | Gumroad 幾乎什麼都回 200，成敗在 body 的 `success` 欄位 → 程式一律讀 `success`（讀狀態碼會把失敗當成功） |
| **建立一定是 draft** | `POST /v2/products` 只建 draft，發佈要另外 `PUT .../enable` |
| **封面／縮圖只能後補** | `/covers`、`/thumbnail` 端點，建立商品時帶不進去 |
| **列表一頁只回 10 筆** | 其餘在 `next_page_url` → 判斷「是否已上架」必須翻頁，否則會重複建立商品 |
| **檔案要 multipart 上傳** | `POST /files/presign`（要 `filename`/`content_type`/`file_size`）→ 逐 part PUT（收 ETag）→ `POST /files/complete`（要 `upload_id`/`key`/`parts`）→ 把回傳 URL 放進 `files[][url]` |
| **發佈會被帳號狀態擋** | 沒完成 payout settings 或 email 未確認 → 只停 draft 並回 `warning`（程式照實印，不假裝成功） |
| **封面／縮圖 API 吃不到** | `/covers` 只收**公開可讀**的圖片 URL 或 `signed_blob_id`（ActiveStorage 簽章 id）。我們的 presign 是私有 S3（GET 會 403）也拿不到 signed id → **封面與縮圖要在 dashboard 手動上傳**（一張 10 秒）；若有公開圖床可用 `--cover-url` |
| **描述要送 HTML** | Gumroad 商品頁吃 HTML：`description` 預設送 `listing.html`（有 `<h3>`／`<ul>`／`<ol>` 才會排版）。純文字會變成一堆換行。要退回純文字用 `--text-description` |

- 商品 id／URL 會 append 一筆 `event="gumroad"` 到 `catalog/releases.jsonl`（append-only）。
- 重複跑**不會重複建立**：先用 `custom_permalink` 比對既有商品，有的話走更新。
- **上傳快取看內容（sha1），不是 mtime**：`release package` 每次都會重建交付 ZIP（成員與日期固定 →
  內容一樣），所以「只改文案／描述」再跑 `release gumroad` **不會重傳數百 MB**（`logs/.gumroad-uploads.json`）。
- 改**內嵌標籤**（母帶的 ID3）例外——那要 `release package --force` 重算母帶，ZIP 內容才會真的變。
- ⚠️ 上架文案（`release.json` 的 `listing`）**一律英文**（Gumroad/DSP 是英文店面）；中文說明在 `CHECKLIST.md`。

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
| 第二個生成一啟動就中止 | 生成互斥鎖（正常保護） | 等前一個跑完；要排隊 `LOFI_LOCK_WAIT=300 ./scripts/lofi ...`；確認沒在跑卻被檔可 `LOFI_NO_LOCK=1` |
| 換了 checkpoint 但行為沒變 | 仍在用集中設定裡的預設 | `./scripts/lofi config` 看目前值；改 `src/lofi/config.py` 或設 `LOFI_IMAGE_CKPT` |
| 生成前就中止「磁碟空間不足」 | Python 端 preflight（正常保護） | 清理 `./scripts/cleanup_outputs.sh --apply`；或調低門檻 `LOFI_MIN_FREE_GB=10` |
| 品檢出現 `❓ error` | ffmpeg/ffprobe 缺失或檔案壞 | 確認 `ffmpeg -version`；該曲重測或刪除 |
| 目錄旁多了 `*.json.corrupt-*` | sidecar JSON 壞損被隔離（正常保護） | 檢查該檔；原檔已保留，必要時手動救回後刪除 |

---

## 附錄：發布前合規檢查表

- [ ] 使用可商用授權的模型（本流程以 ACE-Step 為主）
- [ ] 跑 `./scripts/lofi license check` 確認用到的模型皆可商用（未知／NC 需先處理；見 `docs/LICENSING.md`）
- [ ] 上傳勾選 AI 揭露
- [ ] 視覺原創、每支片不同
- [ ] 影片有 chapters / 曲目 credits（策展痕跡）
- [ ] 不是同一模板換首歌的大量上傳
- [ ] 音訊響度約 -14 LUFS
- [ ] 標題與描述有實質內容，不是純關鍵字堆砌
