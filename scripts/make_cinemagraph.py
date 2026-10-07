#!/usr/bin/env python3
"""相容 shim：實作已移至 src/lofi/cinemagraph.py。等價於 `lofi cinemagraph`。

注意：此模組需要 numpy（用 .venv 執行）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from lofi.cinemagraph import main  # noqa: E402

if __name__ == "__main__":
    main()
