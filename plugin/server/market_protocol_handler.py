"""neko:// URI Scheme 协议处理器。

当系统通过 neko:// URI 唤起 N.E.K.O 时，此模块解析 URI 并执行对应动作。

支持的 URI：
  neko://install?url={package_url}&sha256={hash}&id={plugin_id}&version={ver}
  neko://auth/callback?code={oauth_code}&state={state}
  neko://pair?code={one_time_code}
  neko://open?plugin={plugin_id}

使用方式：
  python -m plugin.server.market_protocol_handler "neko://install?url=..."

或在 launcher.py 中检测 sys.argv 是否包含 neko:// URI 并调用 handle_uri()。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from plugin.logging_config import get_logger

logger = get_logger("server.market_protocol_handler")


def handle_uri(uri: str) -> int:
    """解析并处理 neko:// URI。返回退出码。"""
    logger.info("Handling protocol URI: {}", uri)

    parsed = urlparse(uri)
    if parsed.scheme != "neko":
        logger.error("Not a neko:// URI: {}", uri)
        return 1

    # netloc 是 action（如 install, auth, pair, open）
    # 对于 neko://install?... → netloc="install", path=""
    # 对于 neko://auth/callback?... → netloc="auth", path="/callback"
    action = parsed.netloc
    sub_path = parsed.path.lstrip("/")
    params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

    if action == "install":
        return _handle_install(params)
    elif action == "auth" and sub_path == "callback":
        return _handle_auth_callback(params)
    elif action == "pair":
        return _handle_pair(params)
    elif action == "open":
        return _handle_open(params)
    else:
        logger.warning("Unknown protocol action: {}:{}", action, sub_path)
        return 1


def _handle_install(params: dict) -> int:
    """处理 neko://install — 下载并安装插件包。"""
    url = params.get("url")
    sha256 = params.get("sha256")

    if not url or not sha256:
        logger.error("install requires 'url' and 'sha256' params")
        return 1

    plugin_id = params.get("id", "unknown")
    version = params.get("version", "")
    payload_hash = params.get("payload_hash")

    logger.info("Protocol install: plugin={} version={} url={}", plugin_id, version, url)

    # 调用本地 Market Bridge API
    return asyncio.run(_call_local_install(
        package_url=url,
        package_sha256=sha256,
        plugin_id=plugin_id,
        version=version,
        payload_hash=payload_hash,
    ))


async def _call_local_install(
    package_url: str,
    package_sha256: str,
    plugin_id: str,
    version: str,
    payload_hash: str | None,
) -> int:
    """调用本地 Plugin Server 的 /market/install 端点。"""
    import httpx
    from plugin.server.routes.market_bridge import get_bridge_token

    token = get_bridge_token()
    base_url = "http://127.0.0.1:48911"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            # 触发安装
            res = await client.post(
                f"{base_url}/market/install",
                params={"token": token},
                json={
                    "package_url": package_url,
                    "package_sha256": package_sha256,
                    "payload_hash": payload_hash,
                    "plugin_id": plugin_id,
                    "version": version,
                    "on_conflict": "rename",
                },
            )

            if res.status_code != 200:
                logger.error("Install request failed: {} {}", res.status_code, res.text)
                return 1

            data = res.json()
            task_id = data.get("task_id")
            logger.info("Install task created: {}", task_id)

            # 轮询等待完成
            import time
            for _ in range(180):  # 最多等 3 分钟
                await asyncio.sleep(1)
                status_res = await client.get(
                    f"{base_url}/market/tasks/{task_id}",
                    params={"token": token},
                )
                if status_res.status_code != 200:
                    continue

                task = status_res.json()
                status = task.get("status")
                logger.info("Task {}: {} - {}", task_id, status, task.get("message", ""))

                if status == "completed":
                    logger.info("Plugin installed successfully: {}", plugin_id)
                    _show_notification(f"插件 {plugin_id} 安装成功", "N.E.K.O")
                    return 0
                elif status == "failed":
                    error = task.get("error", "未知错误")
                    logger.error("Install failed: {}", error)
                    _show_notification(f"插件安装失败: {error}", "N.E.K.O")
                    return 1

            logger.error("Install timed out")
            return 1

    except httpx.ConnectError:
        logger.error("Cannot connect to local plugin server. Is N.E.K.O running?")
        _show_notification("无法连接到 N.E.K.O，请确认客户端正在运行", "N.E.K.O")
        return 1
    except Exception as exc:
        logger.error("Install error: {}", exc)
        return 1


def _handle_auth_callback(params: dict) -> int:
    """处理 neko://auth/callback — OAuth 授权回调。"""
    code = params.get("code")
    state = params.get("state")

    if not code or not state:
        logger.error("auth/callback requires 'code' and 'state' params")
        return 1

    logger.info("OAuth callback received: state={}", state)

    # 将 code 写入临时文件，供 N.E.K.O 面板读取
    callback_file = Path.home() / ".neko" / "oauth_callback.json"
    callback_file.parent.mkdir(parents=True, exist_ok=True)

    import json
    import time
    callback_file.write_text(
        json.dumps({"code": code, "state": state, "timestamp": time.time()}),
        encoding="utf-8",
    )
    logger.info("OAuth callback saved to {}", callback_file)
    _show_notification("Market 授权成功，请返回 N.E.K.O", "N.E.K.O")
    return 0


def _handle_pair(params: dict) -> int:
    """处理 neko://pair — Market 配对。"""
    code = params.get("code")
    if not code:
        logger.error("pair requires 'code' param")
        return 1

    logger.info("Pair request received")
    # 配对码验证由 /market/token-exchange 处理
    # 这里只是确认客户端在线
    _show_notification(f"收到配对请求，配对码: {code[:4]}...", "N.E.K.O")
    return 0


def _handle_open(params: dict) -> int:
    """处理 neko://open — 打开本地面板。"""
    plugin_id = params.get("plugin")
    logger.info("Open request: plugin={}", plugin_id)
    # 可以通过 WebSocket 通知前端打开对应面板
    return 0


def _show_notification(message: str, title: str) -> None:
    """显示系统通知（跨平台）。"""
    try:
        import platform
        system = platform.system()

        if system == "Windows":
            # Windows Toast
            try:
                from ctypes import windll
                windll.user32.MessageBoxW(0, message, title, 0x40)
            except Exception:
                pass
        elif system == "Darwin":
            import subprocess
            subprocess.Popen([
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"'
            ])
        elif system == "Linux":
            import subprocess
            subprocess.Popen(["notify-send", title, message])
    except Exception as exc:
        logger.debug("Failed to show notification: {}", exc)


# ─── 入口 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m plugin.server.market_protocol_handler <neko://...>")
        sys.exit(1)

    uri = sys.argv[1]
    sys.exit(handle_uri(uri))
