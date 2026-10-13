"""ffmpeg / ffprobe 小工具（共用；以前 qc.py 與 meta.py 各寫一份 ffprobe_duration）。

純標準庫，系統 python3 即可。
"""
import shutil
import subprocess
import sys


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


def ffprobe_duration(path):
    """回傳秒數（float）；解不開回 None。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None
