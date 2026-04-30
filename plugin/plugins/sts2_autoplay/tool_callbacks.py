"""HTTP callback server for STS2 tool_call invocations.

Plugins run as child processes (via ``multiprocessing.Process``). The
plugin host runs ``startup()`` on a **temporary** event loop that closes
once startup returns. Any ``asyncio.create_task()`` on that loop will be
cancelled.

To keep the callback HTTP server alive, we run uvicorn in a **dedicated
daemon thread** with its own persistent event loop. Tool registration
also runs on that loop after the server is ready.

Lifecycle:
  startup  -> ``start_callback_server()``  (spawns thread, returns port)
  shutdown -> ``stop_callback_server()``   (signals exit, joins thread)
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


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


def _build_callback_app(service: Any) -> Any:
    """Create a minimal FastAPI app with callback endpoints for each tool."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="STS2 Tool Callbacks")

    async def _safe_call(
        request: Request,
        handler: Callable[[Dict[str, Any]], Awaitable[Any]],
    ) -> JSONResponse:
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


class _CallbackServerHandle:
    """Opaque handle for the callback server running in a background thread."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Any = None  # uvicorn.Server
        self.port: int = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def start_callback_server(
    service: Any, logger: Any, *, host: str = "127.0.0.1",
) -> _CallbackServerHandle:
    """Start the callback HTTP server in a dedicated daemon thread.

    The thread runs its own event loop so the server survives the
    temporary startup loop closing. Returns a handle for later shutdown.

    This function is synchronous and can be called from ``startup()``.
    """
    import uvicorn

    port = _find_available_port(host)
    app = _build_callback_app(service)
    handle = _CallbackServerHandle()
    handle.port = port

    ready_event = threading.Event()

    def _run_server() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        handle._loop = loop

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            log_config=None,
        )
        server = uvicorn.Server(config)
        handle._server = server

        async def _serve_and_signal() -> None:
            # Start serving. uvicorn.Server sets self.started=True once
            # it is accepting connections. We poll for that in a background
            # task and signal the main thread.
            async def _wait_started() -> None:
                for _ in range(100):  # up to 5 seconds
                    if getattr(server, "started", False):
                        ready_event.set()
                        return
                    await asyncio.sleep(0.05)
                # Fallback: signal anyway so main thread doesn't hang
                ready_event.set()

            asyncio.ensure_future(_wait_started())
            await server.serve()

        try:
            loop.run_until_complete(_serve_and_signal())
        except Exception as exc:
            logger.warning("Callback server loop exited: %s", exc)
        finally:
            ready_event.set()  # unblock main thread if serve() failed early
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(
        target=_run_server,
        name="sts2-tool-callback-server",
        daemon=True,
    )
    thread.start()
    handle._thread = thread

    # Wait for the server to be ready (up to 5 seconds)
    if ready_event.wait(timeout=5.0):
        logger.info("STS2 tool callback server started on %s:%d (thread=%s)", host, port, thread.name)
    else:
        logger.warning("STS2 tool callback server did not become ready within 5s")

    return handle


def stop_callback_server(handle: Optional[_CallbackServerHandle], logger: Any) -> None:
    """Gracefully shut down the callback HTTP server.

    Synchronous -- safe to call from ``shutdown()`` on the temporary loop.
    """
    if handle is None or not handle.is_running:
        return
    try:
        if handle._server is not None:
            handle._server.should_exit = True
        if handle._thread is not None:
            handle._thread.join(timeout=3.0)
            if handle._thread.is_alive():
                logger.warning("Callback server thread did not exit within 3s")
            else:
                logger.info("STS2 tool callback server stopped")
    except Exception as e:
        logger.warning("Error stopping tool callback server: %s", e)
