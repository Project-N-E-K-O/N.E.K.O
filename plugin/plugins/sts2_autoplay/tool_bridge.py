"""Tool calling bridge for STS2 Autoplay plugin.

Registers LLM-callable tools with the main_server ToolRegistry via
``POST /api/tools/register`` and cleans up via ``POST /api/tools/clear``.

See ``docs/zh-CN/plugins/tool-calling.md`` for the official protocol spec.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from config import MAIN_SERVER_PORT

_SOURCE_TAG = "plugin:sts2_autoplay"
_MAIN_BASE = f"http://127.0.0.1:{MAIN_SERVER_PORT}"
_TOOLS_API = f"{_MAIN_BASE}/api/tools"

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    # 1. Main NL router
    {
        "name": "sts2_neko_command",
        "description": (
            "杀戮尖塔自然语言总入口。用户谈论杀戮尖塔相关话题时调用本工具。"
            "根据用户原话自动判断：查看状态、给建议、打一张牌、执行一步、"
            "开启自动游玩、暂停、恢复、停止或发送软指导。"
            "默认咨询不操作，只有用户明确授权时才执行游戏动作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "用户原话"},
                "scope": {
                    "type": "string",
                    "default": "auto",
                    "description": (
                        "意图提示：auto/status/advice/one_card/one_action/"
                        "autoplay/control/guidance/review/question"
                    ),
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否已确认允许持续托管",
                },
            },
            "required": ["command"],
        },
        "timeout_seconds": 30,
    },
    # 2. Card recommendation (read-only)
    {
        "name": "sts2_recommend_one_card",
        "description": (
            "当用户询问杀戮尖塔当前打哪张牌好时调用：只读取状态并推荐一张牌，"
            "说明理由，不会自动打出卡牌。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户咨询目标"},
            },
        },
        "timeout_seconds": 30,
    },
    # 3. Play one card (destructive)
    {
        "name": "sts2_play_one_card",
        "description": (
            "仅当用户明确授权实际操作、帮我选一张牌打出去时调用。"
            "会选择一张牌并执行出牌。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户授权目标"},
            },
        },
        "timeout_seconds": 30,
    },
    # 4. Autoplay control (combined start/pause/resume/stop)
    {
        "name": "sts2_autoplay_control",
        "description": "杀戮尖塔自动游玩控制。支持 action: start/pause/resume/stop。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "pause", "resume", "stop"],
                    "description": "控制动作",
                },
                "objective": {
                    "type": "string",
                    "description": "仅 start 时有效，用户授权目标",
                },
                "stop_condition": {
                    "type": "string",
                    "enum": ["current_floor", "current_combat", "manual"],
                    "default": "current_floor",
                    "description": "仅 start 时有效，停止条件",
                },
            },
            "required": ["action"],
        },
        "timeout_seconds": 30,
    },
    # 5. Review play (neko-comment)
    {
        "name": "sts2_review_play",
        "description": (
            "杀戮尖塔轻量牌感点评。当用户问'我牌打得怎么样'、"
            "'评价一下刚才的出牌'、'吐槽一下'等复盘类问题时调用。"
            "猫娘会根据最近可见快照评价出牌节奏、攻防平衡和关键牌表现。"
            "只读操作，不执行游戏动作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户的复盘问题"},
            },
        },
        "timeout_seconds": 30,
    },
    # 6. Send guidance
    {
        "name": "sts2_send_guidance",
        "description": "向后台 autoplay 发送猫娘的软指导，会在下一轮决策时被参考。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "猫娘的指导内容"},
            },
            "required": ["content"],
        },
        "timeout_seconds": 15,
    },
    # 7. Get status
    {
        "name": "sts2_get_status",
        "description": "获取杀戮尖塔连接状态、自动游玩状态和最近错误。",
        "parameters": {"type": "object", "properties": {}},
        "timeout_seconds": 10,
    },
    # 8. Autoplay question
    {
        "name": "sts2_autoplay_question",
        "description": (
            "回答用户关于杀戮尖塔自动游玩过程的问题。"
            "当 autoplay 运行或刚暂停时，用户问'打到哪了'、"
            "'为什么选那张牌'等问题时调用。只读操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户的问题"},
            },
            "required": ["question"],
        },
        "timeout_seconds": 30,
    },
]


def _build_callback_url(plugin_port: int, tool_name: str) -> str:
    """Build the callback URL for a tool on the plugin HTTP server."""
    return f"http://127.0.0.1:{plugin_port}/api/sts2_autoplay/tools/{tool_name}"


async def register_all_tools(logger: Any, *, plugin_port: int) -> None:
    """Register all STS2 tools with the main_server ToolRegistry.

    Uses the ``register_with_retry`` pattern recommended by the official
    tool-calling doc: retry indefinitely on connection errors (main_server
    may not be ready yet), break on logical failures.
    """
    async with httpx.AsyncClient() as client:
        for tool_def in TOOL_DEFINITIONS:
            payload = {
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["parameters"],
                "callback_url": _build_callback_url(plugin_port, tool_def["name"]),
                "role": None,
                "source": _SOURCE_TAG,
                "timeout_seconds": tool_def.get("timeout_seconds", 30),
            }
            while True:
                try:
                    r = await client.post(
                        f"{_TOOLS_API}/register", json=payload, timeout=5,
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
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass  # main_server not ready yet, retry
                except Exception as e:
                    logger.warning(
                        "Tool '%s' register error: %s", tool_def["name"], e,
                    )
                    break
                await asyncio.sleep(2)


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
