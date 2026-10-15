# 成品配置範本（RECIPES）

量產時直接照抄的「配方」。數值分兩種：

- **✅ 實測**：本機跑過、有數據。
- **🟡 起點**：程式目前預設值，安全可用，可再微調。

> 換風格只需改 `STYLE`；換視覺分區要對準你的圖（座標是 0~1 比例）。

---

## 0. 實測基準（Mac M4 / 16GB）

| 項目 | 值 |
|---|---|
| 生成速度 | 約 **4–6 分鐘 / 首**（120s） |
| 音檔大小 | 約 **3.5–3.7 MB / 首**（MP3 V0, 120s）→ 1000 首 ≈ 3.7GB |
| 影片大小 | 約 **37 MB/分鐘** @9Mbps；`--vbitrate 5` 約砍半（≈19MB/分） |
| 生圖速度 | 約 **2.5 分鐘 / 張**（SDXL / DreamShaperXL） |
| 響度 | mix 後 **-14 LUFS / -1 dBTP**（`build_long_mix` 自動） |
| ACE-Step 特性 | 輸出**普遍偏燙**（真峰值常 >0dB）→ 品檢只列 warn，mix 階段正規化修掉 |

---

## 1. 音訊生成錨定參數（ACE-Step 1.5 turbo）

✅ 實測可用、🟡 為預設。16GB Mac 建議不要動模型。

| 參數 | 值 |
|---|---|
| DiT | `acestep_v1.5_turbo`（2B） |
| Text encoder | `qwen_0.6b_ace15` + `qwen_1.7b_ace15` |
| VAE | `ace_1.5_vae` |
| 🟡 `--duration` / `--duration-range` | `120`；`--duration-range 120 150`＝每首在 **2:00–2:30** 隨機（去機械感）。產線（`make_episode`）預設就帶 `120 150` |
| 🟡 `--quality` | `fast`（預設）＝原設定 `steps 8 / temp 0.85 / cfg 2.0` |
| 🟡 `--steps` | 依 `--quality`（`balanced`→10、`stable`→12；要更穩可 12+，較慢） |
| 🟡 `--cfg-scale` | `2.0`（三個預設都相同） |
| 🟡 `--temperature` | 依 `--quality`（`balanced`→0.82、`stable`→0.8，越穩越低） |
| 🟡 `--generate-audio-codes` | 純文字生成＝**開**（提升音質、較慢）；`--init-audio`（audio2audio）＝自動**關** |
| 輸出 | MP3 V0 |

> **品質預設**（`--quality`，一次調 steps＋temperature）：
> `fast`＝原設定（速度優先，與舊行為完全相同）；`balanced`／`stable`＝用時間換穩定度。
> 個別 `--steps` / `--temperature` / `--cfg-scale` 有給就蓋過預設。實測 `stable`（12 步）比 `fast` 慢約 5 成。
> **負面 caption（已生效）**：ACE-Step 的內建節點沒有 negative 輸入，本專案自建一個
> `LofiAceStepNegativeEncode`（原始碼 `scripts/comfy_nodes/lofi_ace_negative.py`，安裝到
> `~/ComfyUI/custom_nodes/lofi_ace_negative/`，**裝完要重啟 ComfyUI**）把 `caption_negative`
> 傳進 ACE-Step 的 LLM 語音碼階段做 CFG 對比。`lofi generate` **預設就用每列的 `negative`**
> （`--negative` 覆寫全部、`--no-negative` 關閉）。
> 注意：它只影響「LLM 語意音碼」那段，**不是**擴散取樣的負面條件。

> **音訊參考有兩種機制（互斥，檔名都需在 `ComfyUI/input/`）**
>
> | 旗標 | 底層 | 行為 |
> |---|---|---|
> | `--init-audio X --init-denoise d` | 參考音 latent 當 KSampler 的起點（audio2audio） | `d` 越低越貼近參考（0.3–0.5 旋律明顯跟著走；0.75–0.9 只留骨架） |
> | `--ref-timbre X` | `ReferenceTimbreAudio` 掛在 conditioning（**cover 模式**，底層設 `is_covers=True`） | 維持空 latent、步數全開，可自由重構；會**忽略 LLM 音碼**（故自動關掉以省時） |
>
> 兩者只差一個「起點」概念：audio2audio 從參考出發，cover 則是「重做」參考。參考受版權保護的
> 音訊有衍生風險，**建議只拿自己生成的曲子當參考**（實測也是拿自家曲做的）。
>
> ✅ 實測（同 prompt/seed 對照，Mac M4/16GB）：參考模式因跳過 LLM 音碼，約 **95–104 秒/首**，
> 純文字生成是 **267 秒**——**約快 2.5 倍**。（`qc` 的能量指紋分不出兩者好壞，得靠人耳。）

```bash
./scripts/lofi generate --csv prompts/generated/rainy_lofi.csv --limit 3 --quality stable --clean-raw
```

### 1.1 混音母帶鏈（`build_long_mix.sh --master`）

交叉淡入串成長片時，舊行為（`--master none`，預設）**只對整段混音跑一次 `loudnorm`**：不修逐軌響度差、不碰音色。
`--master warm` 多了兩層（純 ffmpeg、無 GPU）：

1. **逐軌 `loudnorm`（-18 LUFS / -1.5 dBTP）**：對齊每首音量。ACE-Step 各曲 LRA 差很大（實測 3~12 LU），不修的話長片裡會忽大忽小。
2. **bus 音色鏈**：`highpass 28Hz`（清 subsonic）→ 一點低頻暖度（`bass`）→ 收刺耳高頻（`treble -1.5dB@9k`）→ 溫和 glue 壓縮（`acompressor` 2:1）→ 立體寬度（`extrastereo`）→ 軟削頂（`asoftclip`）。最後仍走原本的 two-pass `loudnorm` 到 -14 LUFS / -1 dBTP。

✅ 實測（5 首 nujabes 測試曲、9.5 分長片，兩者都精準 -14.0 LUFS）：

| 模式 | 整體 LRA |
|---|---|
| `none` | 6.3 LU |
| `warm` | **4.0 LU**（響度更一致） |

```bash
./scripts/build_long_mix.sh --master warm out/mix.wav 8 assets/tracks/a.mp3 assets/tracks/b.mp3
```

- `make_long_lofi.sh` 也吃 `--master`（或用環境變數 `LOFI_MIX_MASTER`）。
- 預設是 `none`（**與舊行為完全相同**）；聽過 A/B 覺得好再改預設。

---

## 2. 影片長度配方

每首 120s、crossfade 8s → 第 N 首結束時間 = `112×(N-1) + 120`。

> 用 `--duration-range`（預設 120–150）時每首長度不一，上式改取**平均**：平均 135s → 每首貢獻
> ≈ `135-8 = 127s`。反正 `make_long_lofi` 最後會把混音**裁到目標長度**，長度充裕即可，不必剛好。

| 目標長度 | 軌數 | `--minutes` | `--vbitrate` | 預估大小 | `--loop` |
|---|---|---|---|---|---|
| 10 分 | 6 | 10 | 9（或 5） | ≈370MB（5→190MB） | 20 |
| 30 分 | **16** | 30 | 9（或 5） | ≈1.1GB（5→0.55GB） | 20 |
| 60 分 | **32** | 60 | 9（或 5） | ≈2.2GB（5→1.1GB） | 20 |

> `--loop` 必須整除總長（1800/20=90、3600/20=180）。
> 想省電：`--no-video-fade`、`--vbitrate 5`、`THREADS=4 NICE=15`。
> 影片音源預設優先用**無損母帶**（`--master-source auto`）；中間檔可 `--stage-local` 放內接做（見 `RUNBOOK.md` §5.1）。

---

## 3. 視覺配方（Cinemagraph）

`make_cinemagraph.py` 預設**只開顆粒＋靜態調色**（畫面其餘完全靜止），最保險。
要局部微動再逐一開啟，並把分區指到圖上位置。

**基礎層（🟡 預設，建議保留）**
```bash
--grain .012 --vignette .35 --contrast 1.04 --saturation 1.08
```

**場景 → 建議參數（🟡 起點，分區要對準）**

| 場景 | 建議參數 | 說明 |
|---|---|---|
| 夜晚書桌（預設圖） | `--glow 0.5 --lamp 0.42,0.27,0.22 --sway 0.012 --plant 0.18,0.10,0.32,0.34 --dust 0.5` | 檯燈呼吸＋盆栽微晃 |
| 雨夜窗前 | `--glow 0.4 --lamp 0.88,0.48,0.22 --drops 0.6 --dust 0.4`（`--window` 對準窗） | 玻璃水珠滑落；**別開 `--rain`**（易看成室內下雨） |
| 城市夜景 | `--glow 0.3 --carlight 0.5 --window <x0,y0,x1,y1> --temp 0.02` | 車燈掃過窗面 |
| 森林黃昏 | `--glow 0.35 --dust 0.6 --sheen 0.2 --temp 0.02` | 光束中的塵埃、柔光 |
| 海岸黃昏 | `--zoom 0 --drift 0.004 --temp 0.02 --sheen 0.15` | 極慢飄移，海面光感 |

> 所有時變項都保證頭尾無縫；換圖務必先 `--check-loop` 驗證，並`--help` 看完整參數。
> 強度設 `0` 即關閉該效果。

---

## 3.1 角色鎖定與修圖（吉祥物 Moni）

主角＝黑貓 Moni（純黑＋四腳白襪＋大金眼）。設計單一來源是 `prompts/character/moni.json`，
參考圖＝ `ref04_01_gold-socks.png`（複本放 `~/ComfyUI/input/mascot_blackcat_ref.png`）。

**IPAdapter `--weight` 是「參考圖 vs prompt 誰說話大聲」的旋鈕**（不是相似度）：

| 場合 | 值 | 實測結果 |
|---|---|---|
| 生新圖、要角色像 | `0.60` | 角色像、場景還能自由。代價＝偶爾長白胸 |
| 生新圖、特徵更硬 | `0.65–0.75` | 0.65 開始掉白襪；0.75 連構圖與筆觸都被拉進來（風格變硬） |
| inpaint **修掉多餘的白** | `0.30–0.40` | 0.6 時參考圖贏（白留著）、0.35 時 prompt 贏（白消失） |

> 機制：注入的是**整張參考圖的統計特徵**，不是「只取黑貓」。參考圖唯一的白色（白襪）在高權重時
> 會被擴散到胸口 → 白胸不是隨機，是參考圖的白色元素被放大。
> 原則：**要「像參考圖」→ 高；要「照 prompt 改掉某個特徵」→ 低。**

> **為什麼白襪與白胸分不開（2026-10-13 實測 `sc07`，三張只差一個變數）**
>
> | 變數 | 白襪 | 白胸 |
> |---|---|---|
> | 原 prompt ＋ `--ref` | ✅ 有 | ❌ 大片 |
> | 原 prompt、**不帶** `--ref` | ❌ **完全沒有** | ✅ 沒有（全黑） |
> | 白詞降權的 prompt ＋ `--ref` | ⚠️ 變弱 | ⚠️ 變淡但仍在 |
>
> 白襪與白胸是**同一個來自參考圖的白色訊號**：拿掉參考圖連襪子都長不出來（prompt 單獨叫不出白襪），
> 降白詞則兩者一起變弱 → **權重無法只留襪子、不留胸**。
> 這就是「必須訓練 LoRA」的根本原因（LoRA 才學得到「白只長在腳上」）。
> 訓練前的對策：多生幾張人工挑（實測對設計的比例約 3–4 成），或對「只差白胸」的圖用 inpaint 修
> （`--weight 0.30–0.40` 單獨重畫胸口，見上面流程）。

**修圖流程（遮罩 → 預覽校正 → inpaint）**

```bash
# 1) 由矩形規格產生遮罩（黑底、白＝要重畫）；--preview 疊半透明紅用來校正框位
python3 scratch/_tmp_make_mask.py --base assets/visuals/ds03/<圖>.png \
  --rect x1,y1,x2,y2 --rect x1,y1,x2,y2 --grow 4 --preview
# 2) 只重畫遮罩區；低權重＝讓 prompt 壓過參考圖，把漂掉的特徵改回設計
./scripts/lofi visual --init-image <圖>.png --mask <圖>-mask.png \
  --ref mascot_blackcat_ref.png --weight 0.35 --run-id fix03 --slug fix-xxx
```

- **框位要收在貓輪廓內**：矩形跨到背景時，重繪會把毛畫進背景（貓看起來變胖）。
- **眼睛是禁區**：框到眼睛會把它們整片重畫掉。
- **白襪要保留**：框位別蓋到腳掌，prompt 留著 `(clearly white paws:1.3)`。
- **灰斑不要硬修**：大遮罩重繪時，prompt 裡的 `(clearly white paws:1.3)`／`(white socks on all four feet:1.2)`
  這些**正向白詞會把白畫進重繪區** → 灰斑被修成白斑，比原圖更糟（實測 `ds03_16`、`ds03_06` 兩張都退步、
  `ds03_07` 幾乎沒差）。真的要修灰斑：**prompt 不要出現 white**（只留 `pure black fur` ＋風格詞）、
  遮罩盡量小、或降低 `--denoise`（讓模型保留多一點原圖）。
- **大遮罩風險高**：矩形跨出貓輪廓時，重繪會把毛畫進背景（貓看起來變胖）；寧可拆成幾個小框。
- 命名：修圖產物用 `fix-` 字首、對照合成用 `cmp-`（見 `docs/NAMING.md`）。
- **同一個 run 別混不同參數**：manifest 的 `config` 是 run 級、後寫會蓋掉前值；真的混用時，
  把每張的真實值記在逐檔 `images[].weight`（並用 `status: chosen|superseded|rejected` 標出選定版本）。

---

## 3.2 模型分工（SDXL vs Z-Image GGUF）

| 要什麼 | 用哪顆 | 實測結果 |
|---|---|---|
| 場景豐富、風格多變（咖啡店、代針筆、季節） | **Z-Image Turbo GGUF**（自然語言 prompt、8 步、cfg 1.0、約 225 秒） | 咖啡店的吧台／磨豆機／黑板菜單／吊燈／玻璃罐都到位；**代針筆線條真的畫得出來**（SDXL 只會給水彩） |
| 用參考圖鎖住固定角色（IPAdapter）／之後掛 SDXL LoRA | **DreamShaperXL**（`--ref`） | 角色像，但場景詞容易被吃掉、白襪與白胸分不開（機制見 §3.1） |

**反例（別做）**：拿 Z-Image 生好的場景，再用 SDXL inpaint 把角色換成 IP 主角 →
遮罩外逐像素保留（機制沒錯），但**重畫的角色品質低於 Z-Image 原圖**，實測出現灰斑與破圖
（`sc14` 兩張都被判破圖）＝模型之間的美感對撞。

**正確做法**：要 Z-Image 的畫質就直接用 Z-Image 畫角色——它的 prompt 忠實度本身就能畫出
「純黑＋大金眼＋四腳白襪」（`sc12`／`sc13` 都做到了，而這正是 SDXL 單靠 prompt 做不到的事）。
角色「身分」一致性要另外靠 **Z-Image 自己的 LoRA**，而不是跨模型硬換。

---

## 4. 上片資訊配方

- **標題**：`[詩意短句] | [曲風描述]`（範本在 `prompts/youtube/<style>.json` 的 `title_pool` / `descriptor_pool`）
- **描述**：詩意開場 → `This … mix features …` → `🎧` 要點 → 沉浸段 → `☕` → `💬` → `🔔` → **Tracklist（章節）** → hashtags
- **章節**：由 `--tracks` + `--xfade` 推算；曲名取自 catalog 側錄的 `mood`
- **tags**：約 20 個；**hashtags**：約 20 個（都存在 youtube 範本裡）

已附 youtube 範本：`rainy_lofi`、`cozy_morning`、`tokyo_night`、`dusk_jazz`、`coastal_guitar`、`forest_piano`。
其他風格會用通用模板（可自行複製一份到 `prompts/youtube/`）。

---

## 5. 一集（End-to-End）範本 — 直接複製

> 最省事：五段一條龍交給 orchestrator（音樂→圖片→影片→上傳→整理）
> ```bash
> ./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40 --limit 16
> # 也可分段：--stage music | image | video | upload | cleanup（詳見 RUNBOOK 6.6）
> ```
> 下面是想手動控制每一步時的完整版。

```bash
STYLE=rainy_lofi
RUN="${STYLE}-$(date +%m%d)"
EP="${STYLE}-$(date +%m%d-%H%M)"   # episode 名稱（一集一包）
MIN=30          # 影片長度（分）；30→16 軌、60→32 軌

# 1) 展開清單（先多生一些，之後挑 keeper）
./scripts/lofi expand --style "$STYLE" --count 40 --seed 42

# 2) 生成（可續傳：中斷後用同一個 --run-id 重跑）
./scripts/batch_run.sh --csv "prompts/generated/$STYLE.csv" \
    --chunk 20 --sleep 60 --run-id "$RUN" --clean-raw

# 3) 品檢 + 建圖書館
./scripts/lofi qc --all --index
./scripts/lofi library build
./scripts/lofi library summary

# 4) 挑 keeper（沒有滿意的就用全部）
./scripts/lofi library set-status keep --verdict pass --min-score 90
TRACKS=$(./scripts/lofi library query --style "$STYLE" --status keep --limit 32 --paths)
[ -z "$TRACKS" ] && TRACKS=$(./scripts/lofi library query --style "$STYLE" --limit 32 --paths)

# 5) 生圖 + 合成影片（--style 會自動產生上片資訊；--episode 一集一包）
./scripts/make_long_lofi.sh --generate --minutes "$MIN" --vbitrate 9 \
    --tracks $TRACKS --xfade 8 --style "$STYLE" --episode "$EP"
# → output/episodes/$EP/{video.mp4, mix.wav, visual_loop.mp4}、publish/$EP.json

# 6) 上片佇列
./scripts/lofi publish --ready
# ./scripts/lofi publish --mark-uploaded "$EP" --url https://youtu.be/xxxx
```

> 若已經有喜歡的場景圖，把 `--generate` 換成 `--image assets/visuals/<圖>.png`（完全不開 ComfyUI，最省電）。

---

## 6. 各風格卡片

| 風格 | BPM | 情緒 | YT 範本 | 建議視覺 |
|---|---|---|---|---|
| `rainy_lofi` | 64–82 | 雨夜讀書 | ✅ | 雨夜窗前 |
| `cozy_morning` | 74–88 | 暖陽晨間 | ✅ | 早晨書桌 |
| `tokyo_night` | 72–84 | 東京夜景 | ✅ | 城市夜景 |
| `dusk_jazz` | 62–78 | 黃昏爵士（比照本頻道既有影片） | ✅ | 夜晚書桌／lounge |
| `coastal_guitar` | 66–82 | 海岸木吉他 | ✅ | 海岸黃昏 |
| `forest_piano` | 60–76 | 森林黃昏鋼琴 | ✅ | 森林黃昏 |
| `jazzhop_lounge` | 60–74 | 煙霧 lounge | — | 酒吧夜景 |
| `focus_minimal` | 78–90 | 專注極簡 | — | 簡約書桌 |
| `rnb_soul` | 60–78 | 醇厚 R&B | — | 夜晚房間 |
| `meditation_ambient` | 40–56 | 冥想氛圍 | — | 極簡自然 |
| `cozy_morning_v2` | 62–76 | 晨間 v2 | — | 早晨窗邊 |

**單一風格快速出片：**
```bash
STYLE=dusk_jazz
./scripts/lofi expand --style "$STYLE" --count 40 --seed 42
./scripts/batch_run.sh --csv "prompts/generated/$STYLE.csv" --chunk 20 --sleep 60 --run-id "$STYLE-01" --clean-raw
./scripts/lofi qc --all --index && ./scripts/lofi library build
./scripts/make_long_lofi.sh --generate --minutes 30 --style "$STYLE" \
    --episode "$STYLE-$(date +%m%d)" \
    --tracks $(./scripts/lofi library query --style "$STYLE" --limit 16 --paths)
```

---

## 7. 常見坑（量產前掃一眼）

- **ACE-Step 偏燙** → 品檢 `hot_true_peak` 是正常的；mix 會正規化。只有 `fail`（無聲/截斷/嚴重削波）才需理會。
- **檔名唯一** → 一定帶 `--run-id`；同 run-id 重跑＝續傳，不會重複生成或覆蓋。
- **曲目佈局** → `assets/tracks/<style>/<run_id>/`；選曲優先用 `library query --paths`，別用扁平 glob。
- **外接碟會掉線** → 寫影片/清理前先 `./scripts/disk_report.sh`；log 在內接 `logs/`。
- **風格版本** → 改 `prompts/styles/*.json` 記得 `version` +1。
- **發布合規** → 每支片勾 YouTube **AI 揭露**；不同集換順序/視覺/標題，別同模板換歌。

詳見 [`RUNBOOK.md`](RUNBOOK.md) 與 [`../README.md`](../README.md)。
