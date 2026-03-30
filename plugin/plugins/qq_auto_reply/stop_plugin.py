#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import httpx
import sys

async def stop_qq_auto_reply():
    """停止 QQ 自动回复插件"""

    print("="*60)
    print("停止 QQ 自动回复插件")
    print("="*60)

    plugin_api = "http://localhost:48915/plugin/execute"

    try:
        async with httpx.AsyncClient() as client:
            print("\n[1] 正在停止插件...")
            response = await client.post(
                plugin_api,
                json={
                    "plugin_id": "qq_auto_reply",
                    "entry_id": "stop_auto_reply",
                    "args": {}
                },
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"[OK] 插件已停止!")
                print(f"    状态: {result}")
            else:
                print(f"[ERROR] 停止失败: HTTP {response.status_code}")
                print(f"    响应: {response.text}")

    except httpx.ConnectError:
        print("[ERROR] 无法连接到 NEKO Agent Server")
        sys.exit(1)

    except Exception as e:
        print(f"[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(stop_qq_auto_reply())
