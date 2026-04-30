"""FastAPI callback router for STS2 tool_call invocations.

When the LLM triggers a tool call, main_server POSTs to the plugin's
``callback_url``. This module provides the HTTP endpoints that receive
those requests and dispatch to the appropriate service methods.

Response contract (per ``docs/zh-CN/plugins/tool-calling.md``):
  success: ``{"output": <any JSON>, "is_error": false}``
  failure: ``{"error": "human-readable message", "is_error": true}``
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def create_tool_callback_router(service: Any) -> APIRouter:
    """Create a FastAPI router with callback endpoints for each registered tool.

    The router is mounted on the plugin HTTP server (user_plugin_server)
    so that main_server can POST tool invocations to it.
    """
    router = APIRouter(prefix="/api/sts2_autoplay/tools", tags=["sts2_tools"])

    async def _safe_call(
        request: Request,
        handler: Callable[[Dict[str, Any]], Awaitable[Any]],
    ) -> JSONResponse:
        """Parse request body, call handler, return structured response."""
        body = await request.json()
        args = body.get("arguments", {})
        try:
            result = await handler(args)
            return JSONResponse({"output": result, "is_error": False})
        except Exception as e:
            return JSONResponse({"error": str(e), "is_error": True})

    @router.post("/sts2_neko_command")
    async def cb_neko_command(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.neko_command(
                command=str(args.get("command", "")),
                scope=str(args.get("scope", "auto")),
                confirm=bool(args.get("confirm", False)),
            )
        return await _safe_call(request, handler)

    @router.post("/sts2_recommend_one_card")
    async def cb_recommend_one_card(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.recommend_one_card_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @router.post("/sts2_play_one_card")
    async def cb_play_one_card(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.play_one_card_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @router.post("/sts2_autoplay_control")
    async def cb_autoplay_control(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            action = str(args.get("action", ""))
            if action == "start":
                result = await service.start_autoplay(
                    objective=args.get("objective"),
                    stop_condition=args.get("stop_condition", "current_floor"),
                )
                # Hint to LLM: autoplay is a background task, not yet completed
                if isinstance(result, dict) and result.get("status") == "running":
                    result["hint"] = (
                        "自动游玩已在后台启动，正在持续进行中，尚未打完。"
                        "请告知用户正在帮忙打，可以随时暂停或询问进度。"
                    )
                return result
            elif action == "pause":
                return await service.pause_autoplay()
            elif action == "resume":
                return await service.resume_autoplay()
            elif action == "stop":
                return await service.stop_autoplay()
            raise ValueError(f"unknown autoplay control action: {action}")
        return await _safe_call(request, handler)

    @router.post("/sts2_review_play")
    async def cb_review_play(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.review_recent_play_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @router.post("/sts2_send_guidance")
    async def cb_send_guidance(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.send_neko_guidance({
                "content": str(args.get("content", "")),
                "step": None,
                "type": "soft_guidance",
            })
        return await _safe_call(request, handler)

    @router.post("/sts2_get_status")
    async def cb_get_status(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.get_status()
        return await _safe_call(request, handler)

    @router.post("/sts2_autoplay_question")
    async def cb_autoplay_question(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.answer_autoplay_question_by_neko(
                question=str(args.get("question", "")),
            )
        return await _safe_call(request, handler)

    return router
