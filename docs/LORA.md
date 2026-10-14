# LoRA 訓練（RunPod + AI Toolkit / Z-Image Turbo）

目的：讓主角 **Moni（黑貓）** 每次生成都長一樣——尤其是**純黑身體＋只有四隻腳白襪**這個
小面積特徵，IPAdapter 鎖不牢（見 [`RUNBOOK.md`](RUNBOOK.md) §6.1），只能靠 LoRA。

**為什麼一定要雲端**：本機是 M4 / 16GB 統一記憶體，LoRA 訓練不可能跑（推論都只有 6.5GB 餘裕）。
本機只負責**生資料集**與**用訓好的 LoRA**，訓練在 RunPod。

**為什麼是 AI Toolkit，不是 kohya_ss**：kohya / sd-scripts **不支援 Z-Image**。
AI Toolkit（ostris）有 `zimage:turbo` 這個 arch，且內建 Turbo 的 de-distill 訓練適配器。

> 角色定裝是唯一來源：`prompts/character/moni.json`。改定裝改那份，不要只改 prompt。

---

## 0. 流程總覽

| 步 | 在哪 | 做什麼 |
|---|---|---|
| 1 | 本機 | 生資料集 → 挑圖 → `lofi character check` |
| 2 | 本機 | **寫「觸發詞承載角色」的字幕**（§2，本文最重要的一步）|
| 3 | 本機 | 打包 dataset ＋ 寫 AI Toolkit 設定檔 |
| 4 | RunPod | 送檔 → 訓練 → 下載 → 驗 sha256 → **Terminate** |
| 5 | 本機 | 複製到 `~/ComfyUI/models/loras/` → 生成 |

---

## 1. 生資料集（本機，要 GPU）

```bash
./scripts/lofi visual --ref mascot_blackcat_ref.png --kind ds --write-caption \
    --run-id ds09 --count 20 --size 832x1216 --steps 28 --cfg 6.5 --lora-weight 1.0
./scripts/lofi character check      # 一致性檢查（缺字幕／特徵矛盾／張數不足）
```

- 一次別生太多（16GB 會爆），分批生；生完**自己看一遍**，不符標準的剔除（正常流程）。
- `check` 的判讀原則：**manifest 是生成紀錄、磁碟上還在的才是訓練集**，落差是人工剔除。
- 目前規模：`ds07`(20) + `ds08`(16) + `ds09`(3) = **39 張**（可用，但要更硬的身分建議再往上加）。

---

## 2. 寫字幕：**角色只由觸發詞承載** ★

這一節是本文件的核心。**訓練成敗幾乎完全取決於字幕寫法，不是參數。**

### 2.1 原則

每張字幕＝**場景 ＋ 姿態 ＋ 光線 ＋ 畫風**，**不描述角色本體**，角色交給開頭的觸發詞：

```text
mnxy, A cosy corner of a coffee shop on an autumn afternoon, a wooden table with a cup of
latte beside it. Sitting upright and looking at the camera. Warm sunlight slants through the
big window onto golden maple leaves outside. Hand-painted watercolour anime illustration,
delicate brush strokes, warm nostalgic colour palette.
```

規則：

| # | 規則 | 為什麼 |
|---|---|---|
| 1 | 觸發詞放**第一個 token** | 位置固定才好綁 |
| 2 | **禁止**任何角色描述（`cat`/`tail`/`paw`/`fur`/`whisker`/`ear`/`black`/`white sock`…）| 見 2.2 |
| 3 | 姿態要**物種中立** | 「尾巴繞著前掌」等於在講「這是貓」 |
| 4 | 用**無意義的觸發詞**（本輪用 `mnxy`；`moni` 是人名，底座先驗是「人」）| 避免與既有詞義打架 |
| 5 | `caption_dropout_rate: 0` | 字幕已由觸發詞承載，不需要再丟 |
| 6 | 畫風句最好**逐句變化** | 見 §6 已知限制 |

### 2.2 為什麼：這一步做錯，LoRA 一輩子學不會

v1 的實際失敗案例（同一批 39 張圖、同一顆底座、同樣 rank/lr，只有字幕不同）：

```text
# ❌ v1：用底座聽得懂的詞把角色講光
moni, A cosy corner ... In it, an adorable black cat with a round fluffy face, big sparkling
round golden amber eyes, soft fur and white socks on all four feet, sitting upright with its
tail wrapped around its paws, ... Hand-painted watercolour ...

→ 結果：任何權重（0.8 / 1.0 / 3.0）、任何 prompt 都叫不出貓。
        模型只要抓住「black cat…white socks」那串字就能壓低 loss，沒有動機去用 moni。
        學到的是「無條件偏誤＝畫風」（w=3.0 會整張變水彩）＋一點特徵外溢（人的白鞋）。❌

# ✅ 探針：角色只由 mnxy 承載
mnxy, A cosy corner ... Sitting upright and looking at the camera. ... Hand-painted watercolour ...
→ 結果：w=1.0 就出 Moni；三個**訓練集沒有的**場景（海濱／火車／老街）＋三個 seed 都成立。✅
```

**一句話**：觸發詞必須是通往角色的**唯一道路**，LoRA 才會去學那條路。

### 2.3 工具現況（重要）

`lofi character caption` 是從 manifest 的 prompt **重建**字幕並補觸發詞——它**不會**幫你把
角色描述拿掉。所以 §2 目前是**手寫**（39 句約半小時），或自己寫個一次性轉換（但要逐句檢查英文，
機械式 regex 會產出 `sitting uprights`、`curled around its .` 這種壞字，反而傷 Qwen3 編碼器）。

> TODO：把「寫觸發詞承載字幕」做成 `lofi character caption --trigger-only`，並在 manifest 記
> `tokens`（`ds09` 的 `mncap`/`mnscarf`/`mncollar` 已經是這個做法）。

---

## 3. 打包與上傳

資料集（`<name>` ＝ 觸發詞）：

```text
dataset/<name>/<image>.png + 同名 .txt      ← 檔名對得上，AI Toolkit 才讀得到字幕
```

打包成 zip（含圖、字幕、設定檔），再一次送上去：

```bash
python3 - <<'PY'
import zipfile, pathlib
base = pathlib.Path("output/lora/probe_mnxy")
with zipfile.ZipFile(base/"bundle.zip","w",zipfile.ZIP_DEFLATED) as z:
    for p in sorted((base/"dataset").iterdir()):
        z.write(p, f"dataset/mnxy/{p.name}")     # ← 前綴就是 pod 上的落地路徑
    z.write(base/"mnxy_probe.yaml", "mnxy_probe.yaml")
PY
```

送檔用 `runpodctl send`／`receive`（croc，壓縮＋加密，實測 **50MB 約 11 秒**）：

```bash
runpodctl send output/lora/probe_mnxy/bundle.zip     # 本機；印出一次性 code 後阻塞
# 另開：ssh 進 pod 後
runpodctl receive <code>                             # pod 端
```

> 坑：`scp` 對這台 Mac 實測只有 ~0.13MB/s（比 croc 慢 35 倍）；zsh 會把未加引號的遠端路徑
> 當本機 glob 展開，`scp pod:'/path/*'` 要加引號。

---

## 4. RunPod 訓練（AI Toolkit）

### 4.1 選 GPU 與模板

| 項目 | 值 |
|---|---|
| 模板 | `0fqzfjy6f3`「AI Toolkit - ostris - ui - official」（image `ostris/aitoolkit:latest`，ports `8675/http,22/tcp`，container disk 150GB）|
| GPU | RTX 4090（24GB 足夠；Secure **$0.89/hr**）|
| **`--min-cuda-version 13.0`** | **必加**。該 image 基底是 `nvidia/cuda:13.0.3-runtime-ubuntu24.04`，要求 driver ≥ 580；不加會落在舊 host 上開機即死 |

```bash
runpodctl pod create --name mnxy-probe --template-id 0fqzfjy6f3 \
  --gpu-id "NVIDIA GeForce RTX 4090" --min-cuda-version 13.0 \
  --container-disk-in-gb 150 --data-center-ids EU-RO-1,US-IL-1,US-NC-1
```

### 4.2 設定檔（`zimage` 訓練的關鍵欄位）

```yaml
job: extension
config:
  name: mnxy_probe
  process:
  - type: sd_trainer
    training_folder: /app/ai-toolkit/output
    device: cuda:0
    trigger_word: mnxy
    network: {type: lora, linear: 16, linear_alpha: 16}
    save: {dtype: bf16, save_every: 200, max_step_saves_to_keep: 10, push_to_hub: false}
    datasets:
    - folder_path: /app/ai-toolkit/datasets/mnxy
      caption_ext: txt
      default_caption: mnxy
      caption_dropout_rate: 0.0
      resolution: [832]
    train:
      batch_size: 1
      steps: 1000                  # 探針用；正式建議 2500–3000
      train_unet: true
      train_text_encoder: false    # ★ z-image 不支援，見 §6
      gradient_checkpointing: true
      noise_scheduler: flowmatch
      timestep_type: weighted
      optimizer: adamw8bit
      lr: 0.0001
      dtype: bf16
    model:
      name_or_path: Tongyi-MAI/Z-Image-Turbo
      arch: zimage:turbo
      quantize: true
      quantize_te: true
      qtype: qfloat8
      low_vram: true
      assistant_lora_path: ostris/zimage_turbo_training_adapter/zimage_turbo_training_adapter_v2.safetensors
    sample:
      sampler: flowmatch
      sample_every: 200            # 每 200 步生 4 張，用來判斷收斂
      width: 832
      height: 1216
      prompts: [<四句：2 句帶畫風句、2 句純觸發>]
      neg: ''
      seed: 42
      walk_seed: false             # ★ 固定 seed，樣本才能逐步比較
      guidance_scale: 1
      sample_steps: 9
```

**`assistant_lora_path` 一定要填**：Z-Image Turbo 是步數蒸餾模型，直接訓練會很快破壞蒸餾；
這顆 de-distill adapter 讓它回到一般 flow-matching 行為。它**只在訓練時作用**（訓練與取樣時
`is_active=False`），所以訓練出來的 LoRA 直接掛在純 Turbo 上就能用。

### 4.3 啟動與監看

```bash
ssh root@<ip> -p <port> 'cd /app/ai-toolkit && python run.py config/mnxy_probe.yaml'   # 前景測試
# 正式跑用 nohup，並記下 pid
cd /app/ai-toolkit && nohup python run.py config/mnxy_probe.yaml > /root/mnxy_probe.log 2>&1 &
tr '\r' '\n' < /root/mnxy_probe.log | grep -E "^mnxy_probe:" | tail -1   # 看進度
```

速度實測：**832px、1.50 s/it**（4090）→ 1000 步約 **25 分鐘**；3000 步約 **1.5 小時**。
第一次啟動要先下載 base model（`Tongyi-MAI/Z-Image-Turbo`）＋適配器，約 10–15 分鐘。

### 4.4 收檔與驗證（**沒驗完不准 Terminate**）

```bash
scp -r -i ~/.ssh/runpod_ed25519 -P <port> root@<ip>:/app/ai-toolkit/output/<name>/. <本機目錄>/
# 兩邊 shasum -a 256 逐檔比對；樣本也要抓（那是判讀依據，不是裝飾）
```

### 4.5 費用實測

| 項目 | 實測 |
|---|---|
| 費率 | RTX 4090 Secure **$0.89/hr** |
| 探針（1000 步）整趟 | **$1.37**（建立→SSH 等待→傳檔→下載模型→訓練 25 分→收檔→Terminate）|
| 正式（3000 步）預估 | **約 $2**（訓練 1.5 小時＋固定開銷）|
| 注意 | `--stop-after`／`--terminate-after` **從未生效**（官方已於 v2.12.0 移除）。收工一律 `runpodctl pod remove <id>`，再讀 `runpodctl pod list` 確認空、`runpodctl user` 確認 `spendPerHr=0` |

---

## 5. 本機使用（ComfyUI z-image GGUF）

```bash
cp output/lora/probe_mnxy/run/mnxy_probe.safetensors ~/ComfyUI/models/loras/
```

> ⚠️ ComfyUI **只讀 `~/ComfyUI/models/loras/`**；`output/lora/...` 只是倉庫。換新 LoRA 要兩份
> 都更新並用 sha256 確認（別只比檔名）。新 LoRA 也要先登錄授權（§8），否則產線會擋下來。

### 5.1 產線腳本（`scratch/_tmp_zimage_gguf.py`，已支援 `--lora`）

```bash
python3 scratch/_tmp_zimage_gguf.py \
    --lora mnxy_probe.safetensors --lora-weight 1.0 \
    --prompt "mnxy, <場景與姿態>. Hand-painted watercolour anime illustration, \
              delicate brush strokes, warm nostalgic colour palette." \
    --size 832x1216 --kind sc --slug <標籤>
```

| 參數 | 說明 |
|---|---|
| `--lora` | LoRA 檔名（放 `ComfyUI/models/loras/`）|
| `--lora-weight` | 強度，預設 1.0（實測 1.0 就是在模型上）|
| `--dry-run` | 只印設定與**授權檢查**，不生成。授權檢查**先於** dry-run 執行，所以乾跑就能知道會不會被擋 |

`manifest.json` 會記 `lora` / `lora_weight`，回頭看得出哪批用了哪顆。

### 5.2 節點接法

z-image 的 LoRA **只 patch DiT**，所以用 `LoraLoaderModelOnly`（沒有 `strength_clip`）；
掛在 `ModelSamplingAuraFlow` **之前**，txt2img 與 inpaint 兩個分支都會吃到：

```text
UnetLoaderGGUF(z-image-turbo-Q5_K_M.gguf)
      ↓
LoraLoaderModelOnly(lora_name=<name>.safetensors, strength_model=1.0)
      ↓
ModelSamplingAuraFlow(shift=3.0) → KSampler(steps 8, cfg 1.0, res_multistep/simple)
```

> 不要改用 `lofi visual --lora`——那是 **SDXL** 流程（`LoraLoader` ＋ IPAdapter），架構不同，
> 掛不上 z-image 的 LoRA（見 §6 第 5 點）。

**prompt 必須帶畫風句**（否則只會得到「別的黑貓」）：

```text
mnxy, <場景與姿態>. Hand-painted watercolour anime illustration, delicate brush strokes, warm nostalgic colour palette.
```

實測（本機、832×1216、seed 固定）：

| 權重 | 結果 |
|---|---|
| 0（對照）| 場景正確、**沒有貓** |
| **1.0** | **Moni**（純黑＋四隻白襪＋金眼＋水彩），場景完整保留 |

已驗證的三個**訓練集沒有**的場景：海濱日落長椅、火車籐籃、老街雨夜石牆——三個 seed 全部在模型上。

---

## 6. 已知限制（改版前先讀）

1. **AI Toolkit 不支援 z-image 的文字編碼器訓練。**
   `toolkit/lora_special.py` 的 TE 掛載目標是寫死的 `["CLIPAttention","CLIPMLP"]`，而 Z-Image 的
   TE 是 Qwen3（`Qwen3Attention`/`Qwen3MLP`）→ 一個都不匹配，`train_text_encoder: true`
   **靜默變成 0 modules**（只有 pixart 有 `T5EncoderModel` 特例）。
   **所以不要把希望放在 TE 訓練上**，要靠 §2 的字幕策略。

2. **觸發詞需要畫風句才會完全生效**：只喊 `mnxy, <場景>`（不帶畫風句）時，物種綁得住（會出貓），
   但花色會飄（實測出現過寫實乳牛貓）。因為畫風句在 39 句字幕裡**一字不變**，它變成了觸發詞的
   上下文。→ 正式版讓畫風句有些變化，觸發詞才會更獨立。

3. **身分是隨步數收斂的**（同 prompt、同 seed）：step 400 還是乳牛貓（臉／胸口白），
   step 1000 才鎖成純黑＋白襪。→ 正式版拉到 2500–3000 步；`save_every` 留著中途檢查點可挑。

4. **LoRA 只在 ComfyUI 的 `models/loras/` 被讀到**；`output/lora/...` 是倉庫，改完要同步兩份
   （用 sha256 確認，別只靠檔名）。

5. **別用 `lofi visual --lora` 掛 z-image 的 LoRA**——那是 **SDXL** 流程（DreamShaperXL＋IPAdapter），
   架構不同，掛不上去。

---

## 7. 疑難排解

| 症狀 | 原因 | 處理 |
|---|---|---|
| 任何權重都叫不出角色 | 字幕把角色講光了 | §2：角色只由觸發詞承載，重訓 |
| 套用後「所有黑貓都變 Moni」 | 同上（角色沒綁在觸發詞上，綁在通用詞上）| 同上 |
| 觸發詞有反應但花色亂（變乳牛貓）| (a) prompt 沒帶畫風句 (b) 步數不足 | 補畫風句；提高步數 |
| 場景都被拉成訓練集那幾個 | 步數過多／資料集場景太單一（背場景而非學角色）| 降步數；補不同地點／角度 |
| 掛了 LoRA 卻完全沒效果 | prompt 沒寫觸發詞 | prompt 一定要含觸發詞 |
| ComfyUI 載入後毫無差異 | 檔案不在 `~/ComfyUI/models/loras/` | 複製過去（並 sha256 對過）|
| pod 開機即死 / CUDA 錯誤 | 沒下 `--min-cuda-version 13.0` | 重建時補上（§4.1）|
| `runpodctl send` 卡住 | 兩端都要執行（送出端阻塞等接收端）| 送出端用背景常駐，接收端再連 |

---

## 8. 現況（每次訓練前更新）

- **已可用的 LoRA**：`output/lora/probe_mnxy/run/mnxy_probe.safetensors`（step 1000，觸發詞 `mnxy`）
  - 已在本機 ComfyUI z-image GGUF 驗證：新場景 × 新 seed 皆為 Moni
  - 對照組（w=0）確認無貓 → 效果確實來自 LoRA
- 資料集：`assets/visuals/ds07`(20) ＋ `ds08`(16) ＋ `ds09`(3) ＝ **39 張**
- 本次訓練產物：`output/lora/probe_mnxy/`（`run/` 內含 final ＋ 4 個中途檢查點 ＋ 24 張樣本）
  - 訓練配方：`output/lora/probe_mnxy/mnxy_probe.yaml`（＝pod 上 `run/config.yaml`）
- 累計花費：探針 **$1.37**（v1 那顆已作廢、相關檔案已清）
- 下一步：正式版 2500–3000 步（字幕策略沿用，畫風句加上變化）→ 挑檢查點 → 登記授權

> **授權登錄**：新 LoRA 要進 `src/lofi/config.py` 的 `MODEL_LICENSES`（本專案對未知模型
> fail-closed）。格式：`{"license": "自訓 LoRA（基底 Z-Image Turbo）", "commercial": True,
> "source": "本專案自產（見 prompts/character/moni.json）"}`。
