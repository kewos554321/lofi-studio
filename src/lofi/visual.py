#!/usr/bin/env python3
"""
generate_visual.py — 用 ComfyUI 生 lofi 場景圖（預設 SDXL；--ckpt 可換其他 checkpoint）。

產出的圖片放到 assets/visuals/<run-id>/（一批一夾）。命名規則：資料夾 `<kind><NN>`、檔名
`<run-id>_<NN>[_<slug>].<ext>`（kind：`ref` 主角定裝參考／`ds` 訓練集／`sc` 場景風格測試；
例 `ref04_01_gold-socks.png`、`ds02_07_walk-side-fireplace.png`）。完整規則見 docs/NAMING.md。
夾內 manifest.json 為「一個 run 一份」：
固定配置（模型/尺寸/steps/cfg…）集中在 config 只寫一次，逐檔會變的 prompt/seed 列在 images，
所以同一 run 再跑新圖或改某張的 prompt 都只是併寫（update_manifest），不會蓋掉舊紀錄。
再交給 make_visual_loop.sh 做無縫循環。--run-id 未給時自動編號（ref05 之後給 ref06）。

用法:
  python3 scripts/generate_visual.py                          # 預設場景，1 張（自動編號 scNN）
  python3 scripts/generate_visual.py --run-id sc07 --count 3  # 一批一夾：assets/visuals/sc07/
  python3 scripts/generate_visual.py --slug window --count 3  # 檔名帶標籤：scNN_01_window.png
  python3 scripts/generate_visual.py --count 3                # 生 3 張（不同 seed）
  python3 scripts/generate_visual.py --prompt "your scene"    # 自訂 prompt
  python3 scripts/generate_visual.py --size 768x512
  python3 scripts/generate_visual.py --upscale RealESRGAN_x4plus_anime_6B.pth  # AI 放大（768->1536）
  # SDXL 約 2.5 分鐘／張（載入吃 RAM）；--ckpt 可換基底
  python3 scripts/generate_visual.py --ckpt DreamShaperXL-v1.0-Alpha2.safetensors \
      --size 832x1216 --steps 28 --cfg 6.5
  # 動漫 tag 系模型（NoobAI / Illustrious 家族）建議加 --clip-skip 2
  python3 scripts/generate_visual.py --dry-run

前置：ComfyUI 需執行中，且對應 checkpoint 已在 models/checkpoints。
預設 checkpoint：DreamShaperXL-v1.0-Alpha2.safetensors（SDXL，半寫實插畫；見 models/README.md）。
放大模型放在 ~/ComfyUI/models/upscale_models/（見 download_p2_models.sh）。
"""
import argparse, json, os, random, re, shutil, sys, time, unicodedata
from datetime import datetime
from pathlib import Path

from lofi import comfy
from lofi import io
from lofi import media
from lofi.config import (COMFY_OUTPUT, COMFY_URL, IMAGE_CKPT, MIN_FREE_GB,
                         check_commercial, model_license)
from lofi.lock import gpu_lock
from lofi.paths import VISUALS_DIR

VIS_DIR = VISUALS_DIR
COMFY_OUT = COMFY_OUTPUT


def sanitize_run_id(s):
    """run-id 只留安全字元（避免路徑跳脫、空白）。"""
    s = (s or "").strip().replace(" ", "-")
    return re.sub(r"[^A-Za-z0-9._-]", "", s).strip(".-")


# ── 命名規則 ────────────────────────────────────────────────────────────
# 資料夾：<kind><NN>           例 ref05 / ds02 / sc03
# 檔名　：<run-id>_<NN>[_<slug>].<ext>
#         例 ds02_07_sit-fireplace.png、ref05_02_anim-blink.gif
# 原則：run-id 開頭（同批排在一起、看名知批）；NN 固定位數（照生成順序）；
#       slug 短標籤（小寫英數，一眼看出內容）；衍生檔 slug 用保留字首
#       anim-（動畫）/ cmp-（對照合成）/ mask-（遮罩）/ fix-（後處理）。
VIS_KINDS = ("ref", "ds", "sc")          # ref=主角定裝參考 ds=訓練集 sc=場景風格測試
VIS_KIND_LABELS = {"ref": "定裝參考", "ds": "訓練集", "sc": "場景測試"}


def parse_run_id(run_id):
    """拆 run-id 的用途代號 -> ('ref', 5)；不符規則回 ('', 0)。"""
    m = re.match(r"^([A-Za-z]+)0*(\d+)$", run_id or "")
    return (m.group(1).lower(), int(m.group(2))) if m else ("", 0)


def next_run_id(kind="sc", vis_dir=None):
    """下一個 run-id：ref05 之後給 ref06（同 kind 取最大號 +1，不必自己記數）。"""
    d = Path(vis_dir) if vis_dir else VIS_DIR
    top = 0
    for p in (d.iterdir() if d.is_dir() else []):
        if p.is_dir():
            k, n = parse_run_id(p.name)
            if k == kind:
                top = max(top, n)
    return f"{kind}{top + 1:02d}"


def sanitize_slug(s, maxlen=40):
    """slug 只留小寫英數與 '-'（給檔名用；中文/括號/空白會被清掉）。"""
    s = (s or "").strip().lower().replace("_", "-").replace(" ", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")[:maxlen].strip("-")


def output_filename(run_id, seq, slug="", ext="png", digits=2):
    """標準輸出檔名：<run-id>_<NN>[_<slug>].<ext>。"""
    parts = [sanitize_run_id(run_id), f"{seq:0{digits}d}"]
    s = sanitize_slug(slug)
    if s:
        parts.append(s)
    return "_".join(p for p in parts if p) + "." + ext.lstrip(".").lower()


def _name_pattern(run_id=""):
    head = re.escape(sanitize_run_id(run_id)) + "_" if run_id else r"[A-Za-z0-9._-]+?_"
    return head


def parse_output_name(name, run_id=""):
    """拆標準檔名 -> (run-id, seq, slug, ext)；不符規則回 None。"""
    stem, _, ext = Path(name).name.partition(".")
    m = re.match(r"^" + _name_pattern(run_id) + r"(\d{2,})(?:_([a-z0-9-]+))?$", stem)
    if not m:
        return None
    rid = sanitize_run_id(run_id) or stem[: m.start(1)].rstrip("_")
    return rid, int(m.group(1)), m.group(2) or "", ext


def next_seq(out_dir, run_id=""):
    """下一個可用序號：掃資料夾裡既有的 <run-id>_<NN>_*。"""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return 1
    pat = re.compile(r"^" + _name_pattern(run_id) + r"(\d{2,})(?:_|$)")
    top = 0
    for f in out_dir.iterdir():
        m = pat.match(f.stem)
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 2


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_manifest(out_dir, meta):
    """原樣寫入 manifest.json（底層介面；併寫請用 update_manifest）。"""
    io.atomic_write_json(Path(out_dir) / MANIFEST_NAME, meta)


def upgrade_manifest(raw):
    """v1（扁平：模型參數＋prompt 全在最上層）-> v2（config 共用 + images 逐檔）。

    升級重點：固定配置收進 config（只存一次），prompt 下放到每個 image 條目。
    """
    if not isinstance(raw, dict):
        return {}
    if raw.get("manifest_version") == MANIFEST_VERSION or "config" in raw:
        raw.setdefault("manifest_version", MANIFEST_VERSION)
        raw.setdefault("config", {})
        raw.setdefault("images", [])
        return raw
    cfg = {k: v for k, v in raw.items()
           if k not in ("images", "run_id", "tool", "created_at", "updated_at",
                        "prompt", "count", "seed")}
    prompt = raw.get("prompt", "")
    imgs = []
    for im in raw.get("images", []) or []:
        e = dict(im) if isinstance(im, dict) else {"file": str(im)}
        e.setdefault("prompt", prompt)
        imgs.append(e)
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": raw.get("run_id", ""),
        "tool": raw.get("tool", ""),
        "created_at": raw.get("created_at", ""),
        "updated_at": raw.get("created_at", ""),
        "config": cfg,
        "images": imgs,
    }


def load_manifest(out_dir):
    """讀 manifest；舊的扁平格式會自動升級成 v2。"""
    p = Path(out_dir) / MANIFEST_NAME
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return upgrade_manifest(raw)


def update_manifest(out_dir, run_id="", tool="", caller="", config=None, images=None,
                    created_at=""):
    """併寫 manifest：一個 run 一份，重跑不會蓋掉舊紀錄。

    - config：該批固定、所有圖共用的設定（模型/尺寸/steps/cfg/negative…）只存一次
    - images：逐檔清單 [{file, seed, prompt}]；同一 run 再跑會依 file 新增或就地更新
    - caller：誰呼叫的（腳本名），方便回頭查出這筆是哪支程式寫的
    - created_at：補寫時用（回填舊 run 要沿用原始時間，不能用「現在」）
    空字串不會蓋掉既有值；created_at 一旦有了就不動、updated_at 更新。回傳併寫後的 manifest。
    """
    out_dir = Path(out_dir)
    m = load_manifest(out_dir)
    now = _now_iso()
    m["manifest_version"] = MANIFEST_VERSION
    if created_at and not m.get("created_at"):
        m["created_at"] = created_at
    m.setdefault("created_at", now)
    m["updated_at"] = now
    if run_id:
        m["run_id"] = run_id
    if tool:
        m["tool"] = tool
    if caller:
        m["caller"] = caller
    cfg = m.setdefault("config", {})
    for k, v in (config or {}).items():
        if v is None:
            continue
        if v == "" and cfg.get(k) not in (None, ""):
            continue                      # 不用空值蓋掉既有設定
        cfg[k] = v
    imgs = m.setdefault("images", [])
    index = {e["file"]: e for e in imgs if isinstance(e, dict) and e.get("file")}
    for e in (images or []):
        if not isinstance(e, dict) or not e.get("file"):
            continue
        cur = index.get(e["file"])
        if cur is None:                   # 新檔：append
            cur = dict(e)
            imgs.append(cur)
            index[e["file"]] = cur
        else:                             # 既有檔：就地更新（可改 prompt/seed）
            for k, v in e.items():
                if v is not None:
                    cur[k] = v
    write_manifest(out_dir, m)
    return m


def read_png_workflow(path):
    """讀 ComfyUI 寫進 PNG 的 workflow（tEXt 區塊 'prompt'）；沒有就回 None。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    while pos + 12 <= len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"tEXt":
            key, _, val = body.partition(b"\x00")
            if key == b"prompt":
                try:
                    return json.loads(val.decode("utf-8", "replace"))
                except ValueError:
                    return None
        elif typ == b"IEND":
            break
        pos += 12 + ln
    return None


def guess_tool(wf):
    """看 workflow 用了哪些節點，猜是哪支產生器寫的。"""
    types = {n.get("class_type") for n in (wf or {}).values() if isinstance(n, dict)}
    if "IPAdapterAdvanced" in types or "IPAdapterUnifiedLoader" in types:
        return "sdxl-ipadapter"
    if "UnetLoaderGGUF" in types:
        return "z-image-turbo-gguf"
    return "lofi.visual"


def manifest_from_workflow(wf):
    """把 ComfyUI 的 workflow 濃縮成 (config, image_entry)；用來反推舊圖的紀錄。"""
    nodes = {str(k): v for k, v in (wf or {}).items() if isinstance(v, dict)}

    def of(ctype):
        return [n for n in nodes.values() if n.get("class_type") == ctype]

    def source(ref):
        return nodes.get(str(ref[0])) if isinstance(ref, (list, tuple)) and ref else None

    cfg, entry = {}, {}
    for n in of("CheckpointLoaderSimple"):
        cfg.setdefault("ckpt", n.get("inputs", {}).get("ckpt_name", ""))
    for n in of("UnetLoaderGGUF"):
        cfg.setdefault("unet", n.get("inputs", {}).get("unet_name", ""))
    for n in of("CLIPLoaderGGUF"):
        cfg.setdefault("clip", n.get("inputs", {}).get("clip_name", ""))
        cfg.setdefault("clip_type", n.get("inputs", {}).get("type", ""))
    for n in of("VAELoader"):
        cfg.setdefault("vae", n.get("inputs", {}).get("vae_name", ""))
    for n in of("ModelSamplingAuraFlow"):
        cfg.setdefault("shift", n.get("inputs", {}).get("shift"))
    for n in of("CLIPSetLastLayer"):
        stop = n.get("inputs", {}).get("stop_at_clip_layer")
        if isinstance(stop, int) and stop < 0:
            cfg.setdefault("clip_skip", -stop)

    sampler = of("KSampler") or of("KSamplerAdvanced")
    if sampler:
        s = sampler[0].get("inputs", {})
        for src, dst in (("steps", "steps"), ("cfg", "cfg"),
                         ("sampler_name", "sampler"), ("scheduler", "scheduler"),
                         ("denoise", "denoise")):
            if s.get(src) is not None:
                cfg[dst] = s[src]
        if isinstance(s.get("seed"), int):
            entry["seed"] = s["seed"]          # seed 逐檔，不放到共用 config
        for side in ("positive", "negative"):
            node = source(s.get(side))
            if not node:
                continue
            text = node.get("inputs", {}).get("text")
            if not text and node.get("class_type") == "ConditioningZeroOut":
                text = ""                      # Z-Image 的負向是 zero-out，沒有文字
            if text:
                if side == "positive":
                    entry["prompt"] = text
                else:
                    cfg["negative"] = text

    ipa = of("IPAdapterAdvanced")
    if ipa:
        s = ipa[0].get("inputs", {})
        for src, dst in (("weight", "weight"), ("weight_type", "weight_type"),
                         ("start_at", "start"), ("end_at", "end"), ("embeds_scaling", "embeds_scaling")):
            if s.get(src) is not None:
                cfg[dst] = s[src]
        node = source(s.get("image"))
        if node and node.get("class_type") == "LoadImage":
            cfg["ref"] = node.get("inputs", {}).get("image", "")
    for n in of("IPAdapterUnifiedLoader"):
        cfg.setdefault("preset", n.get("inputs", {}).get("preset", ""))

    loads = [n.get("inputs", {}).get("image", "") for n in of("LoadImage")]
    if loads and not ipa:                      # img2img / 局部重繪的起點圖與遮罩
        cfg.setdefault("init_image", loads[0])
        if len(loads) > 1:
            cfg.setdefault("mask", loads[1])
    for n in of("GrowMask"):
        cfg.setdefault("grow", n.get("inputs", {}).get("expand"))
    return cfg, entry


def backfill_manifest(out_dir, tool=""):
    """掃資料夾裡的圖，從內嵌 workflow 反推 config/prompt 補寫 manifest（救舊 run）。

    回傳補到的張數。已有 manifest 的圖會就地併寫（不覆蓋既有值）。
    created_at 用**最早的圖檔時間**，不要寫成「現在」——不然看不出這批是哪時候跑的。
    """
    out_dir = Path(out_dir)
    files = sorted(out_dir.glob("*.png"))
    oldest = min((f.stat().st_mtime for f in files), default=0)
    created = (datetime.fromtimestamp(oldest).astimezone().isoformat(timespec="seconds")
               if oldest else "")
    n = 0
    for f in files:
        wf = read_png_workflow(f)
        if not wf:
            continue
        cfg, entry = manifest_from_workflow(wf)
        if not entry.get("prompt") and entry.get("seed") is None:
            continue
        entry["file"] = f.name
        update_manifest(out_dir, run_id=out_dir.name, tool=tool or guess_tool(wf),
                        caller="backfill", created_at=created, config=cfg, images=[entry])
        n += 1
    return n

# ── 執行資訊：把「哪個模型在跑、什麼參數、輸出到哪」講清楚 ──────────────
_FIELD_LABELS = {
    "ckpt": "checkpoint", "unet": "UNet", "clip": "CLIP", "clip_type": "CLIP 型別",
    "vae": "VAE", "lora": "LoRA", "lora_weight": "LoRA 權重",
    "size": "尺寸", "steps": "步數", "cfg": "CFG", "shift": "shift",
    "sampler": "取樣器", "scheduler": "排程", "clip_skip": "clip skip",
    "denoise": "重繪強度", "upscale": "放大模型", "upscale_out": "放大後尺寸",
    "init_image": "起點圖", "mask": "遮罩", "grow": "遮罩擴張",
    "ref": "參考圖", "ref_image": "參考圖", "weight": "IPAdapter 權重",
    "weight_type": "權重型別", "preset": "IPAdapter preset",
    "embeds_scaling": "embeds 縮放", "start": "權重起點", "end": "權重終點",
    "license": "模型授權", "source": "模型來源",
}
_FIELD_ORDER = ("ckpt", "unet", "clip", "clip_type", "vae", "lora", "lora_weight",
                "size", "steps", "cfg", "shift", "sampler", "scheduler", "clip_skip",
                "denoise", "upscale", "upscale_out", "init_image", "mask", "grow",
                "ref", "ref_image", "weight", "weight_type", "preset",
                "embeds_scaling", "start", "end", "license", "source")


def model_tag(config, maxlen=34):
    """抓出「跑的是哪顆模型」的短標籤（逐張輸出時帶著，方便中途看 log）。"""
    for k in ("ckpt", "unet", "model"):
        v = (config or {}).get(k)
        if not v:
            continue
        v = str(v).rsplit("/", 1)[-1]
        for ext in (".safetensors", ".gguf", ".ckpt", ".sft", ".pt"):
            if v.endswith(ext):
                v = v[: -len(ext)]
                break
        return v if len(v) <= maxlen else v[: maxlen - 1] + "…"
    return "(未指定模型)"


def format_run_fields(config, maxlen=60):
    """把 config 排成給人看的欄位清單（跳過空值）。"""
    items = []
    for k in _FIELD_ORDER:
        v = (config or {}).get(k)
        if v is None or v == "" or v is False:
            continue
        s = str(v)
        items.append((_FIELD_LABELS.get(k, k), s if len(s) <= maxlen else s[:maxlen - 1] + "…"))
    for k, v in (config or {}).items():
        if k in _FIELD_ORDER or k in ("negative", "prompt"):
            continue
        if v is None or v == "" or v is False:
            continue
        items.append((k, str(v)))
    return items


def print_run_header(tool, run_id, out_dir, config, count=None):
    """執行前清楚印出：工具、run-id、模型、參數、輸出位置。"""
    line = "─" * 64
    print(line)
    head = f"▶ {tool}    run-id={run_id}"
    if count:
        head += f"    共 {count} 張"
    print(head)
    print(f"  模型：{model_tag(config)}")
    print(f"  輸出：{out_dir}")
    for name, val in format_run_fields(config):
        if name in ("checkpoint", "UNet"):
            continue                      # 上面已單獨印「模型」
        print(f"  {name}：{val}")
    neg = (config or {}).get("negative", "")
    if neg:
        neg = str(neg)
        print(f"  negative：{neg[:56]}{'…' if len(neg) > 56 else ''}")
    print(line)


DEFAULT_POS = ("1girl, solo, anime girl sitting at a cozy desk by the window at night, "
               "warm desk lamp, coffee mug, books, potted plant, long hair, looking at viewer, "
               "lo-fi anime illustration, soft warm lighting, detailed background, "
               "muted colors, nostalgic, masterpiece, best quality")
DEFAULT_NEG = ("lowres, blurry, bad anatomy, bad hands, extra digits, fewer digits, bad proportions, "
               "2girls, multiple girls, extra limbs, watermark, signature, text, error, jpeg artifacts")


def build_prompt(pos, neg, w, h, seed, steps, cfg, prefix, ckpt=IMAGE_CKPT,
                 upscale_model="", up_out=None, clip_skip=0,
                 sampler="dpmpp_2m", scheduler="karras",
                 init_image="", denoise=1.0):
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }
    # 起點：img2img 用參考圖經 VAE 編碼（denoise < 1 才有效果）；否則從空 latent 開始
    if init_image:
        g["14"] = {"class_type": "LoadImage", "inputs": {"image": init_image}}
        g["15"] = {"class_type": "VAEEncode",
                   "inputs": {"pixels": ["14", 0], "vae": ["4", 2]}}
        latent_src = ["15", 0]
    else:
        g["5"] = {"class_type": "EmptyLatentImage",
                  "inputs": {"width": w, "height": h, "batch_size": 1}}
        latent_src = ["5", 0]
    # clip skip：動漫 tag 系模型（NoobAI/Illustrious）建議 2；SDXL base／DreamShaper 一般用 0
    clip_src = ["4", 1]
    if clip_skip and clip_skip > 1:
        g["13"] = {"class_type": "CLIPSetLastLayer",
                   "inputs": {"clip": ["4", 1], "stop_at_clip_layer": -clip_skip}}
        clip_src = ["13", 0]
    g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": clip_src}}
    g["7"] = {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": clip_src}}
    g["3"] = {"class_type": "KSampler", "inputs": {
                  "model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                  "sampler_name": sampler, "scheduler": scheduler,
                  "positive": ["6", 0], "negative": ["7", 0],
                  "latent_image": latent_src, "denoise": denoise}}
    src = ["8", 0]
    if upscale_model:
        ow, oh = up_out or (w * 2, h * 2)
        g["10"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}}
        g["11"] = {"class_type": "ImageUpscaleWithModel",
                   "inputs": {"upscale_model": ["10", 0], "image": ["8", 0]}}
        g["12"] = {"class_type": "ImageScale",
                   "inputs": {"image": ["11", 0], "width": ow, "height": oh,
                              "upscale_method": "lanczos", "crop": "disabled"}}
        src = ["12", 0]
    g["9"] = {"class_type": "SaveImage", "inputs": {"images": src, "filename_prefix": prefix}}
    return g


# ── 批次總表：一眼看出哪批是什麼、什麼時候跑的（時間不塞進檔名）──────────
def _disp_width(s):
    """字串顯示寬度（中文算 2 格），用來對齊表格。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, width, right=False):
    gap = " " * max(0, width - _disp_width(s))
    return gap + str(s) if right else str(s) + gap


def age_text(iso, now=None):
    """把時間講成人話：3 天前 / 5 小時前。"""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now(t.tzinfo)
    s = (now - t).total_seconds()
    if s < 0:
        return ""
    if s < 3600:
        return f"{int(s // 60)} 分前"
    if s < 86400:
        return f"{int(s // 3600)} 小時前"
    return f"{int(s // 86400)} 天前"


def run_rows(vis_dir=None):
    """掃 assets/visuals 每一批，回傳表格用的資料。

    時間優先用 manifest 的 created_at；沒有（更早的 run）就用資料夾 mtime。
    """
    d = Path(vis_dir) if vis_dir else VIS_DIR
    rows = []
    for p in (sorted(d.iterdir()) if d.is_dir() else []):
        if not p.is_dir():
            continue
        m = load_manifest(p)
        created = m.get("created_at") or datetime.fromtimestamp(
            p.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        cfg = m.get("config") or {}
        rows.append({
            "run_id": p.name,
            "kind": parse_run_id(p.name)[0],
            "created_at": created,
            "updated_at": m.get("updated_at", ""),
            "model": cfg.get("ckpt") or cfg.get("unet") or "-",
            "tool": m.get("tool", "-"),
            "images": len(list(p.glob("*.png"))),
            "logged": len(m.get("images", [])),
            "renamed_from": m.get("renamed_from", ""),
        })
    return rows


def print_runs(vis_dir=None, sort="id"):
    """批次總表：用途／建立時間／多久前／模型／張數（看得出哪批是上週跑的、該不該清）。

    sort="id" 照批次代號排（好查某一批）；sort="time" 最新的排前面（好清舊的）。
    """
    rows = run_rows(vis_dir)
    if not rows:
        print(f"（{vis_dir or VIS_DIR} 底下還沒有任何批次）")
        return
    rows.sort(key=lambda r: r["created_at"] if sort == "time" else r["run_id"],
              reverse=(sort == "time"))
    print(_pad("run-id", 8) + _pad("用途", 12) + _pad("建立時間", 19)
          + _pad("多久前", 10) + _pad("模型", 24) + _pad("張數", 5, right=True))
    print("─" * 78)
    for r in rows:
        print(_pad(r["run_id"], 8)
              + _pad(VIS_KIND_LABELS.get(r["kind"], r["kind"] or "-"), 12)
              + _pad(r["created_at"][:16].replace("T", " "), 19)
              + _pad(age_text(r["created_at"]), 10)
              + _pad(r["model"][:22], 24)
              + _pad(r["images"], 5, right=True))
    print(f"\n共 {len(rows)} 批、{sum(r['images'] for r in rows)} 張圖"
          "（時間取自 manifest.created_at；沒有就用資料夾 mtime）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=COMFY_URL)
    ap.add_argument("--prompt", default=DEFAULT_POS)
    ap.add_argument("--negative", default=DEFAULT_NEG)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--size", default="768x512", help="寬x高（SDXL 建議 1024 級，例 832x1216）")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=6.5)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="lofi_scene",
                    help="ComfyUI 自己的暫存檔名（會被 rename 複製走；成品檔名看 --slug）")
    ap.add_argument("--slug", default="",
                    help="檔名標籤（小寫英數），例 sit-window -> cat01_01_sit-window.png")
    ap.add_argument("--kind", default="sc", choices=VIS_KINDS,
                    help="用途代號（未給 --run-id 時用來編號）：ref=定裝參考 ds=訓練集 sc=場景測試")
    ap.add_argument("--run-id", default="",
                    help="批次代號＝資料夾名，建議 <kind><NN>（例 ds02）；未給則自動 <kind><NN>")
    ap.add_argument("--ckpt", default=IMAGE_CKPT, help="checkpoint 檔名（放在 ComfyUI/models/checkpoints）")
    ap.add_argument("--upscale", default="",
                    help="放大模型檔名（放 ComfyUI/models/upscale_models/），空=不放大，例：RealESRGAN_x4plus_anime_6B.pth")
    ap.add_argument("--upscale-out", default="", help="放大後輸出尺寸 寬x高（預設生成尺寸×2）")
    ap.add_argument("--clip-skip", type=int, default=0,
                    help="CLIP 切層（SDXL 動漫模型建議 2；0=用 checkpoint 預設）")
    ap.add_argument("--sampler", default="dpmpp_2m", help="取樣器（SDXL 動漫常用 euler_ancestral / dpmpp_2m）")
    ap.add_argument("--scheduler", default="karras", help="排程器（karras / normal / simple）")
    ap.add_argument("--init-image", default="",
                    help="img2img 起點圖（檔名需在 ComfyUI/input/），搭配 --denoise < 1；此時 --size 會被忽略")
    ap.add_argument("--denoise", type=float, default=1.0,
                    help="重繪強度：1.0=完全重畫；0.5~0.7=大改；0.3~0.45=小幅改動保留構圖")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="單張等待上限秒數（預設 900；ComfyUI 卡死就中止）")
    ap.add_argument("--allow-unknown-license", action="store_true",
                    help="允許使用授權未登錄的模型（商用前請先確認，見 docs/LICENSING.md）")
    ap.add_argument("--list", action="store_true",
                    help="列出 assets/visuals 各批（用途/建立時間/多久前/模型/張數）後結束，不需要 ComfyUI")
    ap.add_argument("--sort", default="id", choices=("id", "time"),
                    help="搭配 --list：id=照批次代號排（預設）；time=最新的排前面（好清舊的）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list:
        print_runs(sort=args.sort)
        return

    # 商用合規守門：檢查 checkpoint／放大模型授權（未知授權預設 fail-closed）
    if os.environ.get("LOFI_IGNORE_LICENSE") != "1":
        names = [args.ckpt] + ([args.upscale] if args.upscale else [])
        blocked, findings = check_commercial(names, allow_unknown=args.allow_unknown_license)
        for n, info in findings:
            mark = {True: "✅", False: "⛔"}.get(info["commercial"], "⚠️")
            print(f"  {mark} 授權 {n}：{info['license']}")
        if blocked and not args.dry_run:
            sys.exit("模型授權有疑慮（見上）→ 商用前請確認，或加 --allow-unknown-license 略過。"
                     "\n（已知／登錄於 src/lofi/config.py；詳見 docs/LICENSING.md）")

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit("--size 格式應為 寬x高，例如 768x512")

    up_out = None
    if args.upscale_out:
        try:
            up_out = tuple(int(x) for x in args.upscale_out.lower().split("x"))
        except ValueError:
            sys.exit("--upscale-out 格式應為 寬x高，例如 1536x1024")

    if not args.dry_run:
        comfy.require_online(args.url)
        media.require_free(VIS_DIR, MIN_FREE_GB, "內接碟 assets/visuals")

    # 一批一夾：assets/visuals/<kind><NN>/；未給 run-id 就自動編號（ref05 之後給 ref06）
    run_id = sanitize_run_id(args.run_id) or next_run_id(args.kind)
    out_dir = VIS_DIR / run_id
    prefix = args.prefix
    # config＝該批固定、所有圖共用的設定（manifest 只寫一次）；逐檔會變的只有 file/seed/prompt
    config = {
        "ckpt": args.ckpt,
        "size": f"{w}x{h}",
        "steps": args.steps,
        "cfg": args.cfg,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "clip_skip": args.clip_skip,
        "denoise": args.denoise,
        "upscale": args.upscale,
        "upscale_out": f"{up_out[0]}x{up_out[1]}" if up_out else "",
        "init_image": args.init_image,
        "negative": args.negative,
        "license": model_license(args.ckpt)["license"],
        "source": model_license(args.ckpt)["source"],
    }
    print_run_header("lofi.visual（ComfyUI + SDXL）", run_id, out_dir, config, count=args.count)
    if args.dry_run:
        print(f"（dry-run，未實際生成）prompt：{args.prompt[:80]}…")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = model_tag(config)
    done = 0
    seq = next_seq(out_dir, run_id)
    # 互斥鎖：圖片與音樂生成同時跑會爆 16GB RAM（見 AGENTS.md）
    with gpu_lock("image"):
        for i in range(1, args.count + 1):
            seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
            print(f"[{i}/{args.count}] {tag} {w}x{h} seed={seed}")
            try:
                pid = comfy.submit(args.url, build_prompt(
                    args.prompt, args.negative, w, h, seed, args.steps, args.cfg, prefix, args.ckpt,
                    upscale_model=args.upscale, up_out=up_out,
                    clip_skip=args.clip_skip, sampler=args.sampler, scheduler=args.scheduler,
                    init_image=args.init_image, denoise=args.denoise),
                    client_id="lofi-visual")
            except Exception as e:
                print(f"    ❌ 提交失敗：{e}"); break
            t0 = time.time()
            try:
                res, _ = comfy.wait_done(args.url, pid, poll=3, timeout=args.timeout)
            except comfy.ComfyError as e:
                print(f"    ❌ 等待失敗，中止：{e}"); break
            imgs = res.get("outputs", {}).get("9", {}).get("images", [])
            for im in imgs:
                src = COMFY_OUT / im.get("subfolder", "") / im["filename"]
                if not src.exists():
                    continue
                # 複製時改成標準檔名：<run-id>_<NN>[_<slug>].<ext>（ComfyUI 的 _00001_ 不帶走）
                dst_name = output_filename(run_id, seq, args.slug, Path(im["filename"]).suffix)
                seq += 1
                shutil.copy2(src, out_dir / dst_name)
                # 併寫：固定配置存一次，prompt/seed 逐檔對應；重跑同 run 不會蓋掉舊紀錄
                update_manifest(out_dir, run_id=run_id, tool="lofi.visual",
                                caller=Path(sys.argv[0]).name, config=config,
                                images=[{"file": dst_name, "seed": seed, "prompt": args.prompt}])
                done += 1
                print(f"    ✅ {time.time()-t0:.0f}s -> {dst_name}")
    print(f"\n完成：{done} 張 -> {out_dir}（manifest.json 已更新）")


if __name__ == "__main__":
    main()
