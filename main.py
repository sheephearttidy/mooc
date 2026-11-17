"""项目根目录入口：`python main.py`。

等价于 `python -m moocpy`，保留这个文件是为了照顾原来双击 / 命令行直接跑的习惯。
"""

from __future__ import annotations

import os
import sys

# 允许从任意工作目录用绝对路径启动，例如 `python D:\path\to\main.py`
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from moocpy.bootstrap import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
