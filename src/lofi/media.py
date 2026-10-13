"""ffmpeg / ffprobe 小工具 + 磁碟空間檢查（共用；純標準庫，系統 python3 即可）。

以前 qc.py 與 meta.py 各寫一份 ffprobe_duration；磁碟檢查只散在 shell 腳本裡。
"""
import shutil
import subprocess
import sys
from pathlib import Path


def have(binary):
    return shutil.which(binary) is not None


def require(*bins):
    """確認必要外部指令存在，缺少就中止（訊息比之後 subprocess 爆掉清楚）。"""
    missing = [b for b in bins if not have(b)]
    if missing:
        sys.exit(f"找不到必要工具：{', '.join(missing)}（請先安裝 ffmpeg）")


def run(cmd):
    """執行外部指令，回傳 (returncode, stdout, stderr)。"""
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def parse_duration(text):
    """解析 ffprobe 的 duration 字串（純函式）；解不開回 None。"""
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def ffprobe_duration(path):
    """回傳秒數（float）；解不開回 None。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    ).stdout
    return parse_duration(out)


def ffprobe_size(path):
    """回傳 (寬, 高)；解不開或不是圖／影片回 None（純工具，不做判定）。"""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True,
    )
    try:
        w, h = (int(x) for x in p.stdout.strip().split("x"))
    except (ValueError, AttributeError):
        return None
    return (w, h) if w > 0 and h > 0 else None    # 壞檔 ffprobe 可能回 0x0，視為解不開


# ----------------------------- 磁碟空間 -----------------------------

def free_gb(path):
    """回傳 path 所在檔案系統的可用 GB（path 不存在就往最近的祖先找）。"""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def require_free(path, min_gb, label=""):
    """可用空間不足（< min_gb）就中止，避免生成到一半寫爆碟（見 AGENTS.md 資源限制）。"""
    free = free_gb(path)
    if free < min_gb:
        sys.exit(
            f"磁碟空間不足（{label or path}）：可用 {free:.1f}GB < 需要 {min_gb}GB。\n"
            f"  清理中間檔：./scripts/cleanup_outputs.sh --apply\n"
            f"  確認磁碟：./scripts/disk_report.sh\n"
            f"  （可用 LOFI_MIN_FREE_GB 調整門檻）")
    return free
