#!/usr/bin/env python3
"""
batch_generate.py — 用 ComfyUI API 批次生成 ACE-Step 1.5 lofi 曲子。

從 prompts/prompt_library.csv 讀取氛圍設定（caption/bpm/key），
逐首送進 ComfyUI（預設 http://127.0.0.1:8188），生成後複製到 assets/tracks/。

用法:
  python3 scripts/batch_generate.py                 # 生成 CSV 全部（10 首）
  python3 scripts/batch_generate.py --limit 3       # 只生 3 首
  python3 scripts/batch_generate.py --duration 120  # 每首秒數（預設 120）
  python3 scripts/batch_generate.py --seed 12345    # 固定 seed（可重現）
  python3 scripts/batch_generate.py --dry-run       # 只印出將送出的設定

前置：ComfyUI 需以 scripts/launch_comfyui.sh 啟動，且 ACE-Step 1.5 模型已就位。
"""
import argparse, csv, json, random, shutil, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "prompts" / "prompt_library.csv"
TRACKS_DIR = ROOT / "assets" / "tracks"
COMFY_OUT = Path.home() / "ComfyUI" / "output" / "audio"

UNET = "acestep_v1.5_turbo.safetensors"
CLIP1 = "qwen_0.6b_ace15.safetensors"
CLIP2 = "qwen_1.7b_ace15.safetensors"
VAE = "ace_1.5_vae.safetensors"


def build_prompt(tags, bpm, key, duration, seed, prefix):
    return {
        "104": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "78":  {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["104", 0], "shift": 3.0}},
        "105": {"class_type": "DualCLIPLoader", "inputs": {
                    "clip_name1": CLIP1, "clip_name2": CLIP2, "type": "ace", "device": "default"}},
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "94":  {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
                    "clip": ["105", 0], "tags": tags, "lyrics": "", "seed": seed,
                    "bpm": int(bpm), "duration": float(duration), "timesignature": "4",
                    "language": "en", "keyscale": key, "generate_audio_codes": True,
                    "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "47":  {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
        "98":  {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": float(duration), "batch_size": 1}},
        "3":   {"class_type": "KSampler", "inputs": {
                    "model": ["78", 0], "seed": seed, "steps": 8, "cfg": 1,
                    "sampler_name": "euler", "scheduler": "simple",
                    "positive": ["94", 0], "negative": ["47", 0],
                    "latent_image": ["98", 0], "denoise": 1.0}},
        "18":  {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["3", 0], "vae": ["106", 0]}},
        "107": {"class_type": "SaveAudioMP3", "inputs": {
                    "audio": ["18", 0], "filename_prefix": prefix, "quality": "V0"}},
    }


def api(url, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wait_done(url, pid, poll=5):
    t0 = time.time()
    while True:
        try:
            h = api(url, f"/history/{pid}", timeout=15)
        except Exception:
            h = {}
        if h and pid in h:
            return h[pid], time.time() - t0
        time.sleep(poll)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--limit", type=int, default=0, help="只生成前 N 首（0=全部）")
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--seed", type=int, default=None, help="固定 seed（可重現）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not CSV_PATH.exists():
        sys.exit(f"找不到 {CSV_PATH}")

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    if args.limit:
        rows = rows[:args.limit]

    if not args.dry_run:
        try:
            stats = api(args.url, "/system_stats", timeout=5)
            print(f"ComfyUI {stats['system']['comfyui_version']} @ {args.url}")
        except Exception as e:
            sys.exit(f"無法連線 ComfyUI ({args.url})：{e}\n請先執行 scripts/launch_comfyui.sh")

    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, row in enumerate(rows, 1):
        rid = row.get("id", f"{i:02d}")
        tags = row["prompt"].strip()
        bpm = row.get("bpm", "70")
        key = row.get("key", "D minor")
        seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
        prefix = f"audio/lofi_{rid}"

        print(f"\n[{i}/{len(rows)}] id={rid} | {row.get('mood','')} | {bpm} BPM | {key} | seed={seed}")
        print(f"    {tags[:90]}…")

        if args.dry_run:
            continue

        try:
            r = api(args.url, "/prompt", {"prompt": build_prompt(tags, bpm, key, args.duration, seed, prefix),
                                          "client_id": "lofi-batch"})
        except urllib.error.HTTPError as e:
            print("    ❌ 提交失敗:", e.read().decode()[:500]); continue
        pid = r["prompt_id"]

        res, dt = wait_done(args.url, pid)
        status = res.get("status", {}).get("status_str")
        if status != "success":
            print(f"    ❌ 生成失敗: {status} {res.get('status',{}).get('messages',[])[-2:]}")
            continue

        audio = res.get("outputs", {}).get("107", {}).get("audio", [])
        for a in audio:
            src = Path.home() / "ComfyUI" / "output" / a.get("subfolder", "") / a["filename"]
            dst = TRACKS_DIR / a["filename"]
            if src.exists():
                shutil.copy2(src, dst)
                print(f"    ✅ {dt:.0f}s -> {dst.name}")
                ok += 1
            else:
                print(f"    ⚠️  找不到輸出檔 {src}")

    if args.dry_run:
        print("\n（dry-run，未實際生成）")
    else:
        print(f"\n完成 {ok}/{len(rows)} 首，存放於 {TRACKS_DIR}")


if __name__ == "__main__":
    main()
