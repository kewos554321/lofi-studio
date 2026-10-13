# 模型放置說明（P2）

ComfyUI 安裝在 `~/ComfyUI`，模型實際放在 `~/ComfyUI/models/`。

> 本目錄（專案內的 `models/`）只放說明，不放權重——避免專案夾被塞爆。

## 本流程下載的檔案（分檔版，約 10GB）

| 檔案 | 放置位置 | 大小 |
|---|---|---|
| `acestep_v1.5_turbo.safetensors` | `~/ComfyUI/models/diffusion_models/` | 4.79 GB |
| `qwen_0.6b_ace15.safetensors` | `~/ComfyUI/models/text_encoders/` | 1.19 GB |
| `qwen_1.7b_ace15.safetensors` | `~/ComfyUI/models/text_encoders/` | 3.71 GB |
| `ace_1.5_vae.safetensors` | `~/ComfyUI/models/vae/` | 0.34 GB |
| `v1-5-pruned-emaonly-fp16.safetensors`（SD1.5，相容備用、非預設） | `~/ComfyUI/models/checkpoints/` | 2.13 GB |
| **合計** | | **≈ 12.2 GB** |

一鍵下載（可續傳）：`./scripts/download_p2_models.sh`（音樂模型 + SD1.5；**不含 SDXL，見下節**）

## 生圖模型（SDXL，產線預設）

產線**預設**的圖片模型是 SDXL（`src/lofi/config.py` 的 `IMAGE_CKPT`）：

| 用途 | 檔案 | 位置 | 大小 |
|---|---|---|---|
| 場景圖（預設） | `DreamShaperXL-v1.0-Alpha2.safetensors` | `~/ComfyUI/models/checkpoints/` | 6.5 GB |
| 場景圖（替代） | `sd_xl_base_1.0.safetensors` | `~/ComfyUI/models/checkpoints/` | 6.5 GB |
| 放大（選用） | `RealESRGAN_x4plus_anime_6B.pth` | `~/ComfyUI/models/upscale_models/` | 17 MB |

> ⚠️ `download_p2_models.sh` **只下載 ACE-Step 與 SD1.5，不含 SDXL**；SDXL checkpoint 需自行取得後放到上表位置（或用 `LOFI_IMAGE_CKPT` 指向別的）。放大模型可 `./scripts/download_p2_models.sh --upscale`。
> 每個 SDXL checkpoint 6.5GB，載入吃 RAM；內部碟放得下但要注意**數量**（見 AGENTS.md 資源限制）。
> 🧪（實驗，非預設）z-image turbo GGUF：`unet/*.gguf` + `text_encoders/Qwen3-4B-Q8_0.gguf` + `vae/ae.safetensors`，CLIP type `lumina2`；Mac MPS 上比 int8 快數倍。

## ⚠️ 兩個 text encoder 都是必要的

ACE-Step 1.5 的 `DualCLIPLoader` 會同時載入：

- `qwen_0.6b_ace15` → 文字/歌詞條件
- `qwen_1.7b_ace15` → 生成 audio codes（Chain-of-Thought 規劃）

ComfyUI 原始碼對兩者都呼叫 `get_full_path_or_raise`，**少一個會載入失敗**。
所以不能只留 0.6B。

## AIO 單檔 vs 分檔

官方 AIO `ace_step_1.5_turbo_aio.safetensors` 是 **10.0GB**，內容與上面四個分檔**完全相同**
（DiT + 0.6B + 1.7B + VAE），只是打包成一個檔。分檔不會比較省空間；
若偏好少一個檔案，改用 AIO + `audio_ace_step_1_5_checkpoint.json` 工作流即可。

## 16GB Mac 建議組合

| 元件 | 建議 | 理由 |
|---|---|---|
| DiT | 2B turbo（8 steps） | 快、記憶體友善 |
| Text encoders | 0.6B + 1.7B（架構需要） | |
| 長度 | 120 秒/首 | 太長易爆記憶體 |
| 輸出 | 44.1 kHz 立體聲 | |

## 不要用

- XL（4B DiT）系列：約 10GB，需 ≥12GB VRAM（含 offload）或 ≥20GB，16GB Mac 不適合。
- MusicGen（CC-BY-NC 非商用）。

## 授權

- **ACE-Step 1.5**：MIT（可商用）。
- **ACE-Step v1 3.5B**：Apache-2.0（可商用）。
- **SD1.5**：CreativeML Open RAIL-M（可商用，有使用限制條款）。
- **DreamShaperXL / SDXL base**：CreativeML Open RAIL++-M（可商用，有使用限制條款；以模型頁標示為準）。
- **Real-ESRGAN（放大）**：BSD-3-Clause（可商用）。
- 使用前仍請以官方頁面標示為準，並保留來源紀錄。
