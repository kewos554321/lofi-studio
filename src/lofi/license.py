#!/usr/bin/env python3
"""
license.py — 商用授權稽核：掃 sidecar / manifest 列出實際用到的模型與授權狀態。

用法:
  lofi license check                    # 掃 assets（音樂 sidecar + 圖片 manifest）
  lofi license check --episode NAME      # 另讀 publish/NAME.json 的 credits
  lofi license models                    # 列出授權登錄（= lofi config --licenses）

問題（授權非明確可商用）時 exit code 為 1，可掛在發佈前檢查。
"""
import argparse
import json
import sys

from lofi import catalog as cat
from lofi import config
from lofi.paths import PUBLISH_DIR, VISUALS_DIR

MARK = {True: "✅", False: "⛔"}


def scan_music():
    """回傳 {model: {count, license, source}}（來源：assets/tracks 的 sidecar.gen）。"""
    used = {}
    for p in cat.iter_audio_files():
        gen = (cat.load_sidecar(p) or {}).get("gen") or {}
        m = gen.get("model")
        if m:
            e = used.setdefault(m, {"count": 0,
                                    "license": gen.get("model_license"),
                                    "source": gen.get("model_source")})
            e["count"] += 1
    return used


def scan_visuals():
    """回傳 {model: {count, license, source}}（來源：assets/visuals/*/manifest.json）。"""
    used = {}
    if not VISUALS_DIR.exists():
        return used
    for mpath in VISUALS_DIR.glob("*/manifest.json"):
        try:
            d = json.loads(mpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        cfg = d.get("config") or {}
        for m in (cfg.get("ckpt"), cfg.get("unet")):
            if m:
                e = used.setdefault(m, {"count": 0,
                                        "license": cfg.get("license"),
                                        "source": cfg.get("source")})
                e["count"] += 1
    return used


def _report(title, used):
    print(f"◆ {title}")
    problems = 0
    if not used:
        print("  （無紀錄）")
        return 0
    for name, rec in sorted(used.items()):
        info = config.model_license(name)
        lic = rec.get("license") or info["license"]
        src = rec.get("source") or info["source"]
        mark = MARK.get(info["commercial"], "⚠️")
        if info["commercial"] is not True:
            problems += 1
        print(f"  {mark} {rec['count']:>5}  {name}  [{lic}]  {src}")
    return problems


def print_episode(name):
    p = PUBLISH_DIR / f"{name}.json"
    print(f"\n◆ publish/{name}.json")
    if not p.exists():
        print("  （找不到）")
        return 0
    rec = json.loads(p.read_text(encoding="utf-8"))
    credits = rec.get("credits") or []
    if not credits:
        print("  （無 credits 紀錄；用 lofi meta 重新產生才會有）")
        return 0
    problems = 0
    for c in credits:
        commercial = c.get("commercial")
        mark = MARK.get(commercial, "⚠️")
        if commercial is not True:
            problems += 1
        print(f"  {mark} {c.get('model')}  [{c.get('license')}]  {c.get('source', '')}")
    return problems


def main():
    ap = argparse.ArgumentParser(description="商用授權稽核（掃模型與授權狀態）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="掃 assets 用到的模型與授權狀態")
    c.add_argument("--episode", default="", help="另讀 publish/<episode>.json 的 credits")
    sub.add_parser("models", help="列出授權登錄（= lofi config --licenses）")
    args = ap.parse_args()

    if args.cmd == "models":
        config.print_licenses()
        return

    problems = _report("音樂（sidecar gen.model）", scan_music())
    print()
    problems += _report("圖片（manifest config.ckpt）", scan_visuals())
    if args.episode:
        problems += print_episode(args.episode)

    print()
    if problems:
        print(f"⚠️  有 {problems} 顆模型授權非明確可商用 → 商用前務必確認（見 docs/LICENSING.md）")
        sys.exit(1)
    print("✅ 掃到的模型授權皆為可商用（仍請以官方頁面為準）")


if __name__ == "__main__":
    main()
