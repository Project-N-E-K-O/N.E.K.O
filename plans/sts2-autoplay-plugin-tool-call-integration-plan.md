# STS2 Autoplay Plugin Tool Call Integration Plan (v4)

## Background

PR #1035 introduced a unified tool calling system. Official doc: `docs/zh-CN/plugins/tool-calling.md`.

## Architecture

```
Plugin (user_plugin_server, port NEKO_PLUGIN_PORT)     Main Server (port 48911)
  |                                                          |
  |-- POST /api/tools/register ----------------------------->|  startup
  |     callback_url = http://127.0.0.1:plugin_port/api/... |
  |                                                          |
  |<-- POST callback_url -----------------------------------|  LLM tool_call
  |     {name, arguments, call_id}                           |
  |                                                          |
  |-- {output, is_error} ---------------------------------->|  result to LLM
  |                                                          |
  |-- POST /api/tools/clear -------------------------------->|  shutdown
```

## Tool Selection: 8 Tools

| # | Tool Name | Maps To | Type |
|---|-----------|---------|------|
| 1 | `sts2_neko_command` | `neko_command()` | NL Router |
| 2 | `sts2_recommend_one_card` | `recommend_one_card_by_neko()` | Read-only |
| 3 | `sts2_play_one_card` | `play_one_card_by_neko()` | Destructive |
| 4 | `sts2_autoplay_control` | `start/pause/resume/stop_autoplay()` | Control |
| 5 | `sts2_review_play` | `review_recent_play_by_neko()` | Read-only |
| 6 | `sts2_send_guidance` | `send_neko_guidance()` | Soft input |
| 7 | `sts2_get_status` | `get_status()` | Read-only |
| 8 | `sts2_autoplay_question` | `answer_autoplay_question_by_neko()` | Read-only |

## Files to Create/Modify

### NEW: `plugin/plugins/sts2_autoplay/tool_bridge.py`

```python
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from config import MAIN_SERVER_PORT

_SOURCE_TAG = "plugin:sts2_autoplay"
_MAIN_BASE = f"http://127.0.0.1:{MAIN_SERVER_PORT}"
_TOOLS_API = f"{_MAIN_BASE}/api/tools"

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
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
                    "description": "意图提示：auto/status/advice/one_card/one_action/autoplay/control/guidance/review/question",
                },
                "confirm": {"type": "boolean", "default": False, "description": "是否已确认允许持续托管"},
            },
            "required": ["command"],
        },
        "timeout_seconds": 30,
    },
    {
        "name": "sts2_recommend_one_card",
        "description": "当用户询问杀戮尖塔当前打哪张牌好时调用：只读取状态并推荐一张牌，说明理由，不会自动打出卡牌。",
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户咨询目标"},
            },
        },
        "timeout_seconds": 30,
    },
    {
        "name": "sts2_play_one_card",
        "description": "仅当用户明确授权实际操作、帮我选一张牌打出去时调用。会选择一张牌并执行出牌。",
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户授权目标"},
            },
        },
        "timeout_seconds": 30,
    },
    {
        "name": "sts2_autoplay_control",
        "description": "杀戮尖塔自动游玩控制。支持 action: start/pause/resume/stop。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "pause", "resume", "stop"], "description": "控制动作"},
                "objective": {"type": "string", "description": "仅 start 时有效，用户授权目标"},
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
    {
        "name": "sts2_review_play",
        "description": (
            "杀戮尖塔轻量牌感点评。当用户问'我牌打得怎么样'、'评价一下刚才的出牌'、"
            "'吐槽一下'等复盘类问题时调用。猫娘会根据最近可见快照评价出牌节奏、"
            "攻防平衡和关键牌表现。只读操作，不执行游戏动作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "用户的复盘问题"},
            },
        },
        "timeout_seconds": 30,
    },
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
    {
        "name": "sts2_get_status",
        "description": "获取杀戮尖塔连接状态、自动游玩状态和最近错误。",
        "parameters": {"type": "object", "properties": {}},
        "timeout_seconds": 10,
    },
    {
        "name": "sts2_autoplay_question",
        "description": (
            "回答用户关于杀戮尖塔自动游玩过程的问题。"
            "当 autoplay 运行或刚暂停时，用户问'打到哪了'、'为什么选那张牌'等问题时调用。"
            "只读操作。"
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
    return f"http://127.0.0.1:{plugin_port}/api/sts2_autoplay/tools/{tool_name}"


async def register_all_tools(logger, *, plugin_port: int) -> None:
    """Register all STS2 tools with retry per official doc pattern."""
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
                    r = await client.post(f"{_TOOLS_API}/register", json=payload, timeout=5)
                    body = r.json()
                    if body.get("ok"):
                        logger.info("Registered tool '%s': roles=%s", tool_def["name"], body.get("affected_roles"))
                        break
                    else:
                        logger.warning("Tool '%s' register ok=false: %s", tool_def["name"], body.get("failed_roles"))
                        break
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass  # main_server not ready
                except Exception as e:
                    logger.warning("Tool '%s' register error: %s", tool_def["name"], e)
                    break
                await asyncio.sleep(2)


async def unregister_all_tools(logger) -> None:
    """Clear all STS2 tools on shutdown."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.post(f"{_TOOLS_API}/clear", json={"source": _SOURCE_TAG})
            if r.status_code == 200:
                logger.info("Cleared tools: %s", r.json().get("removed"))
    except Exception as e:
        logger.warning("Failed to clear tools: %s", e)
```

### NEW: `plugin/plugins/sts2_autoplay/tool_callbacks.py`

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def create_tool_callback_router(service) -> APIRouter:
    """Create FastAPI router for tool_call callback endpoints."""
    router = APIRouter(prefix="/api/sts2_autoplay/tools", tags=["sts2_tools"])

    async def _safe_call(request: Request, handler):
        body = await request.json()
        args = body.get("arguments", {})
        try:
            result = await handler(args)
            return JSONResponse({"output": result, "is_error": False})
        except Exception as e:
            return JSONResponse({"error": str(e), "is_error": True})

    @router.post("/sts2_neko_command")
    async def cb_neko_command(request: Request):
        async def handler(args):
            return await service.neko_command(
                command=str(args.get("command", "")),
                scope=str(args.get("scope", "auto")),
                confirm=bool(args.get("confirm", False)),
            )
        return await _safe_call(request, handler)

    @router.post("/sts2_recommend_one_card")
    async def cb_recommend(request: Request):
        async def handler(args):
            return await service.recommend_one_card_by_neko(objective=args.get("objective"))
        return await _safe_call(request, handler)

    @router.post("/sts2_play_one_card")
    async def cb_play(request: Request):
        async def handler(args):
            return await service.play_one_card_by_neko(objective=args.get("objective"))
        return await _safe_call(request, handler)

    @router.post("/sts2_autoplay_control")
    async def cb_control(request: Request):
        async def handler(args):
            action = str(args.get("action", ""))
            if action == "start":
                return await service.start_autoplay(
                    objective=args.get("objective"),
                    stop_condition=args.get("stop_condition", "current_floor"),
                )
            elif action == "pause":
                return await service.pause_autoplay()
            elif action == "resume":
                return await service.resume_autoplay()
            elif action == "stop":
                return await service.stop_autoplay()
            raise ValueError(f"unknown action: {action}")
        return await _safe_call(request, handler)

    @router.post("/sts2_review_play")
    async def cb_review(request: Request):
        async def handler(args):
            return await service.review_recent_play_by_neko(objective=args.get("objective"))
        return await _safe_call(request, handler)

    @router.post("/sts2_send_guidance")
    async def cb_guidance(request: Request):
        async def handler(args):
            return await service.send_neko_guidance({
                "content": str(args.get("content", "")),
                "step": None,
                "type": "soft_guidance",
            })
        return await _safe_call(request, handler)

    @router.post("/sts2_get_status")
    async def cb_status(request: Request):
        async def handler(args):
            return await service.get_status()
        return await _safe_call(request, handler)

    @router.post("/sts2_autoplay_question")
    async def cb_question(request: Request):
        async def handler(args):
            return await service.answer_autoplay_question_by_neko(
                question=str(args.get("question", "")),
            )
        return await _safe_call(request, handler)

    return router
```

### MODIFY: `plugin/plugins/sts2_autoplay/__init__.py`

```python
# Add imports at top:
from .tool_bridge import register_all_tools, unregister_all_tools
from .tool_callbacks import create_tool_callback_router

# Modify startup():
@lifecycle(id="startup")
async def startup(self, **_: Any):
    cfg = _as_mapping(await self.config.dump(timeout=5.0))
    self._cfg = _as_mapping(cfg.get("sts2"))
    await self._service.startup(self._cfg)

    # Mount tool callback endpoints on plugin HTTP server
    self._tool_callback_router = create_tool_callback_router(self._service)
    self.include_router(self._tool_callback_router)

    # Register tools with main_server ToolRegistry (background with retry)
    plugin_port = self._resolve_plugin_port()
    import asyncio
    asyncio.create_task(register_all_tools(self.logger, plugin_port=plugin_port))

    return Ok({"status": "ready", "result": await self._service.get_status()})

# Modify shutdown():
@lifecycle(id="shutdown")
async def shutdown(self, **_: Any):
    await unregister_all_tools(self.logger)
    await self._service.shutdown()
    return Ok({"status": "shutdown"})

# Add helper:
def _resolve_plugin_port(self) -> int:
    import os
    return int(os.environ.get("NEKO_PLUGIN_PORT", "48912"))
```

## Implementation Checklist

- [ ] Create `tool_bridge.py` with 8 tool definitions and register/unregister functions
- [ ] Create `tool_callbacks.py` with FastAPI callback router (8 endpoints)
- [ ] Modify `__init__.py` startup: mount callbacks + background register
- [ ] Modify `__init__.py` shutdown: unregister tools
- [ ] Verify plugin_port discovery (env var NEKO_PLUGIN_PORT)
- [ ] Verify callback_url path matches router prefix
- [ ] Test: tool definitions have valid JSON schemas
- [ ] Test: callback endpoints return correct format
- [ ] Test: startup registers, shutdown clears
- [ ] Optional later: set `agent_hidden: True` on equivalent plugin_entries
