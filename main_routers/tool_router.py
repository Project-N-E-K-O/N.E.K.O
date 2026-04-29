# -*- coding: utf-8 -*-
"""Tool calling router.

Cross-process API for plugins / agent_server / external services to
register and unregister model-callable tools at runtime. The actual
execution path: model emits a tool call → ``OmniOfflineClient`` /
``OmniRealtimeClient`` hands it to ``LLMSessionManager._on_tool_call`` →
``ToolRegistry.execute`` → either local callable or HTTP POST to the
plugin's callback URL.

Roles
-----
The harness runs one ``LLMSessionManager`` per character (the
``session_manager`` dict is keyed by character name). Tools can be
registered globally (apply to every role) or scoped to a single role.

Endpoints
---------
``POST /api/tools/register``
    Register a remote tool. Body schema::

        {
          "name": "get_weather",
          "description": "Get weather for a location.",
          "parameters": { "type": "object", "properties": {...}, "required": [...] },
          "callback_url": "http://127.0.0.1:9333/plugins/foo/tools/get_weather",
          "role": null,                  // null = global (all roles)
          "source": "plugin:foo",        // free-form tag, used for clear()
          "timeout_seconds": 30
        }

``POST /api/tools/unregister``
    Body: ``{"name": "...", "role": null}`` — drops the tool. Returns
    ``{"removed": bool}``.

``POST /api/tools/clear``
    Body: ``{"source": "plugin:foo", "role": null}`` — drops every tool
    whose ``metadata.source == source``. Useful for plugin shutdown.

``GET /api/tools``
    Optional ``?role=Lanlan`` query — returns the active tool list.

The HTTP dispatcher does NOT proxy in-process tools — those are
registered directly via ``LLMSessionManager.register_tool``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
from main_routers.cookies_login_router import verify_local_access
from utils.logger_config import get_module_logger

from .shared_state import get_session_manager

# 这些端点能改运行时状态（注册/卸载工具、配置 callback_url），如果服务被
# 暴露到 LAN 上不加保护就成了任意远程工具转发器。复用 cookies_login_router
# 里已有的 verify_local_access：仅允许 127.0.0.1 / ::1 / localhost，本地之外
# 的请求一律 403。
router = APIRouter(
    prefix="/api/tools",
    tags=["tools"],
    dependencies=[Depends(verify_local_access)],
)
logger = get_module_logger(__name__, "Main")

# Shared HTTP client for plugin callbacks. Created lazily so we don't
# pay for the connection pool when no remote tools are registered.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _HTTP_CLIENT


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class ToolRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    callback_url: str = Field(..., min_length=1)
    role: Optional[str] = None  # None = global
    source: str = "external"
    # 上下界保护：误填超大值会让单次工具调用阻塞整条 tool-call 路径，
    # 模型轮也会被卡住；超过 5 分钟的同步工具应该改成 plugin 自己拆任务
    # 而不是把 main_server 长期 hold 住。
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)


class ToolUnregisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: Optional[str] = None  # None = remove from all roles


class ToolClearRequest(BaseModel):
    source: str = Field(..., min_length=1)
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Remote dispatcher — issued when ToolRegistry.execute() runs a remote tool
# ---------------------------------------------------------------------------


async def _remote_dispatch(call: ToolCall, metadata: Dict[str, Any]) -> ToolResult:
    """POST the tool call to the plugin's callback URL and translate the
    JSON response into a ``ToolResult``. The plugin contract is::

        request body  → {"name": "...", "arguments": {...}, "call_id": "..."}
        response body → {"output": <any JSON>, "is_error": false}
                     or {"error": "...", "is_error": true}
    """
    callback_url = metadata.get("callback_url")
    if not callback_url:
        msg = "remote tool registered without callback_url"
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"error": msg}, is_error=True, error_message=msg,
        )
    timeout = float(metadata.get("timeout_seconds") or 30.0)
    payload = {
        "name": call.name,
        "arguments": call.arguments,
        "call_id": call.call_id,
        "raw_arguments": call.raw_arguments,
    }
    try:
        client = _get_http_client()
        resp = await client.post(callback_url, json=payload, timeout=timeout)
    except Exception as e:
        err = f"remote tool callback HTTP failure: {type(e).__name__}: {e}"
        logger.warning("remote tool '%s' dispatch failed: %s", call.name, err)
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"error": err}, is_error=True, error_message=err,
        )
    if resp.status_code >= 400:
        err = f"remote tool callback returned HTTP {resp.status_code}: {resp.text[:200]}"
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"error": err}, is_error=True, error_message=err,
        )
    try:
        body = resp.json()
    except Exception:
        body = {"output": resp.text}
    if not isinstance(body, dict):
        body = {"output": body}
    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        output=body.get("output", body),
        is_error=bool(body.get("is_error", False)),
        error_message=str(body.get("error") or "") if body.get("is_error") else "",
    )


def _ensure_dispatcher_bound(role_keys) -> None:
    """Ensure every (or one) ``LLMSessionManager`` has the HTTP remote
    dispatcher wired up. Idempotent — safe to call on every register."""
    session_manager = get_session_manager()
    keys = role_keys or list(session_manager.keys())
    for key in keys:
        mgr = session_manager.get(key)
        if mgr is None:
            continue
        registry = getattr(mgr, "tool_registry", None)
        if registry is None:
            continue
        # ``_remote_dispatcher`` is private but stable within this module
        # and main_logic.tool_calling — both ours.
        if registry._remote_dispatcher is None:  # noqa: SLF001
            registry._remote_dispatcher = _remote_dispatch  # noqa: SLF001


def _resolve_target_managers(role: Optional[str]) -> List[Any]:
    session_manager = get_session_manager()
    if role:
        mgr = session_manager.get(role)
        if mgr is None:
            raise HTTPException(status_code=404, detail=f"unknown role: {role}")
        return [mgr]
    return [m for m in session_manager.values() if m is not None]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register")
async def register_tool(req: ToolRegisterRequest) -> Dict[str, Any]:
    targets = _resolve_target_managers(req.role)
    _ensure_dispatcher_bound([req.role] if req.role else None)

    tool = ToolDefinition(
        name=req.name,
        description=req.description,
        parameters=req.parameters,
        handler=None,  # remote — dispatched via _remote_dispatch
        metadata={
            "source": req.source,
            "callback_url": req.callback_url,
            "timeout_seconds": req.timeout_seconds,
            "role": req.role,
        },
    )
    affected: List[str] = []
    failed: List[Dict[str, str]] = []
    for mgr in targets:
        role_name = getattr(mgr, "lanlan_name", "?")
        try:
            # 用 _and_sync 版本：注册后等 session.update 推送完成再返回，
            # 这样调用方拿到 ok=True 的瞬间，active/pending session 上的
            # tools 已经是最新 —— 不会出现"返回成功但下一次 model 调用
            # 还看不到工具"的窗口。
            await mgr.register_tool_and_sync(tool, replace=True)
            affected.append(role_name)
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            logger.warning("register_tool to %s failed: %s", role_name, err_text)
            failed.append({"role": role_name, "error": err_text})
    # 全失败 → ok=False，让插件知道注册没生效（之前永远 ok=True 会让插件
    # 误以为工具已经可用，下次 model 调用工具才会运行时报错）。
    # 部分成功 → ok=True 但带 failed_roles，让调用方按需处理（比如重试该 role）。
    if not affected:
        return {
            "ok": False,
            "registered": req.name,
            "affected_roles": [],
            "failed_roles": failed,
            "error": "no role accepted the registration",
        }
    return {
        "ok": True,
        "registered": req.name,
        "affected_roles": affected,
        "failed_roles": failed,
    }


@router.post("/unregister")
async def unregister_tool(req: ToolUnregisterRequest) -> Dict[str, Any]:
    targets = _resolve_target_managers(req.role)
    removed_any = False
    affected: List[str] = []
    failed: List[Dict[str, str]] = []
    for mgr in targets:
        role_name = getattr(mgr, "lanlan_name", "?")
        try:
            # _and_sync 版本：等 session 同步完成再返回，与 register 端点对偶。
            if await mgr.unregister_tool_and_sync(req.name):
                removed_any = True
                affected.append(role_name)
        except Exception as e:
            # 单角色 sync 失败不能让整个跨角色请求 500 —— 调用方需要拿到
            # 已成功的 role 列表来推断状态。
            err_text = f"{type(e).__name__}: {e}"
            logger.warning("unregister_tool on %s failed: %s", role_name, err_text)
            failed.append({"role": role_name, "error": err_text})
    return {
        "ok": not failed or removed_any,
        "removed": removed_any,
        "name": req.name,
        "affected_roles": affected,
        "failed_roles": failed,
    }


@router.post("/clear")
async def clear_tools(req: ToolClearRequest) -> Dict[str, Any]:
    targets = _resolve_target_managers(req.role)
    total = 0
    affected: List[str] = []
    failed: List[Dict[str, str]] = []
    for mgr in targets:
        role_name = getattr(mgr, "lanlan_name", "?")
        try:
            n = await mgr.clear_tools_and_sync(source=req.source)
            total += n
            if n > 0:
                affected.append(role_name)
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            logger.warning("clear_tools on %s failed: %s", role_name, err_text)
            failed.append({"role": role_name, "error": err_text})
    return {
        "ok": not failed or total > 0,
        "removed": total,
        "source": req.source,
        "affected_roles": affected,
        "failed_roles": failed,
    }


@router.get("")
async def list_tools(role: Optional[str] = Query(None)) -> Dict[str, Any]:
    targets = _resolve_target_managers(role)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for mgr in targets:
        rname = getattr(mgr, "lanlan_name", "?")
        registry = getattr(mgr, "tool_registry", None)
        if registry is None:
            out[rname] = []
            continue
        out[rname] = [
            {
                "name": t.name,
                "description": t.description,
                "source": t.metadata.get("source", ""),
                "callback_url": t.metadata.get("callback_url"),
                "is_remote": t.handler is None,
            }
            for t in registry.all()
        ]
    return {"ok": True, "tools_by_role": out}
