"""專案路徑解析（repo 根目錄與常用目錄）。"""
import os
from pathlib import Path


def _find_root():
    env = os.environ.get("LOFI_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # 由 cwd 往上找，其次由本檔位置往上找
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        for p in (base, *base.parents):
            if (p / "prompts" / "styles").is_dir() and (p / "assets").is_dir():
                return p
    return Path.cwd()


ROOT = _find_root()
TRACKS_DIR = ROOT / "assets" / "tracks"
VISUALS_DIR = ROOT / "assets" / "visuals"
CATALOG_DIR = ROOT / "catalog"
PUBLISH_DIR = ROOT / "publish"
LOGS_DIR = ROOT / "logs"
PROMPTS_DIR = ROOT / "prompts"
