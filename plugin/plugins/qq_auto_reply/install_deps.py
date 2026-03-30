#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安装插件依赖到本地 lib/ 目录"""

import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).parent / "lib"

DEPENDENCIES = [
    "websockets>=12.0",
    "httpx>=0.27.0",
    "tomli>=2.0.0",
    "tomli-w>=1.0.0",
]

def main():
    LIB_DIR.mkdir(exist_ok=True)
    print(f"正在安装依赖到 {LIB_DIR} ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(LIB_DIR), *DEPENDENCIES],
    )
    if result.returncode == 0:
        print("依赖安装完成，可以运行插件了。")
    else:
        print("依赖安装失败，请检查网络连接或手动安装。")
        sys.exit(1)

if __name__ == "__main__":
    main()
