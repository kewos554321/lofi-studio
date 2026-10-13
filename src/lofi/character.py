#!/usr/bin/env python3
"""character.py — 主角定裝的檢視，與 LoRA 訓練集的一致性檢查。

為什麼需要：訓練集的問題（漏字幕、字幕與定裝矛盾、負向詞沒對抗錯誤特徵、數量不足）
都會讓訓練出來的貓漂移，而且往往要等訓練跑完才發現，白等好幾小時。

**重要觀念（決定這個工具怎麼判對錯）**：
  - `manifest.json` 是「**生成紀錄**」：那次生過什麼，只增不改、可追溯。
  - `assets/visuals/<run>/` 底下**還在磁碟上的檔**才是「**策展後的訓練集**」。
  - 兩者的落差是**人工剔除不符標準的圖**的結果，**不是錯誤**（本專案會手動剔除）。
    所以落差只當資訊印出來；錯誤只報在**留下來的那些圖**上。

用法:
  lofi character show                  # 定裝、旅程、訓練集現況
  lofi character check                 # 一致性檢查（有問題回傳非零）
  lofi character check --json
"""
import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lofi import io
from lofi.paths import CHARACTER_DIR, LORA_DIR, VISUALS_DIR

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_MIN_IMAGES = 20          # 角色 LoRA 的經驗下限
ICON = {"error": "❌", "warn": "⚠️"}


# ── 角色檔 ────────────────────────────────────────────────────────────

def load_character(slug="moni"):
    """讀主角定裝 `prompts/character/<slug>.json`；沒有回 None。"""
    p = CHARACTER_DIR / f"{slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        io.quarantine(p, why="角色檔 JSON 壞損")
        return None


# ── 純函式（可單元測試）────────────────────────────────────────────────

def caption_problems(caption, spec):
    """回傳字幕的問題清單（空 ＝ 通過）。

    `spec` 來自角色檔的 `caption_check`：require_tags 必備、forbid_tags 不得出現。
    """
    t = (caption or "").lower()
    if not t.strip():
        return ["字幕是空的"]
    probs = []
    for tag in spec.get("require_tags") or []:
        if tag.lower() not in t:
            probs.append(f"缺少必要特徵「{tag}」")
    for tag in spec.get("forbid_tags") or []:
        if tag.lower() in t:
            probs.append(f"出現矛盾詞「{tag}」")
    return probs


def negative_problems(negative, spec):
    """生成資料集時用的負向詞必須含「要對抗的錯誤特徵」，否則推不出正確特徵。

    注意：負向詞只影響**生成資料集**那一步，LoRA 訓練本身不吃它。
    """
    t = (negative or "").lower()
    return [f"負向詞缺少「{x}」（生成時無法對抗錯誤特徵）"
            for x in (spec.get("negative_should_contain") or [])
            if x.lower() not in t]


def has_trigger(text, trigger):
    """觸發詞是否已存在（比對整個 token，避免 'moni' 誤中 'monitor'）。"""
    if not trigger:
        return True
    return bool(re.search(rf"(?:^|[,\s]){re.escape(trigger)}(?:$|[,\s])", text or "", re.I))


def caption_plan(existing, prompt, trigger=""):
    """決定這張圖的字幕內容，回傳 (新字幕, 動作)。

    - 沒有字幕 → 用 manifest 的 prompt 重建（`--write-caption` 就是寫 prompt，兩者逐字相同）。
    - 有字幕但缺觸發詞 → 前置觸發詞。
    動作：`reconstruct` / `add-trigger` / `reconstruct+trigger` / `ok` / None（沒有可用來源）。
    """
    base = (existing or "").strip()
    action = None
    if not base:
        base = (prompt or "").strip()
        if not base:
            return None, None
        action = "reconstruct"
    if trigger and not has_trigger(base, trigger):
        base = f"{trigger}, {base}"
        action = f"{action}+trigger" if action else "add-trigger"
    return base, (action or "ok")


# ── 檔案層 ────────────────────────────────────────────────────────────

def _images_on_disk(d):
    return {p.name for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS}


def check_run(run_dir, spec):
    """檢查一個訓練 run。回傳統計 + 問題清單（只針對**留下的**圖）。"""
    d = Path(run_dir)
    res = {"run": d.name, "listed": 0, "kept": 0, "rejected": 0, "marked_reject": 0,
           "rejected_names": [], "no_caption": [], "caption_issues": [],
           "negative_issues": [], "not_indexed": []}
    mf = d / "manifest.json"
    if not d.is_dir():
        res["caption_issues"].append(("（整個 run）", ["目錄不存在"]))
        return res
    if not mf.exists():
        res["caption_issues"].append(("manifest.json", ["找不到 manifest.json"]))
        return res
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        io.quarantine(mf, why="manifest JSON 壞損")
        res["caption_issues"].append(("manifest.json", ["manifest 壞損，已隔離"]))
        return res

    imgs = data.get("images") or []
    kept = _images_on_disk(d)
    listed = [str(it.get("file") or "") for it in imgs]
    res["listed"] = len(imgs)
    res["kept"] = len(kept)
    res["rejected"] = sum(1 for f in listed if f not in kept)
    res["rejected_names"] = [f for f in listed if f not in kept]
    res["marked_reject"] = sum(1 for it in imgs if it.get("keep") is False)
    res["not_indexed"] = sorted(kept - set(listed))

    for it in imgs:
        name = str(it.get("file") or "")
        if name not in kept:
            continue                       # 已剔除 → 不是問題（策展結果）
        cap = (d / name).with_suffix(".txt")
        try:
            text = cap.read_text(encoding="utf-8")
        except OSError:
            res["no_caption"].append(name)
            continue
        probs = caption_problems(text, spec)
        if probs:
            res["caption_issues"].append((name, probs))

    neg = (data.get("config") or {}).get("negative")
    if neg is not None:
        res["negative_issues"] = negative_problems(neg, spec)
    return res


def trash_lookup(names):
    """看 ~/.Trash 裡有沒有這些檔（macOS：人工刪除會進垃圾桶）。

    只做**提示**用——本專案手動剔圖是正常流程（剔除的檔通常還在垃圾桶，之後想救回還救得回）。
    """
    if not names:
        return 0
    t = Path.home() / ".Trash"
    if not t.is_dir():
        return 0
    want = set(names)
    try:
        return sum(1 for p in t.iterdir() if p.name in want)
    except OSError:
        return 0


# ── 輸出 ─────────────────────────────────────────────────────────────

def cmd_caption(args):
    """補齊／修正訓練集字幕：缺失的從 manifest prompt 重建，缺觸發詞的補上。"""
    ch = load_character(args.slug)
    if not ch:
        sys.exit(f"找不到角色檔 prompts/character/{args.slug}.json")
    lora = ch.get("lora") or {}
    trigger = lora.get("trigger", "")
    runs = args.run or lora.get("dataset_runs") or []
    if not runs:
        sys.exit("沒有要處理的 run（用 --run 指定，或角色檔要有 lora.dataset_runs）")

    counts = {"reconstruct": 0, "add-trigger": 0, "reconstruct+trigger": 0,
              "ok": 0, "no-source": 0}
    for rname in runs:
        d = VISUALS_DIR / rname
        mf = d / "manifest.json"
        if not mf.exists():
            print(f"⚠️  {rname}：找不到 manifest.json，略過")
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            io.quarantine(mf, why="manifest JSON 壞損")
            print(f"⚠️  {rname}：manifest 壞損，已隔離")
            continue
        kept = _images_on_disk(d)
        print(f"◆ {rname}")
        for it in data.get("images") or []:
            name = str(it.get("file") or "")
            if name not in kept:
                continue                       # 已剔除 → 不處理
            txt = (d / name).with_suffix(".txt")
            existing = txt.read_text(encoding="utf-8") if txt.exists() else ""
            new, action = caption_plan(existing, it.get("prompt"), trigger)
            if new is None:
                counts["no-source"] += 1
                print(f"  ⚠️  {name}：manifest 沒有 prompt，無法重建")
                continue
            counts[action] = counts.get(action, 0) + 1
            if action == "ok":
                continue
            print(f"  {name}：{action}")
            if not args.dry_run:
                io.atomic_write_text(txt, new.rstrip() + "\n")

    print()
    print(f"重建 {counts['reconstruct']}、補觸發詞 {counts['add-trigger']}、"
          f"兩者皆 {counts['reconstruct+trigger']}、已正確 {counts['ok']}"
          + (f"、無來源 {counts['no-source']}" if counts["no-source"] else "")
          + ("　（dry-run，未寫檔）" if args.dry_run else ""))


def build_kohya_toml(name, trigger, *, remote="/workspace", base_model="DreamShaperXL-v1.0-Alpha2.safetensors",
                     resolution="832,1216", repeats=10, epochs=10, dim=32, alpha=16, seed=42):
    """產生 kohya_ss（`sdxl_train_network.py`）用的 TOML。

    `resolution` 預設 `832,1216` ＝ 你資料集的實際尺寸（不要去改成 1024 方形，
    否則會 resize 掉細節）。步數 ＝ 張數 × repeats × epochs。
    """
    return f"""# kohya_ss / sd-scripts：SDXL LoRA（{name}）
# 啟動：accelerate launch --num_cpu_threads_per_process 8 sdxl_train_network.py --config_file {name}.toml
# 註：kohya 版本不同欄位可能略有差異；以你安裝版本 GUI 匯出的 config 為準。

[model]
pretrained_model_name_or_path = "{remote}/models/{base_model}"
sdxl = true

[dataset]
resolution = "{resolution}"
batch_size = 1
enable_bucket = true
min_bucket_reso = 512
max_bucket_reso = 1536
caption_extension = ".txt"
cache_latents = true
cache_latents_to_disk = true

[[dataset.subsets]]
image_dir = "{remote}/dataset/{repeats}_{trigger}"
num_repeats = {repeats}

[network]
network_module = "networks.lora"
network_dim = {dim}
network_alpha = {alpha}

[optimizer]
optimizer_type = "AdamW8bit"
learning_rate = 1e-4
unet_lr = 1e-4
text_encoder_lr = 4e-5
lr_scheduler = "cosine_with_restarts"
lr_warmup_steps = 50

[training]
max_train_epochs = {epochs}
save_every_n_epochs = 2
mixed_precision = "fp16"
save_precision = "fp16"
seed = {seed}
output_dir = "{remote}/output"
output_name = "{name}"
logging_dir = "{remote}/logs"
"""


def build_export_readme(name, trigger, images, steps, remote="/workspace"):
    return f"""# LoRA 訓練包：{name}（trigger = `{trigger}`）

- 影像 **{images} 張**（每張都有字幕 `.txt`，全部前置 trigger token `{trigger}`）
- 預估步數 ＝ {images} × repeats × epochs ＝ **約 {steps} 步**（見 `{name}.toml`）
- 基底：`DreamShaperXL-v1.0-Alpha2.safetensors`（與產線一致；授權 CreativeML Open RAIL++-M，可商用）

## RunPod 步驟

1. 開一個 GPU pod（SDXL LoRA：**24GB 夠**，如 4090 / A5000；template 選含 kohya_ss 或 sd-scripts 的映像）。
2. 把基底模型放到 `{remote}/models/`（三選一，都免費）：
   - Civitai 下載（需**免費** API token）：`https://civitai.com/api/download/models/<id>?token=<你的token>`
   - HuggingFace `Lykon/dreamshaper-xl-1-0`（免 token，但為 diffusers 格式）
   - 從本機上傳本來的 `DreamShaperXL-v1.0-Alpha2.safetensors`
3. 上傳本包，解開到 `{remote}/`（會得到 `dataset/`、`{name}.toml`）。
4. `cd <kohya_ss 或 sd-scripts 目錄>` 然後：
   `accelerate launch --num_cpu_threads_per_process 8 sdxl_train_network.py --config_file {remote}/{name}.toml`
5. 訓練完下載 `{remote}/output/{name}.safetensors` → 放本機 `~/ComfyUI/models/loras/`。
6. 回到本專案使用（記得先登錄授權，見下）：
   `./scripts/lofi visual --lora {name}.safetensors --lora-weight 0.8 --prompt "{trigger}, a sleek black cat ..."`

## 花費

大約 **1–2.5 小時**（依 GPU），RunPod 4090 級約 **US$1–3**。訓練中若 loss 已明顯收斂就提早停。

## 注意

- 字幕一定要有 trigger token（`{trigger}`），否則模型會把「黑貓」這個通用概念學進去。
- 授權：訓練資料是本專案自產，基底為可商用；**但新訓好的 LoRA 要登錄 `config.MODEL_LICENSES` 才會被
  `--lora` 放行**（本專案對未知授權 fail-closed）。
- 本機 16GB 無法訓練，只能推論；這包是為了雲端訓練而生的。
"""


def cmd_export(args):
    """把訓練集打包成 RunPod／kohya_ss 可直接吃的形式（影像＋字幕＋TOML＋說明＋zip）。"""
    ch = load_character(args.slug)
    if not ch:
        sys.exit(f"找不到角色檔 prompts/character/{args.slug}.json")
    lora = ch.get("lora") or {}
    trigger = lora.get("trigger") or args.slug
    runs = args.run or lora.get("dataset_runs") or []
    if not runs:
        sys.exit("沒有要匯出的 run（用 --run 指定，或角色檔要有 lora.dataset_runs）")

    dest = LORA_DIR / args.name
    img_dir = dest / "dataset" / f"{args.repeats}_{trigger}"
    if dest.exists() and any(dest.iterdir()) and not args.force:
        sys.exit(f"{dest} 已存在（--force 可覆蓋）")

    copied, missing, no_prompt = 0, [], []
    for r in runs:
        d = VISUALS_DIR / r
        for name in sorted(_images_on_disk(d)):
            txt = (d / name).with_suffix(".txt")
            if not txt.exists():
                missing.append(f"{r}/{name}")
                continue
            img_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d / name, img_dir / name)
            shutil.copy2(txt, img_dir / f"{Path(name).stem}.txt")
            copied += 1
        if not d.is_dir():
            no_prompt.append(r)

    for r in no_prompt:
        print(f"⚠️  {r}：目錄不存在")

    if not copied:
        sys.exit("沒有匯出任何一張（先跑 `lofi character caption` 補字幕）")

    steps = copied * args.repeats * args.epochs
    (dest / f"{args.name}.toml").write_text(
        build_kohya_toml(args.name, trigger, remote=args.remote, base_model=args.base_model,
                         resolution=args.resolution, repeats=args.repeats,
                         epochs=args.epochs, dim=args.dim, alpha=args.alpha),
        encoding="utf-8")
    (dest / "README.md").write_text(
        build_export_readme(args.name, trigger, copied, steps, remote=args.remote),
        encoding="utf-8")

    zip_path = LORA_DIR / f"{args.name}_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dest.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(dest))

    mn = lora.get("min_images", DEFAULT_MIN_IMAGES)
    print(f"◆ LoRA 訓練包：{args.name}（trigger = {trigger}）")
    print(f"  影像 {copied} 張 → {_rel(dest)}")
    print(f"  預估步數 {copied} × {args.repeats} × {args.epochs} = {steps}")
    print(f"  TOML：{args.name}.toml（resolution {args.resolution}，dim/alpha {args.dim}/{args.alpha}）")
    print(f"  壓縮檔：{_rel(zip_path)}（{zip_path.stat().st_size / 1048576:.1f} MB）→ 上傳 RunPod")
    if missing:
        print(f"  ⚠️  {len(missing)} 張缺字幕，已跳過（先跑 `lofi character caption`）：{', '.join(missing[:3])}")
    if copied < mn:
        print(f"  ❌ 只有 {copied} 張，低於建議下限 {mn} → 先補圖再訓練（別花這個錢）")


def _rel(p):
    """相對於 ROOT 的路徑（output/ 是外接 symlink，不能用 resolve）。"""
    from lofi.paths import ROOT
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def cmd_show(args):
    ch = load_character(args.slug)
    if not ch:
        sys.exit(f"找不到角色檔 prompts/character/{args.slug}.json")
    d = ch.get("design") or {}
    print(f"◆ {ch.get('name')}（artist：{ch.get('artist')}）")
    print(f"  畫風：{ch.get('art_style')}")
    for k in ("fur", "eyes", "socks"):
        if d.get(k):
            print(f"  {k:<8}{d[k]}")
    for dec in ch.get("design_decisions") or []:
        print(f"  定案 {dec.get('date')}：{dec.get('decision')}")
    lora = ch.get("lora") or {}
    print(f"\n◆ LoRA：{lora.get('target')}（{lora.get('status')}）")
    print(f"  訓練集 run：{', '.join(lora.get('dataset_runs') or [])}"
          f"　下限 {lora.get('min_images', DEFAULT_MIN_IMAGES)} 張")
    print(f"\n◆ 旅程：{ch.get('journey', {}).get('concept', '')}")
    for k, v in (ch.get("journey", {}).get("stops") or {}).items():
        print(f"  {k:<14}{v}")
    print("\n（看訓練集狀態請用 `lofi character check`）")


def cmd_check(args):
    ch = load_character(args.slug)
    if not ch:
        sys.exit(f"找不到角色檔 prompts/character/{args.slug}.json")
    spec = ch.get("caption_check") or {}
    lora = ch.get("lora") or {}
    runs = lora.get("dataset_runs") or []
    if not runs:
        sys.exit("角色檔沒有 lora.dataset_runs，不知道要檢查哪些 run")
    if not spec:
        sys.exit("角色檔沒有 caption_check，無法判斷一致性")

    results = [check_run(VISUALS_DIR / r, spec) for r in runs]

    if args.json:
        print(json.dumps({"character": ch.get("name"), "runs": results,
                          "min_images": lora.get("min_images", DEFAULT_MIN_IMAGES)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"◆ LoRA 訓練集一致性：{ch.get('name')}（{', '.join(runs)}）")
        print("─" * 74)
        for r in results:
            print(f"  {r['run']:<7}生成 {r['listed']:>3} 張　→ 留 {r['kept']:>3} 張"
                  f"（剔除 {r['rejected']:>3}）　字幕 {r['kept'] - len(r['no_caption'])}/{r['kept']}")
        tot_kept = sum(r["kept"] for r in results)
        tot_rej = sum(r["rejected"] for r in results)
        mn = lora.get("min_images", DEFAULT_MIN_IMAGES)
        print(f"  {'合計':<7}留存 {tot_kept} 張（剔除 {tot_rej}）")
        print()

        problems = 0
        for r in results:
            for f, probs in r["caption_issues"]:
                problems += 1
                print(f"  {ICON['error']} {r['run']}/{f}：{'；'.join(probs)}")
            if r["no_caption"]:
                problems += 1
                print(f"  {ICON['error']} {r['run']}：{len(r['no_caption'])} 張沒有字幕"
                      f"（例：{r['no_caption'][0]}）→ 這張等於沒有訓練說明")
            if r["negative_issues"]:
                problems += 1
                print(f"  {ICON['error']} {r['run']} 負向詞：{'；'.join(r['negative_issues'])}")
            if r["not_indexed"]:
                print(f"  ⚠️  {r['run']}：{len(r['not_indexed'])} 張在磁碟但 manifest 沒記"
                      f"（例：{r['not_indexed'][0]}）→ 生成時可能漏記")

        # 數量
        if tot_kept < mn:
            problems += 1
            print(f"  {ICON['error']} 訓練集只有 {tot_kept} 張，低於建議下限 {mn} 張"
                  f" → 先補圖再訓練")
        if tot_rej and not any(r["marked_reject"] for r in results):
            in_trash = trash_lookup([f for r in results for f in r["rejected_names"]])
            extra = f"（其中 {in_trash} 張還在垃圾桶，想救回還救得回）" if in_trash else ""
            print(f"  ⚠️  剔除了 {tot_rej} 張，但 manifest 沒記錄『為什麼剔除』{extra}"
                  f" → 下次看不出標準（音樂有 status/review，視覺還沒有）")

        print()
        if problems:
            print(f"{ICON['error']} 有 {problems} 項要處理（見上）；未就緒，先別開始訓練。")
        else:
            print("✅ 訓練集一致，可以開始訓練。")
    if any(r["caption_issues"] or r["no_caption"] or r["negative_issues"] for r in results):
        sys.exit(1)
    if sum(r["kept"] for r in results) < lora.get("min_images", DEFAULT_MIN_IMAGES):
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="主角定裝檢視 + LoRA 訓練集一致性檢查")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--slug", default="moni", help="角色檔名（prompts/character/<slug>.json）")

    p1 = sub.add_parser("show", help="印出定裝與旅程")
    common(p1)
    p1.set_defaults(func=cmd_show)

    p2 = sub.add_parser("check", help="檢查 LoRA 訓練集（檔案/字幕/一致性/數量）")
    common(p2)
    p2.add_argument("--json", action="store_true")
    p2.set_defaults(func=cmd_check)

    p3 = sub.add_parser("caption", help="補齊/修正字幕（缺失的從 manifest prompt 重建＋補觸發詞）")
    common(p3)
    p3.add_argument("--run", nargs="*", default=None,
                    help="只處理這些 run（預設角色檔的 lora.dataset_runs）")
    p3.add_argument("--dry-run", action="store_true", help="只印會做什麼，不寫檔")
    p3.set_defaults(func=cmd_caption)

    p4 = sub.add_parser("export", help="打包 RunPod／kohya_ss 訓練包（影像＋字幕＋TOML＋zip）")
    common(p4)
    p4.add_argument("--name", default="moni", help="輸出名稱（= LoRA 檔名）")
    p4.add_argument("--run", nargs="*", default=None, help="只匯出這些 run（預設角色檔的 dataset_runs）")
    p4.add_argument("--remote", default="/workspace", help="RunPod 上的工作根目錄")
    p4.add_argument("--base-model", default="DreamShaperXL-v1.0-Alpha2.safetensors",
                    help="RunPod 上的基底模型檔名（放 <remote>/models/）")
    p4.add_argument("--resolution", default="832,1216", help="訓練解析度（預設對齊資料集原尺寸）")
    p4.add_argument("--repeats", type=int, default=10, help="kohya 的 num_repeats")
    p4.add_argument("--epochs", type=int, default=10, help="max_train_epochs")
    p4.add_argument("--dim", type=int, default=32, help="network_dim")
    p4.add_argument("--alpha", type=int, default=16, help="network_alpha")
    p4.add_argument("--force", action="store_true", help="覆蓋既有輸出")
    p4.set_defaults(func=cmd_export)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
