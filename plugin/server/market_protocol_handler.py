"""neko:// URI scheme protocol handler.

When the system launches N.E.K.O through a neko:// URI, parse it and perform the corresponding action.

Supported URIs:
  neko://install?url={package_url}&sha256={hash}&id={plugin_id}&version={ver}
  neko://pair?code={one_time_code}
  neko://open?plugin={plugin_id}&panel={panel}&intent={intent}

Usage:
  python -m plugin.server.market_protocol_handler \"neko://install?url=...\"

Alternatively detect neko:// URIs in sys.argv in launcher.py and call handle_uri().
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from plugin.logging_config import get_logger

logger = get_logger("server.market_protocol_handler")

# On startup, Plugin Server writes the bridge token and listening port here through ``write_bridge_token_file``
# for the independent URI-handler process to read. Sharing this file prevents
# reimporting market_bridge in the handler from generating a different in-memory token.
_BRIDGE_FILE = Path.home() / ".neko" / "bridge.json"
_INSTALL_POLL_INTERVAL_SECONDS = 1.0
_INSTALL_POLL_TIMEOUT_SECONDS = 180.0


def _load_bridge_info() -> dict[str, Any] | None:
    """Read bridge.json, returning ``{\"token\": ..., \"port\": ...}`` or None."""

    try:
        raw = _BRIDGE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(
            "Bridge token file not found at {}. 请先启动 N.E.K.O 主程序后再使用 neko:// 链接。",
            _BRIDGE_FILE,
        )
        return None
    except OSError as exc:
        logger.error("Failed to read bridge token file {}: {}", _BRIDGE_FILE, exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Bridge token file is not valid JSON ({}): {}", _BRIDGE_FILE, exc)
        return None

    if not isinstance(data, dict):
        logger.error("Bridge token file payload is not a JSON object: {}", _BRIDGE_FILE)
        return None

    token = data.get("token")
    if not isinstance(token, str) or not token:
        logger.error("Bridge token file missing 'token' field: {}", _BRIDGE_FILE)
        return None

    port_value = data.get("port", 48911)
    try:
        port = int(port_value)
    except (TypeError, ValueError):
        logger.warning(
            "Bridge token file has invalid 'port' value {!r}; falling back to 48911",
            port_value,
        )
        port = 48911

    return {"token": token, "port": port}


def handle_uri(uri: str) -> int:
    """Parse and handle a neko:// URI, returning an exit code."""
    parsed = urlparse(uri)
    safe_uri = _safe_uri_for_log(parsed)
    logger.info("Handling protocol URI: {}", safe_uri)

    if parsed.scheme != "neko":
        logger.error("Not a neko:// URI: {}", safe_uri)
        return 1

    # netloc is the action (such as install, pair, open)
    # For neko://install?... → netloc="install", path=""
    action = parsed.netloc
    sub_path = parsed.path.lstrip("/")
    params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

    if action == "install":
        return _handle_install(params)
    elif action == "pair":
        return _handle_pair(params)
    elif action == "open":
        return _handle_open(params)
    else:
        logger.warning("Unknown protocol action: {}:{}", action, sub_path)
        return 1


def _safe_uri_for_log(parsed) -> str:
    """Return a URI representation with OAuth-like secrets redacted."""
    sensitive_keys = {"code", "state", "token", "bridge_token", "one_time_code"}
    query = parse_qs(parsed.query, keep_blank_values=True)
    redacted = {
        key: (["<redacted>"] if key in sensitive_keys else value)
        for key, value in query.items()
    }
    return urlunparse(parsed._replace(query=urlencode(redacted, doseq=True)))


def _handle_install(params: dict) -> int:
    """Handle neko://install: download and install a plugin package."""
    url = params.get("url")
    sha256 = params.get("sha256")
    plugin_id = params.get("id")
    version = params.get("version")

    # The bridge binds every Market install to a catalogue release, which
    # needs the Market plugin ID and version.
    if not url or not sha256 or not plugin_id or not version:
        logger.error("install requires 'url', 'sha256', 'id' and 'version' params")
        _show_notification("插件安装链接缺少必要参数，请从插件市场重新打开", "N.E.K.O")
        return 1

    payload_hash = params.get("payload_hash")
    channel = params.get("channel")
    published_at = params.get("published_at")
    expected_plugin_toml_id = params.get("expected_plugin_toml_id")
    mode = params.get("mode", "install")
    on_conflict = params.get("on_conflict", "fail")

    logger.info("Protocol install: plugin={} version={} url={}", plugin_id, version, url)

    # Call the local Market Bridge API
    return asyncio.run(_call_local_install(
        package_url=url,
        package_sha256=sha256,
        plugin_id=plugin_id,
        version=version,
        payload_hash=payload_hash,
        channel=channel,
        published_at=published_at,
        expected_plugin_toml_id=expected_plugin_toml_id,
        mode=mode,
        on_conflict=on_conflict,
    ))


async def _call_local_install(
    package_url: str,
    package_sha256: str,
    plugin_id: str,
    version: str,
    payload_hash: str | None,
    channel: str | None,
    published_at: str | None,
    expected_plugin_toml_id: str | None,
    mode: str,
    on_conflict: str,
) -> int:
    """Call the local Plugin Server's /market/install endpoint."""
    import httpx

    bridge = _load_bridge_info()
    if bridge is None:
        _show_notification("无法读取 N.E.K.O bridge 凭据，请确认客户端正在运行", "N.E.K.O")
        return 1

    token = bridge["token"]
    base_url = f"http://127.0.0.1:{bridge['port']}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            # Trigger installation
            res = await client.post(
                f"{base_url}/market/install",
                params={"token": token},
                json={
                    "package_url": package_url,
                    "package_sha256": package_sha256,
                    "payload_hash": payload_hash,
                    "plugin_id": plugin_id,
                    "expected_plugin_toml_id": expected_plugin_toml_id,
                    "version": version,
                    "channel": channel,
                    "published_at": published_at,
                    "mode": mode,
                    "on_conflict": on_conflict,
                },
            )

            if res.status_code != 200:
                logger.error("Install request failed: {} {}", res.status_code, res.text)
                try:
                    detail = res.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                reason = detail.get("message") if isinstance(detail, dict) else None
                _show_notification(f"插件安装失败: {reason or res.status_code}", "N.E.K.O")
                return 1

            data = res.json()
            task_id = data.get("task_id")
            if not task_id:
                logger.error("Install task response did not include task_id")
                return 1
            logger.info("Install task created: {}", task_id)

            # Poll until completion. Bridge download timeout is 120s before unpack
            # work starts, so the protocol handler must wait longer than that.
            deadline = asyncio.get_running_loop().time() + _INSTALL_POLL_TIMEOUT_SECONDS
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(_INSTALL_POLL_INTERVAL_SECONDS)
                try:
                    status_res = await client.get(
                        f"{base_url}/market/tasks/{task_id}",
                        params={"token": token},
                        timeout=10.0,
                    )
                except httpx.HTTPError as exc:
                    logger.warning("Task poll failed for {}: {}", task_id, exc)
                    continue
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
                elif status == "canceled":
                    # Cancellation is a terminal state the user asked for, not a
                    # failure — stop polling and exit cleanly instead of waiting
                    # out the timeout with no notification.
                    logger.info("Install canceled: {}", plugin_id)
                    _show_notification(f"已取消安装插件 {plugin_id}", "N.E.K.O")
                    return 0

            # Timing out here means we stopped watching, not that the install
            # failed — it is still running in the app. Say so instead of
            # exiting silently.
            logger.error("Install timed out")
            _show_notification(
                f"插件 {plugin_id} 安装仍在进行中，请回到 N.E.K.O 查看进度",
                "N.E.K.O",
            )
            return 1

    except httpx.ConnectError:
        logger.error("Cannot connect to local plugin server. Is N.E.K.O running?")
        _show_notification("无法连接到 N.E.K.O，请确认客户端正在运行", "N.E.K.O")
        return 1
    except Exception as exc:
        logger.error("Install error: {}", exc)
        return 1


def _handle_pair(params: dict) -> int:
    """Handle neko://pair: Market pairing."""
    code = params.get("code")
    if not code:
        logger.error("pair requires 'code' param")
        return 1

    logger.info("Pair request received")
    # /market/token-exchange handles pairing-code validation
    # This only confirms the client is online
    _show_notification(f"收到配对请求，配对码: {code[:4]}...", "N.E.K.O")
    return 0


def _handle_open(params: dict) -> int:
    """Handle neko://open: open a local panel."""
    plugin_id = params.get("plugin")
    panel = params.get("panel")
    intent = params.get("intent")
    logger.info("Open request: plugin={} panel={} intent={}", plugin_id, panel, intent)
    if intent == "login" or panel == "account":
        _show_notification("请回到 N.E.K.O 完成账号登录同步", "N.E.K.O")
    # A WebSocket notification can ask the frontend to open the corresponding panel
    return 0


def _show_notification(message: str, title: str) -> None:
    """Show a cross-platform system notification."""
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
            script = (
                "on run argv\n"
                "display notification (item 1 of argv) with title (item 2 of argv)\n"
                "end run"
            )
            subprocess.Popen(["osascript", "-e", script, message, title])
        elif system == "Linux":
            import subprocess
            subprocess.Popen(["notify-send", title, message])
    except Exception as exc:
        logger.debug("Failed to show notification: {}", exc)


# ─── Entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m plugin.server.market_protocol_handler <neko://...>")
        sys.exit(1)

    uri = sys.argv[1]
    sys.exit(handle_uri(uri))
