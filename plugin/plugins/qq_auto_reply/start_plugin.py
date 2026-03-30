#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# 加载本地依赖
_lib_dir = Path(__file__).parent / "lib"
if _lib_dir.exists() and str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

def _check_deps():
    missing = []
    for pkg in ["websockets", "httpx", "tomli", "tomli_w"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERROR] 缺少依赖: {', '.join(missing)}")
        print("请先运行: python install_deps.py")
        sys.exit(1)

_check_deps()

import asyncio
import httpx

async def start_qq_auto_reply():
    """启动 QQ 自动回复插件"""

    print("="*60)
    print("启动 QQ 自动回复插件")
    print("="*60)

    # NEKO 插件服务器 API
    plugin_api = "http://localhost:48916/runs"

    try:
        async with httpx.AsyncClient() as client:
            # 调用插件的 start_auto_reply 入口点
            print("\n[1] 正在启动插件...")
            response = await client.post(
                plugin_api,
                json={
                    "plugin_id": "qq_auto_reply",
                    "entry_id": "start_auto_reply",
                    "args": {}
                },
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"[OK] 插件启动成功!")
                print(f"    状态: {result}")

                print("\n" + "="*60)
                print("QQ 自动回复插件已启动")
                print("="*60)
                print("\n现在可以向机器人 QQ 号发送私聊消息进行测试")
                print("\n停止插件:")
                print("  python stop_plugin.py")

            else:
                print(f"[ERROR] 插件启动失败: HTTP {response.status_code}")
                print(f"    响应: {response.text}")

    except httpx.ConnectError:
        print("[ERROR] 无法连接到 NEKO Agent Server")
        print("    请确保 NEKO 主服务正在运行")
        sys.exit(1)

    except Exception as e:
        print(f"[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(start_qq_auto_reply())
