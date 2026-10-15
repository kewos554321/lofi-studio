"""專案路徑解析（repo 根目錄與常用目錄）。

**所有目錄的唯一來源**：其他模組請 `from lofi.paths import ...`，
不要再各自 `ROOT / "..."` 拼路徑（曾散落多處、易走鐘）。
"""
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

# 素材 / 紀錄
ASSETS_DIR = ROOT / "assets"
TRACKS_DIR = ASSETS_DIR / "tracks"
VISUALS_DIR = ASSETS_DIR / "visuals"
CATALOG_DIR = ROOT / "catalog"
PUBLISH_DIR = ROOT / "publish"
LOGS_DIR = ROOT / "logs"

# 產線 run 帳本（append-only 事件流；run × stage × 進度，見 src/lofi/runs.py）。
# 與 tracks/releases.jsonl 同一個 catalog/：git 收紀錄、狀態變更用新事件。
RUNS_JSONL = CATALOG_DIR / "runs.jsonl"
# 通知紀錄（本機，不進版控）：哪封信寄成功／失敗，也是 dedupe 冷卻的依據。
NOTIFY_LOG = LOGS_DIR / "notifications.jsonl"
# 「現在」快照（衍生物、可重建）：由 run 帳本攤平而來，給 UI／看板便宜地讀。
RUN_STATE = LOGS_DIR / ".run_state.json"

# 提示詞骨架
PROMPTS_DIR = ROOT / "prompts"
STYLE_DIR = PROMPTS_DIR / "styles"
YT_DIR = PROMPTS_DIR / "youtube"
CHARACTER_DIR = PROMPTS_DIR / "character"   # 主角（黑貓 Moni）定裝與品牌資料
GEN_DIR = PROMPTS_DIR / "generated"
PROMPT_LIB = PROMPTS_DIR / "prompt_library.csv"

# 輸出（output 為外接碟 symlink）與機密
OUTPUT_DIR = ROOT / "output"
VIDEOS_DIR = OUTPUT_DIR / "videos"
EPISODES_DIR = OUTPUT_DIR / "episodes"
RELEASES_DIR = OUTPUT_DIR / "releases"
MASTERS_DIR = OUTPUT_DIR / "masters"      # 無損母帶倉庫（外接；見 docs/DISTRIBUTION.md §6-3）
LORA_DIR = OUTPUT_DIR / "lora"            # LoRA 訓練產物（RunPod + AI Toolkit；外接）
SECRETS_DIR = ROOT / ".secrets"
