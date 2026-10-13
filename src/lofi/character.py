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
import sys
from pathlib import Path

from lofi import io
from lofi.paths import CHARACTER_DIR, VISUALS_DIR

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
    """負向詞必須含「要對抗的錯誤特徵」，否則推不出正確特徵。"""
    t = (negative or "").lower()
    return [f"負向詞缺少「{x}」（推不出正確特徵）"
            for x in (spec.get("negative_should_contain") or [])
            if x.lower() not in t]


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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
