# 商用授權與合規（LICENSING）

本專案的目標是**產出可商用**（YouTube 變現）。這份文件記錄「用了什麼、授權是什麼、換模型要注意什麼」。
**授權以各官方頁面最新標示為準**；本檔不是法律意見。

工具：`./scripts/lofi license check`（稽核產出實際用到的模型）、`./scripts/lofi config --licenses`（看登錄）。

---

## 1. 授權矩陣

**工具（只影響「散布軟體」，不影響你的影片／音樂產出）**

| 元件 | 用途 | 授權 | 商用 |
|---|---|---|---|
| ComfyUI | 生成引擎 | GPL-3.0 | ✅ 產出不受影響（別散布 ComfyUI 本體／改作） |
| ComfyUI-GGUF / ComfyUI_IPAdapter_plus | custom node | Apache-2.0 | ✅ |
| ffmpeg / ffprobe | 影音處理 | LGPL-2.1+ / GPL | ✅ 產出不受影響 |
| Demucs | 分軌 | MIT | ✅ |

**模型**

| 模型 | 用途 | 授權 | 商用 | 來源 |
|---|---|---|---|---|
| `acestep_v1.5_turbo` ＋ `qwen_0.6b/1.7b_ace15` ＋ `ace_1.5_vae` | 音樂生成（**預設**） | MIT / Apache-2.0 | ✅ | ACE-Step / Comfy-Org |
| `DreamShaperXL-v1.0-Alpha2`（SDXL） | 場景圖（**預設**） | CreativeML Open RAIL++-M | ✅（有使用限制條款） | Civitai 112902 |
| `sd_xl_base_1.0` | 場景圖（替代） | CreativeML Open RAIL++-M | ✅ | Stability |
| `RealESRGAN_x4plus_anime_6B` | 放大 | BSD-3-Clause | ✅ | xinntao/Real-ESRGAN |
| `z-image-turbo-Q5_K_M.gguf` ＋ `Qwen3-4B` ＋ `ae` | 場景圖（實驗；曾用於 ref01–05、sc06） | Apache-2.0 | ✅ | Tongyi-MAI/Z-Image |
| `ip-adapter-plus_sdxl_vit-h` | 風格參考（實驗；曾用於 ds01–03、sc01–05） | Apache-2.0 | ✅ | h94/IP-Adapter |
| `CLIP-ViT-H-14-laion2B-s32B-b79K` | IPAdapter 的 vision encoder | MIT | ✅ | laion/open_clip |
| `controlnet-union-sdxl-1.0` | ControlNet（實驗，未見於正式 manifest） | Apache-2.0 | ✅ | xinsir |

> 未登錄的模型 → `commercial=None` → **生成時 fail-closed（會中止）**，需先登錄或加 `--allow-unknown-license`。

## 2. 各授權的義務（重點）

- **MIT / BSD / Apache-2.0**：寬鬆，商用 OK；BSD/Apache 在**散布軟體**時需保留版權與授權聲明。你的產出（影片）沒有額外義務。
- **CreativeML Open RAIL++-M**：允許商用，但要遵守 **Attachment A 使用限制**（不得違法、危害未成年人、不實資訊、醫療/法律建議…）。**散布模型**時要附授權；你只散布產出，故 OK。
- **GPL（ComfyUI/ffmpeg）**：只在**散布軟體**時有義務；用它做內容不影響你的著作權。

## 3. 換模型 SOP（避免不小心用到非商用）

1. 查官方授權頁。
2. 在 `src/lofi/config.py` 的 `MODEL_LICENSES` 登錄：`{license, commercial: True/False, source}`。
3. `./scripts/lofi config --licenses` 確認。
4. 生成時 `generate` / `visual` 會自動檢查；`commercial=False` 或未知 → **中止**（除非 `--allow-unknown-license` 或 `LOFI_IGNORE_LICENSE=1`）。
5. 產出後跑 `./scripts/lofi license check` 稽核。

## 4. 產出的 provenance（可追溯＝可證明）

紀錄裡會存模型與授權，稽核時能回溯「哪首/哪張用哪顆模型」：

- **音樂**：`assets/tracks/<style>/<run>/*.json` 的 `gen.model` ＋ `gen.model_license` / `gen.model_source`（`generate` 寫入）。
- **圖片**：`assets/visuals/<run>/manifest.json` 的 `config.ckpt` ＋ `config.license` / `config.source`（`visual` 寫入）。
- **成片**：`publish/<episode>.json` 的 `credits`（`meta` 由 sidecar 彙整）＋ `ai_disclosure: true`。

> 既有舊紀錄沒有 license 欄位也沒關係：`license check` 會用 `config.MODEL_LICENSES` 回查補上。

## 5. YouTube 政策（影響「變現」，非授權）

- **AI 揭露**：勾選「變造或合成內容」（metadata 已記 `ai_disclosure: true`）。
- **Inauthentic content**（2025/7 起頻道層級偵測）：避免同模板量產、換首歌就上。靠**不同順序／視覺／標題**降低風險。
- **風格參考 vs. 素材重用**：模仿別人頻道的「風格」可以（風格不受著作權保護）；但**不要**重用他人頻道的音訊、圖片、文案。
  （本頻道 `Moni on the Journey` 自己的素材當然可以自由重用——這條是針對**外部**頻道。）
- 響度、章節、曲目 credits 等「策展痕跡」照 README/RUNBOOK 做。

## 6. 已知待處理

- 既有素材混用過實驗模型（z-image、IPAdapter）；授權皆查為可商用 ✅，但那些 run 早期**沒有記錄 license**（靠回查補）。新生成會自動記錄。
- 若日後引入 Civitai 上標示「禁止商用／NC」的 checkpoint 或 LoRA，**務必**先登錄並確認，否則守門會擋下。
