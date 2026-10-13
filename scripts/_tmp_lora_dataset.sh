#!/usr/bin/env bash
# LoRA 訓練集生成（文字生圖 + IPAdapter 鎖角色）
# 命名規則：資料夾 <kind><NN>、檔名 <run-id>_<NN>_<slug>.<ext>（見 docs/VISUAL_NAMING.md）
# 用法: bash scripts/_tmp_lora_dataset.sh [run-id]     預設 ds02
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=src

RUN_ID="${1:-ds02}"
REF="mascot_blackcat_ref.png"
CK="DreamShaperXL-v1.0-Alpha2.safetensors"
OUT="assets/visuals/${RUN_ID}"
mkdir -p "$OUT"

IDENT="a sleek black cat with bright golden amber eyes, (clearly white paws:1.3), (white socks on all four feet:1.2), pure black fur on the rest of the body"
STYLE="hand-painted watercolor anime illustration, delicate brush strokes, warm nostalgic color palette"
NEG="all black paws, black feet, worst quality, low quality, blurry, jpeg artifacts, watermark, signature, text, human, humans, person, people, 1girl, girl, humanoid, deformed, extra limbs, extra fingers, cropped, sketch"

# 姿勢：描述「貓在做什麼」（可含動作動詞）＋檔名用的 slug
POSES=(
  "sitting upright, front view, looking directly at the viewer"
  "sitting, three-quarter view, head turned slightly to the side"
  "walking sideways, full body side profile"
  "lying down on its belly, front view, relaxed"
  "curled up asleep with eyes closed"
  "stretching with front legs extended forward"
  "sitting and looking up at something above"
  "close-up portrait of the face, very detailed golden eyes"
  "standing, full body, seen from behind"
  "one front paw raised up, reaching upward"
)
POSE_SLUGS=( sit-front sit-3q walk-side lie sleep stretch look-up face stand-back paw-up )

# 場景：只寫「地點＋光線」，不含動詞 → 跟任何姿勢都能配
SCENES=(
  "on a wooden windowsill, warm afternoon light, potted plants"
  "on a wooden desk with open books and a steaming coffee mug"
  "on a cozy rug beside a fireplace, warm firelight"
  "on an autumn forest path with fallen leaves"
  "by a rainy window at night with a warm lamp glow"
  "inside a woven wicker basket"
  "in a grassy meadow in soft daylight"
  "on a wooden bookshelf among old books"
  "on a kitchen counter with a teapot and copper pots"
  "on a mossy stone wall in a garden"
)
SCENE_SLUGS=( windowsill desk fireplace forest rain-window basket meadow bookshelf kitchen garden )

n=${#POSES[@]}
i=0
for k in $(seq 0 $((n-1))); do
  for off in 0 3 7; do              # 錯開的偏移：30 個姿勢×場景組合全不重複
    i=$((i+1))
    s=$(( (k + off) % n ))
    pose="${POSES[$k]}"
    scene="${SCENES[$s]}"
    slug="${POSE_SLUGS[$k]}-${SCENE_SLUGS[$s]}"
    p="${IDENT}, ${pose}, ${scene}, ${STYLE}"
    printf -v nn "%02d" "$i"
    echo "=== [${i}/30] ${RUN_ID}_${nn}_${slug} ==="
    python3 scripts/_tmp_sdxl_ipadapter.py \
      --ref "$REF" --ckpt "$CK" \
      --size 832x1216 --steps 28 --cfg 6.5 --weight 0.60 --weight-type linear \
      --run-id "$RUN_ID" --slug "$slug" --write-caption \
      --prompt "$p" --negative "$NEG" 2>&1 | tail -1
  done
done
echo "DATASET_DONE 共 $i 張 -> $OUT"
