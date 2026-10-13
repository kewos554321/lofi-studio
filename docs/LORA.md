# LoRA 訓練（雲端／RunPod）

目的：讓主角 **Moni（黑貓）** 每次生成都長一樣——尤其是**白襪**這個小面積特徵，
IPAdapter 鎖不牢（見 [`RUNBOOK.md`](RUNBOOK.md) §6.1），只能靠 LoRA。

**為什麼一定要雲端**：本機是 M4 / 16GB 統一記憶體，SDXL LoRA 訓練不可能跑（推論都只有 6.5GB 餘裕）。
本機只負責**生資料集**與**用訓好的 LoRA**，訓練在 RunPod。

> 角色定裝是唯一來源：`prompts/character/moni.json`。改定裝改那份，不要只改 prompt。

---

## 0. 流程總覽

| 步 | 在哪 | 指令 |
|---|---|---|
| 1 | 本機 | 生資料集（≥20 張）→ `lofi character check` |
| 2 | 本機 | 補字幕／觸發詞 → `lofi character caption` |
| 3 | 本機 | 打包 → `lofi character export` |
| 4 | RunPod | 訓練 → 下載 `*.safetensors` |
| 5 | 本機 | 登錄授權 → `lofi visual --lora` 使用 |

---

## 1. 生資料集（本機，要 GPU）

```bash
# 一批 ds 圖（含字幕）；--ref 是定裝參考圖，--kind ds 是訓練集
./scripts/lofi visual --ref mascot_blackcat_ref.png --kind ds --write-caption \
    --run-id ds06 --count 20 --size 832x1216 --steps 28 --cfg 6.5 --lora-weight 1.0
```

- 一次別生太多（16GB 會爆），分批生。
- 生完**自己看一遍**，不符標準的刪掉（剔圖是正常流程，不是錯誤）。
- 檢查：`./scripts/lofi character check`

```text
◆ LoRA 訓練集一致性：Moni（ds03, ds04, ds05, ds06）
  ds03   生成  30 張　→ 留  10 張（剔除  20）　字幕 10/10
  ...
  ❌ 訓練集只有 13 張，低於建議下限 20 張 → 先補圖再訓練
```

`check` 的判讀原則：**manifest 是生成紀錄、磁碟上還在的才是訓練集**，
兩者落差是人工剔除（正常）；錯誤只報在**留下的那些**（漏字幕、特徵矛盾、數量不足）。

---

## 2. 補字幕與觸發詞（本機）

```bash
./scripts/lofi character caption            # 缺失的從 manifest prompt 重建，並補觸發詞
./scripts/lofi character caption --dry-run  # 先看會做什麼
```

**為什麼要觸發詞（trigger word）**：字幕若只寫 `a sleek black cat with white socks`，
模型會把「黑貓」這個**通用概念**學進去 → 套用 LoRA 後**所有黑貓都變成 Moni**，而且無法控制。
所以每張字幕都前置一個獨特 token（本專案用 `moni`）：

```text
moni, a sleek black cat with (large round golden amber eyes:1.2), (clearly white paws:1.3), ...
```

> 註：ds04/ds05 當初生成時沒帶 `--write-caption`，但 `manifest.json` 逐張記了 prompt，
> 而 `--write-caption` 寫的內容就等於 prompt（已驗證 ds03 的 10 個 `.txt` 與 manifest 逐字相同），
> 所以那些字幕可以**逐字重建**。

---

## 3. 打包（本機）

```bash
./scripts/lofi character export --name moni
#   → output/lora/moni/            （dataset/ + moni.toml + README.md）
#   → output/lora/moni_dataset.zip （上傳用，十幾 MB）
```

產物要點：

| 檔案 | 說明 |
|---|---|
| `dataset/10_moni/*.png` + `*.txt` | kohya 的 `N_concept` 重複資料夾格式 |
| `moni.toml` | kohya `sdxl_train_network.py` 設定；**resolution 對齊資料集原尺寸 `832,1216`** |
| `README.md` | 打包內容 + 訓練步驟 + 花費提醒 |

步數 ＝ 張數 × `num_repeats` × `max_train_epochs`。13 張 × 10 × 10 ＝ 1300 步。
張數少就調高 repeats；張數多就調低。

---

## 4. RunPod 訓練

### 選 GPU
SDXL LoRA **24GB 就夠**（4090 / A5000 / A6000）。不必用到 A100/H100。

### 選 image
挑內含 **kohya_ss** 或 **sd-scripts** 的 image（RunPod 社群有不少現成的）。
> template 名稱會變動，請以 RunPod 當下的清單為準；要的是「已裝好 kohya_ss／sd-scripts + accelerate 的 PyTorch image」。

### 準備基底模型（三選一，**都免費**）
放到 `<remote>/models/`（預設 `/workspace/models/`）：

| 方式 | 說明 |
|---|---|
| Civitai（**需免費 API token**） | `curl -L -o model.safetensors "https://civitai.com/api/download/models/<id>?token=<token>"`，檔案與本機**完全相同** |
| HuggingFace `Lykon/dreamshaper-xl-1-0` | 免 token，但是 **diffusers 格式**（資料夾），且不確定與 Alpha2 同一版 |
| 從本機上傳 | 你已有 `~/ComfyUI/models/checkpoints/DreamShaperXL-v1.0-Alpha2.safetensors`（6.5GB） |

> Civitai 的 token 只是認證，**不用錢**（付費是生成用的 Buzz，與下載無關）。

### 訓練
把 `moni_dataset.zip` 解到 `<remote>/`，然後在 kohya 目錄下：

```bash
accelerate launch --num_cpu_threads_per_process 8 sdxl_train_network.py \
    --config_file /workspace/moni.toml
```

### 儲存策略：不要付到冤枉錢

RunPod 有三種儲存，**Stop 時的行為完全不同**（[官方 pricing](https://docs.runpod.io/pods/pricing)）：

| 類型 | 掛載點 | Stop 之後 | 費用 |
|---|---|---|---|
| **Container disk** | 系統 | **被抹掉** | $0.10/GB/月，**只在執行時收** |
| **Volume disk** | `/workspace` | 保留 | $0.10/GB/月 → **Stop 後變 $0.20/GB/月（更貴）** |
| **Network volume** | `/workspace` | 永久保留 | $0.07/GB/月（跑不跑都收） |

**關鍵：RunPod 沒有 ingress／egress 費用** —— 上傳資料集、下載 LoRA 都免費。

#### 推薦：完全不掛 volume

```
container disk 開 30GB
  → 在 pod 內下載 base model（免費、一兩分鐘；不要從家裡上傳 6.5GB）
  → 上傳 moni_dataset.zip（17MB）
  → 訓練
  → 下載 moni.safetensors
  → Terminate
```

單次成本：GPU 約 $0.7–1.5 ＋ container disk 約 **$0.01**；**閒置時 $0**。

#### 什麼時候才值得掛 network volume

只有**密集來回迭代**時（同個月重訓 3–4 次、想省下每次重下載）。
約 9GB × $0.07/月 ≈ **$0.63/月**。只訓一次就別掛。

#### ⚠️ 兩個必知的坑

1. **`Stop` 不是免費**：用 volume disk 再 Stop，儲存費會**漲到 $0.20/GB/月**。要省錢是 **Terminate**。
2. **`Terminate` 會刪光一切**：**一定要先下載 `moni.safetensors`** 再 Terminate，否則等於白花 GPU 錢。
   RunPod 官方明講他們**不是長期儲存**，重要的東西一律備份回本機。

> RunPod **沒有本地磁碟直通**——沒辦法把家中硬碟掛進 pod。
> 「本地是主檔、雲端只用 GPU」的做法就是：主檔留在本機，每次推進 pod（或掛免費 egress 的物件儲存當中介）。


- 監看 loss：`tensorboard --logdir /workspace/logs` 或看 sample 圖。
- 產物：`<remote>/output/moni.safetensors`。

### 費用
約 **1–2.5 小時**，4090 級約 **US$1–3**。loss 明顯收斂就提早停。

---

## 5. 回本機使用

1. 下載 `moni.safetensors` → `~/ComfyUI/models/loras/`
2. **登錄授權**（本專案對未知模型 fail-closed，沒登錄會被擋）：

```python
# src/lofi/config.py → MODEL_LICENSES
"moni.safetensors": {
    "license": "自訓 LoRA（基底 DreamShaperXL-v1.0-Alpha2）", "commercial": True,
    "source": "本專案自產（見 prompts/character/moni.json）"},
```

3. 生成（`moni` 就是觸發詞）：

```bash
./scripts/lofi visual --lora moni.safetensors --lora-weight 0.8 \
    --prompt "moni, a sleek black cat with white socks, sitting on a windowsill, \
              rainy night, warm lamp, hand-painted watercolor anime illustration" \
    --negative "black paws, black feet, lowres, blurry" \
    --size 832x1216 --steps 28 --cfg 6.5 --kind sc --run-id sc07
```

- **`--lora-weight` 是主控旋鈕**：0.7–0.8 自然、0.9–1.0 特徵硬（但也可能變僵）。
- LoRA 會在 workflow 裡接成 `checkpoint → LoRA → IPAdapter → 取樣器`，
  `manifest.json` 會記 `lora` / `lora_weight`，回頭看得出哪批用了哪顆。
- `--lora` 可與 `--ref`（IPAdapter）併用：LoRA 管特徵、IPAdapter 補構圖。

---

## 6. 疑難排解

| 症狀 | 原因 | 處理 |
|---|---|---|
| 套用後「所有黑貓都變 Moni」 | 字幕沒有觸發詞 | 跑 `lofi character caption` 補 `moni`，重訓 |
| 白襪還是黑的 | 訓練集白襪樣本太少／權重太低 | 補多一點「四腳白襪」清楚的圖；提高 `--lora-weight` |
| 每張都同一個姿勢 | 資料集場景太單一 | 補不同地點／角度（窗台／壁爐／森林／草地…） |
| CUDA OOM | batch 太大或解析度太高 | `batch_size=1`、`gradient_checkpointing=true`、降 `max_bucket_reso` |
| loss 不收斂 | lr 太高或步數不足 | `unet_lr` 降到 5e-5；提高 epochs |
| 用了 LoRA 卻沒效果 | 觸發詞沒寫進 prompt | prompt 一定要含 `moni` |
| `--lora` 被中止「授權未知」 | 沒登錄 LoRA | 見 §5 步驟 2 |

---

## 7. 現況（每次訓練前更新）

- 資料集：`assets/visuals/ds03`（10）＋ `ds04`（1）＋ `ds05`（2）＝ **13 張**，**低於下限 20**
- 字幕：13/13 完整且含觸發詞 ✅
- 下一步：生 `ds06`（至少 7 張，建議 20）→ `character check` 轉綠 → `export` → RunPod
