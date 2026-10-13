"""原子寫入與壞檔隔離。

問題背景：
  - `Path.write_text()` 非原子：寫到一半斷電／掉線／Ctrl-C 會留半截 JSON，
    而 `load_sidecar()` 讀壞檔會回 None → 呼叫端把原檔當新檔覆蓋，資料遺失。
  - `tracks.jsonl` / `visuals.jsonl` 直接 append：多程序可能交錯、寫入半行。

這裡提供：
  atomic_write_text / atomic_write_json  — 寫同目錄 tmp 再 os.replace（原子）
  append_jsonl                           — flock + 單次寫 + fsync（多程序安全）
  quarantine                             — 壞檔改名保留，避免被當新檔覆蓋
"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path


def atomic_write_text(path, text, *, fsync=True):
    """原子寫入文字檔：同目錄 tmp → os.replace。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def atomic_write_json(path, data, *, indent=2, fsync=True):
    """原子寫入 JSON（含結尾換行）。"""
    return atomic_write_text(
        path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n", fsync=fsync)


def append_jsonl(path, obj, *, fsync=True):
    """append 一行 JSON 到檔案（fcntl 排他鎖 + fsync，避免多程序交錯）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return path


def quarantine(path, *, why="", log=True):
    """把無法解析的檔案改名保留（<name>.corrupt-<時間>），回傳新路徑。

    目的是：解析失敗時不要讓呼叫端把原檔當新檔覆蓋掉（內容可能只壞一半、還救得回）。
    """
    p = Path(path)
    if not p.exists():
        return None
    dest = p.with_name(f"{p.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    os.replace(p, dest)
    if log:
        note = f"（{why}）" if why else ""
        print(f"⚠️  檔案無法解析{note}，已保留為 {dest.name}，改寫新檔", file=sys.stderr)
    return dest
