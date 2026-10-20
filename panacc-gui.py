#!/usr/bin/env python
"""panacc-gui：pan-accelerator 图形界面启动器（Material 3 Expressive）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接从仓库根目录运行，无需先安装
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pan_accel.gui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
