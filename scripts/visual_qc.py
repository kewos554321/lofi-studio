#!/usr/bin/env python3
"""相容 shim：實作在 src/lofi/visual_qc.py。等價於 `lofi visual-qc`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from lofi.visual_qc import main  # noqa: E402

if __name__ == "__main__":
    main()
