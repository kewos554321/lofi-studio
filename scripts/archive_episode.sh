#!/usr/bin/env bash
#
# archive_episode.sh — 把一集成片封存到外接碟，並清掉中間檔（第 5 段「整理環境」）。
#
# 用法:
#   ./scripts/archive_episode.sh --episode rl01
#   ./scripts/archive_episode.sh --episode rl01 --keep-visual      # 保留 visual_loop.mp4
#   ./scripts/archive_episode.sh --episode rl01 --dest /path/archive
#   ./scripts/archive_episode.sh --episode rl01 --dry-run
#
# 來源（自動判斷）:
#   output/episodes/<episode>/       新佈局：搬整個資料夾（video.mp4 + mix.wav + visual_loop.mp4）
#   output/videos/<episode>.mp4      舊佈局：搬單檔
#
# 目標（預設）: 依 output symlink 決定 → <output 同層>/archive/
#   例：output -> /Volumes/WJ_SATA/lofi-studio/output，則 archive 在 /Volumes/WJ_SATA/lofi-studio/archive
#   可用 --dest 或環境變數 LOFI_ARCHIVE 覆寫。
#
# 動作: 搬到 <archive>/<episode>/ → 刪中間檔 visual_loop.mp4（--keep-visual 可保留）
#       → 複製 publish/<episode>.json/.md 進封存夾（自帶說明）
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EPISODE=""
DEST=""
KEEP_VISUAL=0
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --episode)     EPISODE="$2"; shift 2;;
    --dest)        DEST="$2"; shift 2;;
    --keep-visual) KEEP_VISUAL=1; shift;;
    --dry-run)     DRY=1; shift;;
    -h|--help)     sed -n '2,22p' "$0"; exit 0;;
    *) echo "未知參數: $1（用 --help）" >&2; exit 1;;
  esac
done

[ -n "$EPISODE" ] || { echo "錯誤: 需要 --episode NAME" >&2; exit 1; }

# ---------- 來源 ----------
SRC_DIR="output/episodes/${EPISODE}"
SRC_FILE="output/videos/${EPISODE}.mp4"
if [ -d "$SRC_DIR" ]; then
  SRC="$SRC_DIR"; KIND=dir
elif [ -f "$SRC_FILE" ]; then
  SRC="$SRC_FILE"; KIND=file
else
  echo "錯誤: 找不到 ${SRC_DIR}/ 或 ${SRC_FILE}" >&2
  exit 1
fi

# ---------- 目標 ----------
if [ -z "$DEST" ]; then
  DEST="${LOFI_ARCHIVE:-}"
fi
if [ -z "$DEST" ]; then
  if [ -L output ]; then
    REAL="$(cd output && pwd -P)"       # 解析 symlink 目標
    DEST="$(dirname "$REAL")/archive"
  else
    DEST="$ROOT/archive"
  fi
fi
DEST_EP="${DEST}/${EPISODE}"

if [ -e "$DEST_EP" ]; then
  echo "錯誤: 封存目標已存在 ${DEST_EP}（避免覆蓋）" >&2
  exit 1
fi

SIZE="$(du -sh "$SRC" | cut -f1)"
echo "==> 封存 episode: ${EPISODE}"
echo "    來源: $SRC  ($SIZE, $KIND)"
echo "    目標: $DEST_EP/"
if [ "$KEEP_VISUAL" = 0 ]; then
  echo "    中間檔: 將刪除 visual_loop.mp4（--keep-visual 可保留）"
fi

if [ "$DRY" = 1 ]; then
  echo "（dry-run，未動作）"
  exit 0
fi

mkdir -p "$DEST"
if [ "$KIND" = dir ]; then
  mv "$SRC" "$DEST_EP"
  [ -f "publish/${EPISODE}.json" ] && cp "publish/${EPISODE}.json" "$DEST_EP/" || true
  [ -f "publish/${EPISODE}.md" ] && cp "publish/${EPISODE}.md" "$DEST_EP/" || true
  [ "$KEEP_VISUAL" = 0 ] && rm -f "${DEST_EP}/visual_loop.mp4" || true
  # 保險：清掉任何殘留中間檔
  rm -f "${DEST_EP}"/_loop_1080.mp4 "${DEST_EP}"/_video_copy.mp4 \
        "${DEST_EP}"/_mix_raw.wav "${DEST_EP}"/_loop_list.txt
  rmdir "output/episodes" 2>/dev/null || true
else
  mkdir -p "$DEST_EP"
  mv "$SRC" "$DEST_EP/"
  [ -f "publish/${EPISODE}.json" ] && cp "publish/${EPISODE}.json" "$DEST_EP/" || true
  [ -f "publish/${EPISODE}.md" ] && cp "publish/${EPISODE}.md" "$DEST_EP/" || true
fi

echo "✅ 已封存到 ${DEST_EP}/"
if [ "$KIND" = dir ]; then
  ls -lh "$DEST_EP" | sed 's/^/    /'
fi
