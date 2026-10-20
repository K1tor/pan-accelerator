"""图形界面入口：python -m pan_accel.gui 或 panacc-gui 命令。"""
from __future__ import annotations

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
