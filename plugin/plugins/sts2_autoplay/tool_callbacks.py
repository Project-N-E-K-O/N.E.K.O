"""HTTP callback server for STS2 tool_call invocations.

Plugins run as child processes (via ``multiprocessing.Process``), so they
do NOT share the plugin server's FastAPI app. The tool_calling protocol
requires a ``callback_url`` that main_server can POST to, which means
the plugin must run its own lightweight HTTP server.

This module provides:
- ``create_tool_callback_app(service)`` -- build a FastAPI app with callbacks
- ``start_callback_server(app, logger)`` -- start it on a random available port
- ``stop_callback_server(server)`` -- clean shutdown

Response contract (per ``docs/zh-CN/plugins/tool-calling.md``):
  success: ``{"output": <any JSON>, "is_error": false}``
  failure: ``{"error": "human-readable message", "is_error": true}``
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _find_available_port(host: str = "127.0.0.1", start: int = 49100, max_tries: int = 100) -> int:
    """Find an available TCP port on localhost."""
    for offset in range(max_tries):
        port = start + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"no available port in range {start}-{start + max_tries - 1}")


def create_tool_callback_app(service: Any) -> FastAPI:
    """Create a minimal FastAPI app with callback endpoints for each tool.

    This app runs inside the plugin child process on its own port,
    separate from the plugin server's FastAPI app.
    """
    app = FastAPI(title="STS2 Tool Callbacks")

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

    @app.post("/sts2_neko_command")
    async def cb_neko_command(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.neko_command(
                command=str(args.get("command", "")),
                scope=str(args.get("scope", "auto")),
                confirm=bool(args.get("confirm", False)),
            )
        return await _safe_call(request, handler)

    @app.post("/sts2_recommend_one_card")
    async def cb_recommend_one_card(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.recommend_one_card_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @app.post("/sts2_play_one_card")
    async def cb_play_one_card(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.play_one_card_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @app.post("/sts2_autoplay_control")
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

    @app.post("/sts2_review_play")
    async def cb_review_play(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.review_recent_play_by_neko(
                objective=args.get("objective"),
            )
        return await _safe_call(request, handler)

    @app.post("/sts2_send_guidance")
    async def cb_send_guidance(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.send_neko_guidance({
                "content": str(args.get("content", "")),
                "step": None,
                "type": "soft_guidance",
            })
        return await _safe_call(request, handler)

    @app.post("/sts2_get_status")
    async def cb_get_status(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.get_status()
        return await _safe_call(request, handler)

    @app.post("/sts2_autoplay_question")
    async def cb_autoplay_question(request: Request) -> JSONResponse:
        async def handler(args: Dict[str, Any]) -> Any:
            return await service.answer_autoplay_question_by_neko(
                question=str(args.get("question", "")),
            )
        return await _safe_call(request, handler)

    return app


async def start_callback_server(
    app: FastAPI, logger: Any, *, host: str = "127.0.0.1",
) -> Tuple[Any, int]:
    """Start the callback HTTP server on a dynamically selected port.

    Returns ``(server_instance, port)`` so the caller can build
    callback_urls and stop the server later.
    """
    import uvicorn

    port = _find_available_port(host)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(config)

    # Run the server in a background task so it doesn't block the event loop
    serve_task = asyncio.create_task(server.serve())
    # Give uvicorn a moment to bind the socket
    await asyncio.sleep(0.3)

    logger.info("STS2 tool callback server started on %s:%d", host, port)
    return server, port


async def stop_callback_server(server: Any, logger: Any) -> None:
    """Gracefully shut down the callback HTTP server."""
    if server is None:
        return
    try:
        server.should_exit = True
        # Give it a moment to finish in-flight requests
        await asyncio.sleep(0.5)
        logger.info("STS2 tool callback server stopped")
    except Exception as e:
        logger.warning("Error stopping tool callback server: %s", e)
