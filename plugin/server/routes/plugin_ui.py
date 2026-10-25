"""
Plugin UI static-file proxy routes

Allow plugins to inject custom frontend interfaces embedded into the main app via iframe.

Plugin directory structure:
    my_plugin/
    ├── __init__.py
    ├── plugin.toml
    └── static/           # Static-file directory
        ├── index.html    # Entry file
        ├── main.js
        └── style.css

Access paths:
    GET /plugin/{plugin_id}/ui/          -> static/index.html
    GET /plugin/{plugin_id}/ui/main.js   -> static/main.js
    GET /plugin/{plugin_id}/ui/style.css -> static/style.css
"""
import asyncio
import json
import mimetypes
import os
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from utils.deployment import has_forwarding_metadata

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.infrastructure.mutation_auth import PluginPageMutationGuardedRoute

router = APIRouter(tags=["plugin-ui"])
# Plugin pages (including published market plugins) call these routes; the
# browser token stays optional so they keep working. See
# mutation_auth.require_plugin_page_mutation_access before tightening this.
mutation_router = APIRouter(tags=["plugin-ui"], route_class=PluginPageMutationGuardedRoute)
logger = get_logger("server.routes.plugin_ui")
plugin_ui_query_service = PluginUiQueryService()

_I18N_LOCALE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,15}$")
# (mtime, payload) keyed by absolute file path. Bundles are typically <100KB
# and only change when the plugin author edits a translation file, so we keep
# the parsed bytes in process memory and revalidate via mtime on each hit.
_I18N_BUNDLE_CACHE: dict[Path, tuple[float, bytes]] = {}

# ---- Plugin static UI SSE push channel (enhanced backend → frontend communication) ----
# plugin_id -> list[asyncio.Queue]; one queue per SSE connection, broadcast via POST /ui-api/push.
# Plugins/backends push to /ui-api/push; frontend pages connect to /ui-api/events for immediate updates.
_sse_clients: dict[str, list[asyncio.Queue]] = {}
_sse_clients_lock = asyncio.Lock()
# Maximum per-client queue buffer (slow/blocked clients cannot grow it indefinitely); discard new messages when full.
_SSE_QUEUE_MAX = 100
# Push body limit (bytes): 1MB, enough for catgirl avatar chat overlays while bounding memory from malicious local chunked requests.
_PUSH_MAX_BODY = 1024 * 1024
# Trusted Origin hosts (loopback): push broadcasts accept absent Origin (plugin backend/non-browser) or local pages only.
_TRUSTED_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# ---- runs bus → SSE bridge: forward run-state transitions to the corresponding plugin's SSE clients ----
# The run protocol already emits every transition through state.bus_change_hub (bus 'runs')
# (plugin/runs/manager.py _emit_runs). Subscribe here and bridge to _sse_clients,
# letting frontend call() avoid tight polling of /runs/{id}. Bridge terminal states only (succeeded/failed/
# canceled/timeout), which are all the frontend needs.
_SSE_RUNS_BRIDGE_INSTALLED = False
_SSE_RUNS_BRIDGE_SUB = None
_SSE_RUN_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "timeout"})


def _sse_queue_put_best_effort(queue: asyncio.Queue, frame: str) -> bool:
    """Enqueue a frame for an SSE client; when full, discard the **oldest** and enqueue the new frame.

    Slow clients retain the **latest** frames (drop the oldest head item), matching a real-time stream,
    instead of discarding newly generated frames. ``asyncio.Queue`` is FIFO; ``get_nowait()`` removes the head.
    Return True if the frame was ultimately enqueued (directly or after dropping the oldest).
    """
    try:
        queue.put_nowait(frame)
        return True
    except asyncio.QueueFull:
        try:
            queue.get_nowait()      # Drop the oldest item (queue head).
            queue.put_nowait(frame)  # Enqueue the new frame.
            return True
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            return False  # Extreme race: do not crash; best effort.


def _bridge_runs_event(op: str, payload: object) -> None:
    """runs bus event → SSE queues for the corresponding plugin_id (terminal states only).

    Runs execute on the event loop (asyncio.create_task), and emits also occur on its thread,
    so direct put_nowait here is thread-safe.
    """
    try:
        if not isinstance(payload, dict):
            return
        plugin_id = payload.get("plugin_id")
        if not isinstance(plugin_id, str) or not plugin_id:
            return
        status = str(payload.get("status") or "").strip()
        if status not in _SSE_RUN_TERMINAL_STATUSES:
            return
        clients = _sse_clients.get(plugin_id)
        if not clients:
            return
        frame = "data: " + json.dumps({
            "type": "run",
            "plugin_id": plugin_id,
            "run_id": payload.get("run_id"),
            "status": status,
        }, ensure_ascii=False) + "\n\n"
        for queue_obj in list(clients):
            _sse_queue_put_best_effort(queue_obj, frame)
    except Exception:
        return


def _ensure_runs_sse_bridge() -> None:
    """Lazy installation: subscribe to runs bus on the first SSE client connection. Idempotent."""
    global _SSE_RUNS_BRIDGE_INSTALLED, _SSE_RUNS_BRIDGE_SUB
    if _SSE_RUNS_BRIDGE_INSTALLED:
        return
    try:
        _SSE_RUNS_BRIDGE_SUB = state.bus_change_hub.subscribe("runs", _bridge_runs_event)
        _SSE_RUNS_BRIDGE_INSTALLED = True
    except Exception:
        _SSE_RUNS_BRIDGE_INSTALLED = False


def _origin_is_trusted(origin: str) -> bool:
    """Push-broadcast Origin check: allow absent Origin (plugin backend/curl) or local loopback.

    Prevent malicious websites from injecting SSE messages into loopback via browser form POST (no-cors).
    """
    origin = (origin or "").strip()
    if not origin:
        return True
    try:
        host = (urlparse(origin).hostname or "").lower()
    except Exception:
        return False
    return host in _TRUSTED_ORIGIN_HOSTS


def _is_loopback_host(host: str) -> bool:
    """Check whether the peer is loopback: localhost, the full IPv4 loopback subnet 127.0.0.0/8,
    IPv6 ::1, and IPv4-mapped IPv6 ::ffff:127.x.x.x.

    After removing the push shared secret, this enforces loopback-only pushing: distrust
    forwarding headers such as X-Forwarded-For and inspect the direct peer request.client.host.
    """
    host = (host or "").strip().lower()
    if host in ("localhost", "::1"):
        return True
    if host.startswith("127."):  # All of 127.0.0.0/8 is IPv4 loopback (local backends may bind 127.0.0.2, etc.).
        return True
    return host.startswith("::ffff:127.")


def _parse_push_payload(body: bytes) -> dict:
    """Parse POST /ui-api/push input: support {\"type\":..., \"text\":..., ...} or plain text.

    type is a caller/plugin-defined message category passed through without preset server categories;
    text is required; style / avatar are optional metadata. An empty dict means no content.
    """
    raw = body.decode("utf-8", "replace").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except Exception:
            return {"text": raw}  # Invalid JSON → fall back to plain text.
        if not isinstance(payload, dict):
            return {}  # JSON but not an object → reject.
        text = str(payload.get("text") or "").strip()
        if not text:
            return {}  # Missing / empty text → reject (no plain-text fallback, lest raw JSON become the body).
        result: dict = {"text": text}
        msg_type = str(payload.get("type") or "").strip()
        if msg_type:
            result["type"] = msg_type
        # Pass optional structured data through (e.g. qq_message's qq_inbound) for direct consumption by SSE subscribers.
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            result["data"] = data
        style = str(payload.get("style") or "").strip()
        if style in ("catgirl", "narration"):
            result["style"] = style
        placement = str(payload.get("placement") or "").strip()
        if placement in ("scrolling", "top"):
            result["placement"] = placement
        avatar = str(payload.get("avatar") or "").strip()
        if avatar:
            result["avatar"] = avatar
        return result
    return {"text": raw}


class HostedUiActionRequest(BaseModel):
    args: dict[str, object] = Field(default_factory=dict)
    kind: str = "panel"
    surface_id: str = "main"
    locale: str | None = None


async def _wait_for_request_disconnect(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(0.05)


async def _await_action_or_disconnect(
    request: Request,
    action: Awaitable[dict[str, object]],
) -> dict[str, object]:
    action_task = asyncio.create_task(action)
    disconnect_task = asyncio.create_task(_wait_for_request_disconnect(request))
    try:
        done, _pending = await asyncio.wait(
            {action_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if action_task in done:
            return await action_task
        raise HTTPException(
            status_code=499,
            detail={"code": "hosted_action_client_disconnected"},
        )
    finally:
        for task in (action_task, disconnect_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(action_task, disconnect_task, return_exceptions=True)


async def _get_plugin_static_dir(plugin_id: str) -> Path | None:
    """Get the plugin's static-file directory.
    
    Return it only after the plugin explicitly calls register_static_ui().
    
    Args:
        plugin_id: Plugin ID
    
    Returns:
        Static-file directory path, or None if unregistered or absent.
    """
    return await plugin_ui_query_service.get_static_dir(plugin_id)


async def _get_static_ui_config(plugin_id: str) -> dict[str, object] | None:
    """Get the plugin's static UI configuration."""
    return await plugin_ui_query_service.get_static_ui_config(plugin_id)


def _get_mime_type(file_path: Path) -> str:
    """Get the file MIME type."""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        return mime_type
    
    # Default type mapping.
    suffix = file_path.suffix.lower()
    mime_map = {
        ".html": "text/html",
        ".htm": "text/html",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
    }
    return mime_map.get(suffix, "application/octet-stream")


@router.get("/plugin/{plugin_id}/ui")
@router.get("/plugin/{plugin_id}/ui/")
async def plugin_ui_index(plugin_id: str):
    """Get the plugin UI entry page."""
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    index_file = static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' has no index.html in static directory"
        )
    
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui-api/locale")
async def plugin_ui_api_locale(plugin_id: str) -> JSONResponse:
    """Return the currently effective global UI language.

    Generic endpoint for all static UI plugins: i18n.js fetches it once during init() to choose
    a translation bundle. Return the full locale (e.g. `zh-TW`, `en-US`) and let the
    frontend's _localeCandidates handle fallback.
    """
    try:
        from utils.language_utils import get_global_language_full

        locale = str(get_global_language_full())
    except Exception:
        locale = "en"
    return JSONResponse(
        {"locale": locale},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/plugin/{plugin_id}/ui-api/i18n/{locale}.json")
async def plugin_ui_api_i18n_bundle(plugin_id: str, locale: str) -> Response:
    """Serve translation bundles from `i18n/<locale>.json` under the plugin root.

    Generic endpoint independent of `register_static_ui()`; only an `i18n/` directory
    is needed. i18n.js usually tries several locales in `_localeCandidates` order,
    making an HTTP request for each until fallback succeeds. Therefore:
      - Use `_I18N_BUNDLE_CACHE` (by file mtime) to avoid repeated disk reads;
      - Add `max-age=300` to 200 responses for browser cache sharing across iframes;
      - Regex-allowlist locale to block path traversal.
    """
    if not _I18N_LOCALE_PATTERN.match(locale):
        raise HTTPException(status_code=404, detail=f"Invalid locale: {locale!r}")

    try:
        plugin_meta = await plugin_ui_query_service.get_plugin_meta(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)

    if plugin_meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no config_path")

    try:
        plugin_dir = Path(config_path_obj).parent.resolve()
    except Exception:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' config_path invalid")

    bundle_file = (plugin_dir / "i18n" / f"{locale}.json").resolve()
    try:
        bundle_file.relative_to(plugin_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path traversal detected")

    if not bundle_file.is_file():
        raise HTTPException(status_code=404, detail=f"Locale bundle '{locale}' not found")

    try:
        mtime = bundle_file.stat().st_mtime
    except OSError:
        raise HTTPException(status_code=404, detail=f"Locale bundle '{locale}' not readable")

    cached = _I18N_BUNDLE_CACHE.get(bundle_file)
    if cached is None or cached[0] != mtime:
        try:
            payload = bundle_file.read_bytes()
        except OSError:
            raise HTTPException(status_code=500, detail=f"Failed to read locale bundle '{locale}'")
        cached = (mtime, payload)
        _I18N_BUNDLE_CACHE[bundle_file] = cached

    return Response(
        content=cached[1],
        media_type="application/json; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "ETag": f'W/"{plugin_id}-{locale}-{int(mtime)}"',
        },
    )


@router.get("/plugin/{plugin_id}/ui-api/events")
async def plugin_ui_sse_events(plugin_id: str):
    """Plugin static UI SSE stream (real-time backend → frontend push).

    Frontend pages connect via EventSource for immediate /ui-api/push broadcast updates;
    also subscribe to runs bus, pushing ``type:run`` on completion instead of tight frontend polling.
    """
    # Bounded queue: do not buffer indefinitely for slow/blocked clients (push discards new messages when full).
    queue_obj: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAX)
    # Install runs bus → SSE bridge when the first client connects (idempotent).
    _ensure_runs_sse_bridge()

    async def event_stream():
        await _sse_clients_lock.acquire()
        try:
            _sse_clients.setdefault(plugin_id, []).append(queue_obj)
        finally:
            _sse_clients_lock.release()
        try:
            yield "data: " + json.dumps({"type": "hello", "lanes": 12}, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue_obj.get(), timeout=15.0)
                    yield payload
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await _sse_clients_lock.acquire()
            try:
                clients = _sse_clients.get(plugin_id)
                if clients and queue_obj in clients:
                    clients.remove(queue_obj)
                # Delete plugin_id when its list empties, preventing arbitrary path parameters from leaving permanent empty entries.
                if clients is not None and not clients:
                    _sse_clients.pop(plugin_id, None)
            finally:
                _sse_clients_lock.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@mutation_router.post("/plugin/{plugin_id}/ui-api/push")
async def plugin_ui_push(plugin_id: str, request: Request):
    """Broadcast a real-time message to all plugin static UI SSE clients (backend → frontend).

    Input: {\"text\": \"...\"} or plain text. Any plugin/backend may push to subscribed frontend pages.
    Return ``queued``: count of successful client-buffer enqueues, not confirmed client deliveries.

    Authentication: only direct loopback clients may push (no shared secret required; reject every
    non-loopback peer, regardless of forged Origin / forwarding headers; Origin still prevents cross-site injection).
    """
    # Proxy middleware may rewrite client.host to a loopback upstream address.
    # Push is a native local operation, so reject forwarding metadata before
    # considering that address, including when an outer proxy is loopback.
    if has_forwarding_metadata(request.headers):
        return JSONResponse({"ok": False, "error": "forwarded push rejected"}, status_code=403)
    client_host = request.client.host if request.client else ""
    if not _is_loopback_host(client_host):
        return JSONResponse({"ok": False, "error": "non-loopback push rejected"}, status_code=403)
    # Origin validation: cross-site browser form POST (no-cors) can inject SSE into loopback;
    # accept only absent Origin (plugin backend/curl) or loopback Origin.
    if not _origin_is_trusted(request.headers.get("origin", "")):
        return JSONResponse({"ok": False, "error": "invalid origin"}, status_code=403)
    # Stream the request body, counting bytes and immediately returning 413 over the fixed limit to bound malicious local chunked-request memory.
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _PUSH_MAX_BODY:
            return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    payload = _parse_push_payload(body)
    if not payload.get("text"):
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    # Plugins define type, passed through unchanged (server has no preset categories); omit the type field if unspecified.
    event: dict = {"text": payload["text"]}
    if payload.get("type"):
        event["type"] = payload["type"]
    if "data" in payload:  # Empty containers ([]/{}) often mean clear/reset; preserve based on key presence rather than truthiness.
        event["data"] = payload["data"]
    if payload.get("style"):
        event["style"] = payload["style"]
    if payload.get("placement"):
        event["placement"] = payload["placement"]
    if payload.get("avatar"):
        event["avatar"] = payload["avatar"]
    data = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    queued = 0
    await _sse_clients_lock.acquire()
    try:
        clients = _sse_clients.get(plugin_id, [])
        for c in list(clients):
            try:
                if _sse_queue_put_best_effort(c, data):  # When full, drop the oldest item and retain the new frame.
                    queued += 1
            except Exception as exc:  # Record other runtime failures (e.g. closed queues) without blocking other clients.
                logger.warning("[plugin-ui] SSE push 队列写入失败: %s", exc)
    finally:
        _sse_clients_lock.release()
    return JSONResponse({"ok": True, "queued": queued})


@router.get("/plugin/{plugin_id}/ui/{file_path:path}")
async def plugin_ui_file(plugin_id: str, file_path: str):
    """Get plugin UI static files."""
    if not file_path:
        # Redirect to index.
        return await plugin_ui_index(plugin_id)
    
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    # Resolve file path.
    target_file = (static_dir / file_path).resolve()
    
    # Security check: ensure the file is within the static directory.
    try:
        target_file.relative_to(static_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access denied: path traversal detected"
        )
    
    if not target_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path}"
        )
    
    if not target_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Not a file: {file_path}"
        )
    
    mime_type = _get_mime_type(target_file)
    
    # Get cache-control configuration.
    try:
        ui_config = await _get_static_ui_config(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    cache_control = "public, max-age=3600"
    if ui_config is not None:
        cache_control_obj = ui_config.get("cache_control")
        if isinstance(cache_control_obj, str) and cache_control_obj:
            cache_control = cache_control_obj
    
    return FileResponse(
        str(target_file),
        media_type=mime_type,
        headers={
            "Cache-Control": cache_control,
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui-info")
async def plugin_ui_info(plugin_id: str):
    """Get plugin UI information.
    
    Return whether the plugin has a UI, its entry path, etc.
    """
    try:
        ui_info = await plugin_ui_query_service.get_ui_info(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(ui_info)


@router.get("/plugin/{plugin_id}/surfaces")
async def plugin_ui_surfaces(plugin_id: str, locale: str | None = None):
    """Get the plugin's unified UI Surface list.

    LEGACY_STATIC_UI_COMPAT:
    Existing static UI is normalized as a mode=\"static\" panel surface.
    """
    try:
        surfaces = await plugin_ui_query_service.get_surfaces(plugin_id, locale=locale)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(surfaces)


@router.get("/plugin/{plugin_id}/hosted-ui/source")
async def plugin_hosted_ui_source(
    plugin_id: str,
    kind: str = "panel",
    id: str = "main",
    locale: str | None = None,
):
    """Read hosted surface source.

    Read-only source MVP for hosted-tsx / markdown. `locale` lets markdown
    tutorials select the sibling `<entry>.<locale>.md` for the current UI language, falling back
    to the default entry without a locale suffix when no sibling matches.
    """
    try:
        source = await plugin_ui_query_service.get_surface_source(
            plugin_id,
            kind=kind,
            surface_id=id,
            locale=locale,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(source)


@router.get("/plugin/{plugin_id}/hosted-ui/context")
async def plugin_hosted_ui_context(plugin_id: str, kind: str = "panel", id: str = "main", locale: str | None = None):
    """Get read-only hosted surface context."""
    try:
        context = await plugin_ui_query_service.get_surface_context(
            plugin_id,
            kind=kind,
            surface_id=id,
            locale=locale,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(context)


@mutation_router.post("/plugin/{plugin_id}/hosted-ui/action/{action_id}")
async def plugin_hosted_ui_action(
    plugin_id: str,
    action_id: str,
    http_request: Request,
    request: HostedUiActionRequest,
):
    """Execute a hosted surface action; version one reuses this plugin's plugin_entry."""
    try:
        result = await _await_action_or_disconnect(
            http_request,
            plugin_ui_query_service.call_surface_action(
                plugin_id,
                action_id=action_id,
                args=request.args,
                kind=request.kind,
                surface_id=request.surface_id,
                locale=request.locale,
            ),
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(result)


class ChatCardActionRequest(BaseModel):
    card_id: str = Field(min_length=1)
    target_lanlan: str = Field(min_length=1)
    args: dict[str, object] = Field(default_factory=dict)
    locale: str | None = None
    presentation: Literal["chat", "agent"] = "chat"


@mutation_router.post("/plugin/{plugin_id}/chat-card/action/{action_id}")
async def plugin_chat_card_action(
    plugin_id: str, action_id: str, http_request: Request, request: ChatCardActionRequest,
):
    """Trusted plugin HTML buttons call existing @ui.action entries without a panel."""
    card_context = {"card_id": request.card_id, "lanlan_name": request.target_lanlan}
    if request.presentation == "agent":
        card_context["view_id"] = request.card_id
    try:
        result = await _await_action_or_disconnect(
            http_request,
            plugin_ui_query_service.call_surface_action(
                plugin_id, action_id=action_id, args=request.args,
                kind="plugin_view" if request.presentation == "agent" else "chat_card",
                surface_id=request.card_id, locale=request.locale,
                _card_context=card_context,
            ),
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(result)


router.include_router(mutation_router)
