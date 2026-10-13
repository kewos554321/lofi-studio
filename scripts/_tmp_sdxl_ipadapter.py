#!/usr/bin/env python3
"""臨時測試：SDXL + IPAdapter（角色一致性）——用參考圖鎖住角色/風格。

節點：CheckpointLoaderSimple -> IPAdapterUnifiedLoader -> IPAdapterAdvanced -> KSampler

前置：
  ~/ComfyUI/models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors
  ~/ComfyUI/models/clip_vision/CLIP-ViT-H-14-*.safetensors
  參考圖放 ~/ComfyUI/input/
"""
import argparse
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lofi.visual import (VIS_DIR, COMFY_OUT, api,        # noqa: E402
                         update_manifest, print_run_header, model_tag,
                         output_filename, next_seq, next_run_id, VIS_KINDS)


def build(pos, neg, w, h, seed, steps, cfg, prefix, ref_image, ckpt,
          weight=0.8, weight_type="linear", start=0.0, end=1.0,
          embeds_scaling="K+V", preset="PLUS (high strength)",
          sampler="dpmpp_2m", scheduler="karras"):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "IPAdapterUnifiedLoader",
              "inputs": {"model": ["1", 0], "preset": preset}},
        "3": {"class_type": "LoadImage", "inputs": {"image": ref_image}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": pos}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": neg}},
        "6": {"class_type": "IPAdapterAdvanced",
              "inputs": {"model": ["2", 0], "ipadapter": ["2", 1], "image": ["3", 0],
                         "weight": weight, "weight_type": weight_type,
                         "combine_embeds": "concat", "start_at": start, "end_at": end,
                         "embeds_scaling": embeds_scaling}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["6", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--ckpt", default="DreamShaperXL-v1.0-Alpha2.safetensors")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative", default="worst quality, low quality, normal quality, lowres, blurry, jpeg artifacts, watermark, signature, text, error, human, humans, person, people, 1girl, girl, humanoid, deformed, bad anatomy, extra limbs, extra tail, cropped, sketch")
    ap.add_argument("--size", default="832x1216")
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--cfg", type=float, default=6.5)
    ap.add_argument("--weight", type=float, default=0.8)
    ap.add_argument("--weight-type", default="linear")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1.0)
    ap.add_argument("--embeds-scaling", default="K+V")
    ap.add_argument("--preset", default="PLUS (high strength)")
    ap.add_argument("--sampler", default="dpmpp_2m", help="取樣器")
    ap.add_argument("--scheduler", default="karras", help="排程器")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="ipa_test",
                    help="ComfyUI 自己的暫存檔名（會被 rename 複製走；成品檔名看 --slug）")
    ap.add_argument("--slug", default="",
                    help="檔名標籤（小寫英數），例 sit-window -> ds02_01_sit-window.png")
    ap.add_argument("--kind", default="sc", choices=VIS_KINDS,
                    help="用途代號（未給 --run-id 時用來編號）：ref=定裝參考 ds=訓練集 sc=場景測試")
    ap.add_argument("--run-id", default="",
                    help="批次代號＝資料夾名，建議 <kind><NN>（例 ds02）；未給則自動 <kind><NN>")
    ap.add_argument("--write-caption", action="store_true",
                    help="同時寫同名 .txt 字幕檔（LoRA 訓練集用，內容＝prompt）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit("--size 格式應為 寬x高")

    if not args.dry_run:
        try:
            api(args.url, "/system_stats", timeout=5)
        except Exception as e:
            sys.exit(f"無法連線 ComfyUI ({args.url})：{e}")

    VIS_DIR.mkdir(parents=True, exist_ok=True)
    # 未給 run-id 就自動編號（ref05 之後給 ref06）
    run_id = args.run_id or next_run_id(args.kind)
    out_dir = VIS_DIR / run_id
    prefix = f"{args.prefix}_{run_id}"
    seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
    # config＝該批固定、所有圖共用的設定；prompt/seed 逐檔寫進 manifest 的 images
    config = {
        "ckpt": args.ckpt,
        "ref": args.ref,
        "size": f"{w}x{h}",
        "steps": args.steps,
        "cfg": args.cfg,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "preset": args.preset,
        "weight": args.weight,
        "weight_type": args.weight_type,
        "start": args.start,
        "end": args.end,
        "embeds_scaling": args.embeds_scaling,
        "negative": args.negative,
    }
    print_run_header("SDXL + IPAdapter（鎖角色）", run_id, out_dir, config)
    if args.dry_run:
        print(f"（dry-run，未實際生成）prompt：{args.prompt[:80]}…")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    seq = next_seq(out_dir, run_id)
    print(f"[1/1] {model_tag(config)} {w}x{h} seed={seed}")
    r = api(args.url, "/prompt", {"prompt": build(
        args.prompt, args.negative, w, h, seed, args.steps, args.cfg, prefix, args.ref, args.ckpt,
        weight=args.weight, weight_type=args.weight_type, start=args.start, end=args.end,
        embeds_scaling=args.embeds_scaling, preset=args.preset),
        "client_id": "lofi-ipa"})
    pid = r["prompt_id"]
    t0 = time.time()
    while True:
        try:
            hist = api(args.url, f"/history/{pid}", timeout=15)
        except Exception:
            hist = {}
        if hist and pid in hist:
            break
        time.sleep(2)
    res = hist[pid]
    if res.get("status", {}).get("status_str") == "error":
        print("   ❌ ComfyUI 回報錯誤：")
        for m in res.get("status", {}).get("messages", []):
            if m[0] == "execution_error":
                print("     ", m[1].get("node_type"), ":", m[1].get("exception_message"))
        return
    for im in res.get("outputs", {}).get("10", {}).get("images", []):
        src = COMFY_OUT / im.get("subfolder", "") / im["filename"]
        if not src.exists():
            continue
        # 複製時改成標準檔名：<run-id>_<NN>[_<slug>].<ext>（ComfyUI 的 _00001_ 不帶走）
        dst_name = output_filename(run_id, seq, args.slug, Path(im["filename"]).suffix)
        seq += 1
        shutil.copy2(src, out_dir / dst_name)
        update_manifest(out_dir, run_id=run_id, tool="sdxl-ipadapter",
                        caller=Path(sys.argv[0]).name, config=config,
                        images=[{"file": dst_name, "seed": seed, "prompt": args.prompt}])
        if args.write_caption:                 # LoRA 訓練集：字幕檔與圖同名（.txt）
            (out_dir / f"{Path(dst_name).stem}.txt").write_text(
                args.prompt.strip() + "\n", encoding="utf-8")
        print(f"    ✅ {time.time()-t0:.0f}s -> {dst_name}")


if __name__ == "__main__":
    main()
