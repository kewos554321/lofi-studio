#!/usr/bin/env python3
"""
expand_style.py — 把「一個風格骨架」展開成多首同風格、變化受控的曲目清單。

讀 prompts/styles/<風格>.json（固定骨架 + 可變維度），組合出 key/BPM/樂器/情境
不重複的 N 列，寫成 batch_generate.py 可直接讀的 CSV。

用法:
  python3 scripts/expand_style.py --style rainy_lofi --count 20
  python3 scripts/expand_style.py --style rainy_lofi --count 40 --seed 42
  python3 scripts/expand_style.py --style rainy_lofi --out prompts/generated/my.csv
  python3 scripts/expand_style.py --style rainy_lofi --count 5 --dry-run

接著:
  python3 scripts/batch_generate.py --csv prompts/generated/rainy_lofi.csv --limit 20
"""
import argparse, csv, itertools, json, random, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE_DIR = ROOT / "prompts" / "styles"
GEN_DIR = ROOT / "prompts" / "generated"

FIELDS = ["id", "style", "mood", "instruments", "key", "bpm", "prompt", "negative"]


def load_style(name):
    p = Path(name)
    if p.suffix != ".json":
        p = STYLE_DIR / f"{name}.json"
    if not p.exists():
        avail = ", ".join(sorted(f.stem for f in STYLE_DIR.glob("*.json"))) or "（無）"
        sys.exit(f"找不到風格檔 {p}\n可用風格: {avail}")
    with p.open(encoding="utf-8") as f:
        st = json.load(f)
    for k in ("backbone", "keys", "bpm_range", "instruments", "moods"):
        if k not in st:
            sys.exit(f"風格檔缺少必要欄位: {k}")
    st.setdefault("name", p.stem)
    st.setdefault("negative", "")
    st.setdefault("closing", "")
    return st


def build_prompt(st, mood, instr, bpm, key):
    parts = [st["backbone"], mood, instr, f"{bpm} BPM", key]
    if st.get("closing"):
        parts.append(st["closing"])
    return ", ".join(p.strip() for p in parts if p and p.strip())


def main():
    ap = argparse.ArgumentParser(description="展開同風格曲目清單")
    ap.add_argument("--style", required=True, help="風格名稱（prompts/styles/<name>.json）或 .json 路徑")
    ap.add_argument("--count", type=int, default=20, help="要生成幾首（預設 20）")
    ap.add_argument("--seed", type=int, default=None, help="固定 seed（可重現）")
    ap.add_argument("--out", default=None, help="輸出 CSV（預設 prompts/generated/<style>.csv）")
    ap.add_argument("--dry-run", action="store_true", help="只印出前幾首，不寫檔")
    args = ap.parse_args()

    st = load_style(args.style)
    lo, hi = st["bpm_range"]
    bpms = list(range(int(lo), int(hi) + 1))

    combos = list(itertools.product(st["keys"], bpms, st["instruments"], st["moods"]))
    if not combos:
        sys.exit("風格檔的維度組合為空，請檢查 keys/bpm_range/instruments/moods")

    rng = random.Random(args.seed)
    rng.shuffle(combos)

    rows = []
    for i in range(args.count):
        # 組合不夠時，重新洗牌再繼續，仍不會出現「同 key+BPM」的相鄰重複
        if i and i % len(combos) == 0:
            rng.shuffle(combos)
        key, bpm, instr, mood = combos[i % len(combos)]
        rows.append({
            "id": f"{i + 1:02d}",
            "style": st["name"],
            "mood": mood,
            "instruments": instr,
            "key": key,
            "bpm": bpm,
            "prompt": build_prompt(st, mood, instr, bpm, key),
            "negative": st["negative"],
        })

    if args.dry_run:
        print(f"風格: {st['name']}  ({st.get('description','')})")
        print(f"可變維度組合數: {len(combos)}，將產生 {args.count} 首\n")
        for r in rows[:5]:
            print(f"  [{r['id']}] {r['key']:>8} | {r['bpm']} BPM | {r['mood']}")
            print(f"        {r['prompt'][:100]}…")
        if args.count > 5:
            print(f"  …另 {args.count - 5} 首")
        return

    out = Path(args.out) if args.out else GEN_DIR / f"{st['name']}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    keys_used = sorted({r["key"] for r in rows})
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"✅ 已產生 {len(rows)} 首 → {out}")
    print(f"   風格: {st['name']} | 用到 {len(keys_used)} 種調性 | 建議時長 {st.get('duration', 120)}s")
    print(f"   下一步: python3 scripts/batch_generate.py --csv {shown} --limit {len(rows)}")


if __name__ == "__main__":
    main()
