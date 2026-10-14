# 多平台上架與變現（DISTRIBUTION）

本檔是**規劃**：目前產線只有一個出口（YouTube），目標是把同一批素材變成多平台、可重複計價的資產。
授權前提見 [`LICENSING.md`](LICENSING.md)；命名見 [`NAMING.md`](NAMING.md)。
實作進度用 ✅（已完成）／🚧（進行中）／⬜（未開工）標記，動工前先確認 §6 的三個決策。

> 原則：**能用官方 API 的才進產線；沒有 API 的，產線負責「生出上架包」，人只負責拖檔與填表。**

---

## 1. 現況診斷

| 素材 | 數量 | 目前用途 |
|---|---|---|
| 單曲 | **126 首**（`assets/tracks/<style>/<run>/`，11 種風格） | 只當長片原料 |
| 場景圖 | 16 個 run（`assets/visuals/`） | 只當影片背景 |
| 成片 | **2 集**（`output/episodes/`） | 上 YouTube |
| publish metadata | 3 筆 | 上 YouTube |
| **出路** | **1 個（YouTube）** | — |

瓶頸不是產能，是**出口只有一個**，而且 126 首可獨立上架的單曲被消耗掉了。
`assets/tracks/<style>/<run>/` 這個結構**本身就是一張專輯**（`coastal01` = 12 首），缺的只是「run → release」這一段。

### 產品線與變現方式

| 產品 | 單位 | 變現 | 現況 |
|---|---|---|---|
| 長片 | 集 | YT 廣告、觀看時數 | ✅ 已自動化 |
| **單曲／專輯** | run → album | DSP 串流分潤、Content ID、Bandcamp | 🚧 路線 A（`lofi release` 已可用） |
| **直播** | 頻道 | 觀看時數、Super Chat | ⬜ 路線 B |
| **素材／授權** | 曲 | stock/sync 授權、sample pack、stems | ⬜ 路線 C |

---

## 2. 自動化天花板（先講清楚，避免做白工）

| 平台類型 | 有無官方 API | 自動化的正確形狀 |
|---|---|---|
| YouTube 全家桶（上傳／Shorts／Analytics） | ✅ 有 | 進產線，全自動 |
| IG Reels | ✅ Graph API（需 Business＋公開影片 URL） | 進產線，全自動 |
| TikTok | ✅ 有，但**未通過 audit 只能發 `SELF_ONLY`** | 產「待發草稿」＋人進去按發布 |
| Internet Archive | ✅ S3-like API | 進產線 |
| 自有商店（Stripe／靜態頁） | ✅ | 進產線 |
| Gumroad | ✅ API v2 | 進產線 |
| RTMP 直播（YouTube／Twitch） | 無 API，純 `ffmpeg` | 進產線（本來就不用 API） |
| **DSP 串流（Spotify/Apple/…）** | ❌ DistroKid / Amuse / TuneCore / RouteNote / CD Baby **皆無公開 API** | **產上架包**，人拖檔 |
| Bandcamp / SoundCloud | ❌ 無（SoundCloud 上傳權限實務上已關） | 產上架包 |
| stock／sync（Pond5、AudioJungle…） | ❌ 無 | 產上架包 |
| Patreon / Ko-fi | 部分（webhook／shop） | 產上架包 |

> 唯一能「程式化送 DSP」的是 **label 級 distribution API**（LabelGrid、Revelator，走 DDEX ERN 3.8.2/4.3.2）。
> 那等於自己當 label：要付費、有品質門檻、且你承接平台 AI 政策風險。列為天花板，現階段不做（見 §8）。

---

## 3. 路線 A：串流上架包（release package）

**一次解鎖 DSP ＋ Bandcamp ＋ stock 三條路**，且重複執行邊際成本近零。優先做這個。

### 3.1 產物設計（🚧 已實作，`lofi release`）

```bash
./scripts/lofi release plan    --run coastal01                    # 只印，不寫檔
./scripts/lofi release package --run coastal01 --limit 12
./scripts/lofi release package --run forest-1hr-01 --tracks 32,10,4,7,36,11 --album "Into the Forest"
```

> **一個 run 的 `mood` 通常只有 5–6 種**，所以「取前 12 首」會滿是 `(pt. 2)`。實務做法是用 `--tracks`
> **策展**（挑一組），再把重複的曲名改成有敘事的名字——改 `release.json` 的 `tracks[].title` 後重跑即可。

```
output/releases/coastal01/
├── audio/                01_slow-tide.wav …（-14 LUFS / -1 dBTP，ID3 已填）
├── cover_3000x3000.jpg   封面（無文字，見 §6-1）
├── download/<album>.zip  交付檔（audio/＋封面＋tracklist＋credits）→ Gumroad／Bandcamp 的「內容」
├── release.json          機器用：album/artist/track 順序/genre/explicit/songwriter/AI credits/ISRC 欄/授權 provenance/母帶參數
├── tracklist.csv         人用：No, Title, Duration, Key, BPM, Mood, ISRC(new)
├── LISTING.md            上架欄位對照：DistroKid／Gumroad 要填的值（複製貼上）＋長版文案/tags
├── credits.txt           隨交付檔附上的 AI 揭露與模型授權（賣點，見 §5）
└── CHECKLIST.md          各平台上架步驟（DSP／Bandcamp／stock／Gumroad 各一段）
```

實作時發現、原規劃沒預期到的幾點（已處理）：

- **取樣率必須明確指定**：`loudnorm` 內部會升到 192kHz 處理，不帶 `-ar` 時偶發輸出 192kHz 檔
  （同批不同檔還會不一致）。已改為量測來源取樣率並原樣輸出。
- **一個 run 可能有 30+ 首**（`coastal01` 有 34 首）→ 加了 `--limit` / `--offset` 挑一組。
- **曲名會大量重複**（只有 6 種 mood）→ 自動加 `(pt. N)`，並支援「改 `release.json` 再重跑」沿用人工曲名。
- **改曲名重跑會留孤兒母帶** → 已自動刪除改名前那首舊檔（避免上架時多看到一首不存在的歌）。
- **已存在的母帶會略過**（`--force` 重做），所以中斷可續跑、改一首只重算一首。
- **母帶來源自動分級**：若該曲有無損母帶（`output/masters/`），`release` 會**優先採用**，並在
  `release.json` 記 `master_source`、逐首記 `lossless` 與 `master.source`。所以同一張專輯可以「部分無損、部分有損」，
  而且看得出來哪些是哪些。

### 3.3 兩層存放：為什麼無損母帶不能放內接

實測（48kHz 立體、120 秒）：**mp3 3.6MB vs FLAC 30.8MB（8.6 倍）**。內接碟只有 228GB：

| 規模 | 工作 mp3（內接） | FLAC（若放內接） |
|---|---|---|
| 126 首（現況） | 0.44 GB | ~2.5–3.9 GB |
| 1000 首 | 3.6 GB | **~20–31 GB** |

加上 ComfyUI 原始輸出也得先落內接，內接會很快見底。所以：

| 位置 | 內容 | 用途 |
|---|---|---|
| `assets/tracks/<style>/<run>/*.mp3`（**內接**） | 工作檔 | 品檢／圖書館／混音／長片（流程完全不變） |
| `output/masters/<style>/<run>/*.flac`（**外接**） | 無損母帶 | 只給 `release package` 當來源 |

`--format flac` 的實際流程：ACE-Step 存 FLAC（ComfyUI 內接暫存）→ 複製到外接母帶夾 →
ffmpeg 轉一份 mp3 進工作目錄 → `--clean-raw` 刪掉 ComfyUI 原始檔。

> 註：ComfyUI 的 `SaveAudio` 節點已被官方標記 `(DEPRECATED)`（新版是 `SaveAudioAdvanced`，但其
> `format` 是 dict 型輸入）。目前用舊節點（簽章已驗證可用），若哪天被移除，改節點時**輸入格式要一起改**。

### 3.2 缺口（要新增的程式）

| 缺口 | 現況 | 狀態 |
|---|---|---|
| **單曲級 metadata** | 原本 `meta.py` 只有「一集」為單位 | ✅ `release.py` 以 sidecar 為來源產生 track-level |
| **唯一曲名** | ⚠️ 章節標題會**重複** | ✅ 專輯內自動去重（`(pt. N)`）＋可人工改名重跑 |
| **無損母帶** | 舊曲只有 mp3 | ✅ 新曲 `generate --format flac`（母帶放外接 `output/masters`）；舊曲 loudnorm 正規化（§6-3） |
| **封面** | 只有場景圖（非方圖） | ✅ ffmpeg 置中裁切＋放大成 3000×3000；**不疊字** |
| **release 記錄** | 無 | ✅ `catalog/releases.jsonl`（append-only、只記事件） |

> 不要另開一套 metadata 來源：曲名/調性/BPM/mood 一律從 sidecar（`assets/tracks/<style>/<run>/*.json`）取，
> 商用 provenance 沿用 `release.collect_credits()`（與 `meta.track_model_credit()` 同邏輯）。

---

## 4. 路線 B：24/7 直播

最低人力換觀看時數。無 API，純 `ffmpeg` → RTMP。

```bash
# 概念：一支無縫 loop 掛著推流
./scripts/lofi live --loop output/episodes/coastal-1hr-01/visual_loop.mp4 \
    --mix output/episodes/coastal-1hr-01/mix.wav --key env:YT_STREAM_KEY
```

- 素材直接複用 `output/episodes/<episode>/`（`video.mp4`／`mix.wav`／`visual_loop.mp4`）——**零額外生成成本**。
- stream key 用環境變數，不進版控（比照 `.secrets/` 慣例）。
- 斷線自動重連、`-stream_loop -1`、音訊 AAC 128k、影像 `-c:v libx264 -preset veryfast`。
- ⚠️ **政策風險最高的一條**：YouTube 的 inauthentic content 是**頻道層級**偵測，24/7 單一模板最容易踩雷。
  必須輪播多支 loop、定期換視覺、標題/描述有實質內容。詳見 §8。

---

## 5. 路線 C：素材授權

單價最高，但人工審核重。**產線的責任是生出「可賣的包」，不是自動上架。**

| 產品 | 來源 | 上架處 | 自動化 |
|---|---|---|---|
| 單曲授權（sync/stock） | 126 首 | Pond5、AudioJungle、Songtradr、Soundstripe；Artlist/Musicbed/Epidemic 需邀請或策展 | 產包＋清單 |
| 無損單曲／專輯下載 | run | Bandcamp、Gumroad（有 API ✅） | 產包；Gumroad 可全自動 |
| **分軌 stems** | `separate_stems.sh` 已在產線（Demucs） | Gumroad、Bandcamp、Splice（策展） | 產包 |
| 免費引流池 | 挑冷門曲 | Pixabay Music、Internet Archive（都免費、可全自動 ✅） | 進產線，當漏斗 |
| 影片素材 | `assets/visuals/` | Pond5（影片類） | 產包 |

> 一律帶上 `release.json` 的授權 provenance（模型／授權／來源），這是商用素材的**賣點**，不是附註。
> `release` 會把同一份 provenance 寫成 `credits.txt` **一起放進交付 ZIP**（買家看得到），
> 並在 `LISTING.md` 列出各平台要填的欄位。

### 5-1. AI 揭露（2026 起是上架的必要欄位，不是選配）

2026 起 AI 揭露**進了上傳流程本身**：DistroKid 的 **AI Credits** 會問「歌詞／旋律／全部音訊／部分音訊」
哪些是 AI 生成，選「全部音訊」後還會追問 artist 名是**真人還是 AI 角色**；Apple Music（Transparency Tags）、
Spotify（Song Credits）也都在收同一組旗標（經 DDEX）。本產線的音樂是 ACE-Step **全生成** → 預設揭露
`all_audio`（`config.RELEASE_AI_CREDITS`）。這組值會同時進 `release.json`（機器）與 `LISTING.md`／
`credits.txt`（人／買家），避免上架時靠記憶填。

| 欄位 | 值（本產線預設） | 出處 |
|---|---|---|
| `genre` | `Lo-fi` | `config.RELEASE_GENRE` |
| `explicit` | `False`（純器樂、無歌詞） | `config.RELEASE_EXPLICIT` |
| `songwriter` | `WeiChieh Wang`（**真名**，非藝名） | `config.RELEASE_SONGWRITER`／`--songwriter` |
| `ai_credits.scope` | `all_audio` | `config.RELEASE_AI_CREDITS` |
| `ai_credits.persona` | `True`（artist 是 AI 角色） | `config.RELEASE_AI_PERSONA` |
| `listing.tags` | 由 genre/style/專輯名推得（Gumroad 上限 10） | `release.listing_tags()` |
| `listing.price_usd` | `0`（名稱你的價錢） | `config.RELEASE_PRICE_USD`／`--price` |

---

## 6. 三個決策（已定案）

### 6-1. 封面要不要疊字？→ **決定：不疊字（純圖）**

證據：

- 本機 `ffmpeg -filters` **沒有 `drawtext`**（brew 版未編 libfreetype）；專案也沒有 Pillow／ImageMagick。
- 「盡量不安裝新東西」＋「`src/lofi` 幾乎只用標準庫」是硬約定；為疊字引相依不划算。
- DSP／YouTube 本來就會在封面旁顯示曲名與歌手，疊字是重複資訊。

若日後真的要疊字，唯一不破壞約定的路是**在 ComfyUI 端生圖時就帶文字**（模型不一定可靠），
否則就得引入 Pillow 到 `.venv` 並在 AGENTS.md 說明理由。**預設不做。**

### 6-2. artist / label 名稱 → **決定：`Moni on the Journey`**（與 YouTube 頻道分開，走廠牌家族）

頻道 `Drifted LoFi`（影片）／音樂 `Moni on the Journey`（串流），形成 `[X] LoFi` 的廠牌家族聯想，
主角是**黑貓**，每張專輯＝牠旅程的一站。名稱放 `config.RELEASE_ARTIST`（`LOFI_RELEASE_ARTIST` 可覆寫）。

定案依據（2026-10 實查 Apple Music ＋ MusicBrainz）：

| 候選 | 結果 |
|---|---|
| `Sumi` / `Hoshi` / `Ember` / `Soot` / `Nori` / `Kage` / `Marlow` / `Bramble` | **全部撞名**（單字名對新藝人是死路） |
| `Moni` 單獨 | 撞名嚴重 |
| `Moni's Journey`（初版，2026-10 改名） | 兩邊零撞名，但已棄用 |
| **`Moni on the Journey`** | **兩邊零撞名** ✅ |

> 改名原因：`Moni's Journey` 帶撇號，發行商可能把直引號 `'`（U+0027）與彎引號 `’` 當成**不同藝人**；
> 新名沒有撇號，這個坑消失。改名後已重新查核：Apple Music 無真實撞名（僅模糊比對）、MusicBrainz 0 筆。

可用 handle：`monionthejourney.bandcamp.com` ✅（導向 `/signup?new_domain=` = 可申請）、`monionthejourney.com` ✅、`@monionthejourney` ✅
（舊名 `@monisjourney` 反而已被佔用）。

- ⚠️ 全專案字串保持一致（`config.RELEASE_ARTIST`／`prompts/character/moni.json` 的 `artist`／文件）；
  發行商以**這個字串**建藝人頁，改字等於換藝人。
- label 欄位留空（DistroKid 免費方案本就不能自訂）。

#### 主角定裝（`prompts/character/moni.json`）

品牌的主體是一隻**黑貓**，定裝與品牌資料集中在 `prompts/character/moni.json`：

| 欄位 | 內容 |
|---|---|
| `design` | 純黑毛、大而圓的琥珀金眼、**四腳白襪**、手繪水彩動畫劇照風 |
| `cover_match` | 封面挑圖的判別條件（`must`/`never`/`features`），`release` 用它排除橘虎斑與無主角的圖 |
| `lora` | LoRA 方向與訓練集 run（`ds03`–`ds05`） |
| `journey` | 「每張專輯＝牠旅程的一站」的地點 → 專輯名對照 |

> ⚠️ **未確認**：`ref04_01_gold-socks.png` 檔名寫「金襪」，但該圖 prompt、`ds03` 字幕
> （`clearly white paws:1.3` / `white socks on all four feet:1.2`）與負向詞（`all black paws, black feet`）
> **全部指向白襪**。若定裝其實要金襪，`ds` 系列的訓練字幕與負向詞必須同步改，否則 LoRA 會學成白襪。

### 6-3. 母帶要不要重新算？

**這題的前提要修正：不是「要不要重新算」，而是「你根本沒有無損母帶」。**
`generate.py` 的節點 107 是 `SaveAudioMP3`，ACE-Step 直接輸出 mp3（48kHz stereo，≈248kbps VBR）。
`~/ComfyUI/output/audio/` 只剩 1 個檔（`--clean-raw` 刪掉了）。

**建議分兩軌（已決定：兩軌都做）：**

1. **未來生成的曲：改輸出格式（✅ 已實作）**
   `generate --format flac`，新曲直接是無損母帶；**母帶放外接 `output/masters/`，內接只留工作 mp3**（§3.3）。
2. **現有 126 首：先 (a) 再考慮 (b)**
   - **(a) 直接正規化上架**（✅ 已實作）：mp3 248kbps VBR 對 DSP 而言**可接受**（DistroKid 收 mp3）。
     `release package` 會用 `loudnorm` 做 -14 LUFS / -1 dBTP → WAV 母帶。**成本≈0，先用這個把專輯發出去。**
   - **(b) 用 sidecar 重生成取無損**（⬜ 未做）：sidecar 有完整 `seed/prompt/steps/cfg/temperature`，可重跑同一首。
     但 126 首要吃很多 GPU 小時，且 ACE-Step 的 `temperature` 可能造成非完全相同。
     **只對「之後要當主打單曲」的少數曲做**，不要一次全跑。

⚠️ 不論走哪條，`release.json` 都要記「母帶來源＝重生成／mp3 正規化」（`master_source` 欄位），
別讓半年後的人（或你自己）誤以為那是原始無損。

---

## 7. 分期實作順序（建議）

| 期 | 內容 | 產出 | 狀態 |
|---|---|---|---|
| **P1** | `lofi release package` ＋ 單曲 metadata ＋ `generate --format flac` | 上架包 | ✅ 已完成 |
| **P2** | `lofi live`（ffmpeg → RTMP） | 24/7 直播 | ⬜ 下一步 |
| **P3** | `lofi release gumroad`（API v2 全自動上架）＋ stems 包 | 素材授權 | ⬜（資料層已就緒：`release.json` 的 `listing`＋`LISTING.md`＋`download/*.zip`） |
| **P4**（選配） | Shorts／Reels 直式衍生 | 短影音 | ⬜ 未選，先列後補 |
| **P5**（天花板） | label 級 API（DDEX） | 全自動送 DSP | ⬜ 等 P1–P4 跑順再評估 |

> P1 已上線：`lofi release plan|package`、`generate --format flac`、`catalog/releases.jsonl`、
> 上架資料層（`release.json` 的 `genre/explicit/songwriter/ai_credits/listing`、
> `LISTING.md`、`credits.txt`、`download/<album>.zip`）。
> 尚未做的是 §3.2 的 **(b) 重生成無損母帶**（列為選配，見 §6-3）。

**P3 為什麼先做「資料層」而不是直接寫 API**：Gumroad 的 `POST /v2/products` 只建立 draft，
發佈要 `payout settings` 完成；Discover 又要 US$100 真實銷售＋風控審查＋至少 1 筆非退費銷售。
自動化寫完卻卡在帳號閘門對測試沒幫助；而 DistroKid／Gumroad 缺的是**同一組欄位**，
所以先把欄位做進 `release`（一次解鎖兩條路），再手動上架一次驗證格式，最後才做 API 自動化。

---

## 8. 風險與驗收

### 風險

| 風險 | 說明 | 對策 |
|---|---|---|
| **平台 AI 政策** | YouTube inauthentic content 是**頻道層級**；DSP 端模板化量產也會被演算法邊緣化 | 差異化進到 **release 層**（每張專輯自己的封面／文案／曲名／曲序），不是同模板換字 |
| **無損母帶缺失** | 現有曲源頭是 mp3 | §6-3；`release.json` 明記來源 |
| **自動化落差** | DSP 商沒有 API，做不出「一鍵上架」 | 承諾的是「上架包＋清單」，不是「自動上架」；別過度承諾 |
| **同一素材過度鋪貨** | 126 首一次全上、同封面同文案 | 一次一張專輯（一個 run），間隔釋出 |
| **授權** | 已由 `license` 守門（fail-closed） | 上架前跑 `./scripts/lofi license check` |

### 驗收（每條路線的 done 定義）

- **P1**：跑完 `release package` 後，能在 **10 分鐘內**手工完成一組 DSP 上架（含檔案、封面、曲序、文案齊備），且 `catalog/releases.jsonl` 有事件、`CHECKLIST.md` 步驟無需回頭查程式。
- **P2**：`lofi live` 掛上後斷線能自愈，連續 2 小時不掉。
- **P3**：Gumroad 上架可由 `lofi` 指令全自動完成（有 API），並回寫商品 URL。

### 一句話總結

> 現在缺的不是產能，是「把 run 變成 release」那一段。**先做上架包，再談流量。**
