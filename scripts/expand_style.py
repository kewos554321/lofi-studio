#!/usr/bin/env python3
"""相容 shim：實作已移至 src/lofi/expand.py。等價於 `lofi expand`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from lofi.expand import main  # noqa: E402

if __name__ == "__main__":
    main()
