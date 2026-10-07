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
| 生圖速度 | 約 **40 秒 / 張**（SD1.5 / MeinaMix, 768x512） |
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
| 🟡 `--duration` | `120`（安全區） |
| 🟡 `--steps` | `8`（要更穩可 10–12，較慢） |
| 🟡 `--cfg-scale` | `2.0` |
| 🟡 `--temperature` | `0.85`（想更穩用 `0.8`） |
| 輸出 | MP3 V0 |

```bash
python3 scripts/batch_generate.py --csv prompts/generated/rainy_lofi.csv --limit 3 --clean-raw
```

---

## 2. 影片長度配方

每首 120s、crossfade 8s → 第 N 首結束時間 = `112×(N-1) + 120`。

| 目標長度 | 軌數 | `--minutes` | `--vbitrate` | 預估大小 | `--loop` |
|---|---|---|---|---|---|
| 10 分 | 6 | 10 | 9（或 5） | ≈370MB（5→190MB） | 20 |
| 30 分 | **16** | 30 | 9（或 5） | ≈1.1GB（5→0.55GB） | 20 |
| 60 分 | **32** | 60 | 9（或 5） | ≈2.2GB（5→1.1GB） | 20 |

> `--loop` 必須整除總長（1800/20=90、3600/20=180）。
> 想省電：`--no-video-fade`、`--vbitrate 5`、`THREADS=4 NICE=15`。

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
python3 scripts/expand_style.py --style "$STYLE" --count 40 --seed 42

# 2) 生成（可續傳：中斷後用同一個 --run-id 重跑）
./scripts/batch_run.sh --csv "prompts/generated/$STYLE.csv" \
    --chunk 20 --sleep 60 --run-id "$RUN" --clean-raw

# 3) 品檢 + 建圖書館
python3 scripts/auto_qc.py --all --index
python3 scripts/library.py build
python3 scripts/library.py summary

# 4) 挑 keeper（沒有滿意的就用全部）
python3 scripts/library.py set-status keep --verdict pass --min-score 90
TRACKS=$(python3 scripts/library.py query --style "$STYLE" --status keep --limit 32 --paths)
[ -z "$TRACKS" ] && TRACKS=$(python3 scripts/library.py query --style "$STYLE" --limit 32 --paths)

# 5) 生圖 + 合成影片（--style 會自動產生上片資訊；--episode 一集一包）
./scripts/make_long_lofi.sh --generate --minutes "$MIN" --vbitrate 9 \
    --tracks $TRACKS --xfade 8 --style "$STYLE" --episode "$EP"
# → output/episodes/$EP/{video.mp4, mix.wav, visual_loop.mp4}、publish/$EP.json

# 6) 上片佇列
python3 scripts/upload_status.py --ready
# python3 scripts/upload_status.py --mark-uploaded "$EP" --url https://youtu.be/xxxx
```

> 若已經有喜歡的場景圖，把 `--generate` 換成 `--image assets/visuals/<圖>.png`（完全不開 ComfyUI，最省電）。

---

## 6. 各風格卡片

| 風格 | BPM | 情緒 | YT 範本 | 建議視覺 |
|---|---|---|---|---|
| `rainy_lofi` | 64–82 | 雨夜讀書 | ✅ | 雨夜窗前 |
| `cozy_morning` | 74–88 | 暖陽晨間 | ✅ | 早晨書桌 |
| `tokyo_night` | 72–84 | 東京夜景 | ✅ | 城市夜景 |
| `dusk_jazz` | 62–78 | 黃昏爵士（參考 Drifted LoFi） | ✅ | 夜晚書桌／lounge |
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
python3 scripts/expand_style.py --style "$STYLE" --count 40 --seed 42
./scripts/batch_run.sh --csv "prompts/generated/$STYLE.csv" --chunk 20 --sleep 60 --run-id "$STYLE-01" --clean-raw
python3 scripts/auto_qc.py --all --index && python3 scripts/library.py build
./scripts/make_long_lofi.sh --generate --minutes 30 --style "$STYLE" \
    --episode "$STYLE-$(date +%m%d)" \
    --tracks $(python3 scripts/library.py query --style "$STYLE" --limit 16 --paths)
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
