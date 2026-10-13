"""跨程序互斥鎖：避免同時跑多個吃 GPU／RAM 的生成。

16GB 統一記憶體下，ACE-Step（約 9GB）與 SDXL（約 6.5GB）同時跑必爆
（見 AGENTS.md）。與其靠人記得，不如讓產線自己擋。

用法：
    from lofi.lock import gpu_lock
    with gpu_lock("music"):
        ...生成...

- 鎖檔在 logs/（內接碟；外接碟掉線也不受影響）。
- 已有其他生成在跑時，預設**立刻中止**並印出持有者；等得到就設 LOFI_LOCK_WAIT=秒。
- 緊急略過：LOFI_NO_LOCK=1。
"""
import fcntl
import os
import socket
import sys
import time
from contextlib import contextmanager

from lofi.paths import LOGS_DIR

LOCK_PATH = LOGS_DIR / ".gpu.lock"


def _read_holder(fh):
    try:
        fh.seek(0)
        return fh.read().strip()
    except OSError:
        return ""


def _describe(fh):
    holder = _read_holder(fh)
    return holder or "（未知；可能是殘留，確認沒有生成在跑就可續用）"


@contextmanager
def gpu_lock(label="generate", wait=False):
    """取得生成互斥鎖。wait=True 或設 LOFI_LOCK_WAIT 會排隊等待，否則衝突即中止。"""
    if os.environ.get("LOFI_NO_LOCK") == "1":
        yield
        return

    wait_timeout = float(os.environ.get("LOFI_LOCK_WAIT", "0") or 0)
    if wait_timeout:
        wait = True

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "a+", encoding="utf-8")
    got = False
    deadline = time.time() + wait_timeout if wait_timeout else None
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if not wait or (deadline and time.time() >= deadline):
                    break
                print(f"⏳ 等待生成鎖（{label}）；持有者：{_describe(fh)}", file=sys.stderr)
                time.sleep(5)
        if not got:
            sys.exit(
                f"❌ 已有其他生成在跑（{label}），為避免 RAM 爆掉而中止。\n"
                f"   持有者：{_describe(fh)}\n"
                f"   等它結束再跑；要排隊可設 LOFI_LOCK_WAIT=秒；確定硬跑可設 LOFI_NO_LOCK=1。")

        fh.seek(0)
        fh.truncate()
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} "
                 f"host={socket.gethostname()} label={label}")
        fh.flush()
        yield
    finally:
        if got:
            try:
                fh.seek(0)
                fh.truncate()
                fh.flush()
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()
