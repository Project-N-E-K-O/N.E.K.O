"""Tool calling bridge for STS2 Autoplay plugin.

Registers LLM-callable tools with the main_server ToolRegistry via
``POST /api/tools/register`` and cleans up via ``POST /api/tools/clear``.

See ``docs/zh-CN/plugins/tool-calling.md`` for the official protocol spec.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import httpx

from config import MAIN_SERVER_PORT

_SOURCE_TAG = "plugin:sts2_autoplay"
_MAIN_BASE = f"http://127.0.0.1:{MAIN_SERVER_PORT}"
_TOOLS_API = f"{_MAIN_BASE}/api/tools"

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    # Minimal safety valve exposed to the LLM: only stop an already-running
    # autoplay task. Status is exposed as a read-only observation tool. All
    # advice/play/start/pause/resume/review/guidance tools stay unregistered
    # so the model cannot proactively drive STS2 via tools.
    {
        "name": "sts2_autoplay_control",
        "description": (
            "仅用于停止已经运行或暂停中的杀戮尖塔自动游玩任务。"
            "本工具只接受 action=stop；不能启动、暂停、恢复、查询状态或执行游戏动作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stop"],
                    "description": "唯一允许的控制动作：stop",
                },
            },
            "required": ["action"],
        },
        "timeout_seconds": 10,
    },
    {
        "name": "sts2_get_status",
        "description": (
            "只读获取杀戮尖塔连接状态、自动游玩状态、当前界面和最近错误。"
            "本工具不会启动、停止、暂停、恢复或执行任何游戏动作。"
        ),
        "parameters": {"type": "object", "properties": {}},
        "timeout_seconds": 10,
    },
]


def _build_callback_url(callback_port: int, tool_name: str) -> str:
    """Build the callback URL for a tool on the plugin's callback HTTP server.

    The callback server runs inside the plugin child process on its own
    dedicated port (separate from the plugin server). Routes are mounted
    at the root level (no prefix).
    """
    return f"http://127.0.0.1:{callback_port}/{tool_name}"


async def register_all_tools(logger: Any, *, callback_port: int, deadline_seconds: float = 30.0) -> None:
    """Register all STS2 tools with the main_server ToolRegistry.

    Retries transient connection errors until ``deadline_seconds`` elapses.
    This keeps plugin startup bounded so tool-calling can degrade cleanly
    when main_server is unavailable.
    """
    deadline_at = time.monotonic() + max(1.0, float(deadline_seconds))
    async with httpx.AsyncClient() as client:
        for tool_def in TOOL_DEFINITIONS:
            payload = {
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["parameters"],
                "callback_url": _build_callback_url(callback_port, tool_def["name"]),
                "role": None,
                "source": _SOURCE_TAG,
                "timeout_seconds": tool_def.get("timeout_seconds", 30),
            }
            while True:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    logger.warning("Tool '%s' register timed out after %.1fs", tool_def["name"], deadline_seconds)
                    break
                try:
                    r = await client.post(
                        f"{_TOOLS_API}/register", json=payload, timeout=min(5.0, max(0.5, remaining)),
                    )
                    body = r.json()
                    if body.get("ok"):
                        logger.info(
                            "Registered tool '%s': roles=%s",
                            tool_def["name"],
                            body.get("affected_roles"),
                        )
                    else:
                        logger.warning(
                            "Tool '%s' register returned ok=false: %s",
                            tool_def["name"],
                            body.get("failed_roles"),
                        )
                    break
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    if time.monotonic() >= deadline_at:
                        logger.warning("Tool '%s' register unavailable before deadline: %s", tool_def["name"], exc)
                        break
                except Exception as e:
                    logger.warning(
                        "Tool '%s' register error: %s", tool_def["name"], e,
                    )
                    break
                await asyncio.sleep(min(2.0, max(0.1, deadline_at - time.monotonic())))


async def unregister_all_tools(logger: Any) -> None:
    """Clear all STS2 tools from the main_server ToolRegistry on shutdown."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.post(
                f"{_TOOLS_API}/clear", json={"source": _SOURCE_TAG},
            )
            if r.status_code == 200:
                logger.info("Cleared STS2 tools: %s", r.json().get("removed"))
    except Exception as e:
        logger.warning("Failed to clear STS2 tools on shutdown: %s", e)
