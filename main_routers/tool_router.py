# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import ipaddress
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
from main_routers.cookies_login_router import verify_local_access
from utils.logger_config import get_module_logger

from .shared_state import get_session_manager


def _validate_local_callback_url(url: str) -> str:
    """callback_url host whitelist validation: it may only point at local loopback.

    ``verify_local_access`` only governs who may call /api/tools/register; it
    says nothing about the ``callback_url`` value. Without host validation, a
    local caller could register a callback_url pointing at the public internet
    / LAN, turning main_server into an SSRF egress proxy that ships LLM
    tool-call payloads (including user conversation content and
    model-generated args) off-box.

    The host is forced to be loopback (``127.0.0.0/8`` IPv4, ``::1`` IPv6, or
    the literal ``localhost``). All current plugin models are local processes;
    there is no legitimate cross-machine use case. Cross-machine setups should
    go through a dedicated reverse proxy + an explicit authorization flow.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"callback_url scheme 必须是 http/https，实际：{parsed.scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise ValueError("callback_url 缺少 host")
    host = host.strip("[]")  # IPv6 字面量
    # 直接比对 localhost 字面量
    if host.lower() == "localhost":
        return url
    # 解析为 IP 后用 ipaddress 模块判断是否 loopback —— 同时正确处理
    # IPv4 / IPv6 / IPv4-mapped IPv6（::ffff:127.0.0.1）等情况。
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            f"callback_url host 必须是 loopback 地址（127.0.0.0/8、::1、"
            f"localhost），实际是非 IP 域名：{host!r}"
        ) from None
    # is_loopback 对 IPv4-mapped IPv6 不穿透映射的行为 CPython 3.11.11
    # 才修（gh-117566 backport，::ffff:127.0.0.1 在此前版本返回 False）。
    # 项目允许 ==3.11.* 且不钉 patch（Debian 12 系统 Python 是 3.11.2），
    # 需手动解包 ipv4_mapped 再判一次。
    mapped = getattr(ip, "ipv4_mapped", None)
    if not (ip.is_loopback or (mapped is not None and mapped.is_loopback)):
        raise ValueError(
            f"callback_url host 必须是 loopback 地址，实际：{host!r}"
        )
    return url

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

    @field_validator("callback_url")
    @classmethod
    def _check_callback_url_is_local(cls, v: str) -> str:
        return _validate_local_callback_url(v)


class ToolUnregisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: Optional[str] = None  # None = remove from all roles


class ToolClearRequest(BaseModel):
    source: str = Field(..., min_length=1)
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Remote dispatcher — issued when ToolRegistry.execute() runs a remote tool
# ---------------------------------------------------------------------------

# 死插件自动驱逐：插件进程崩了之后，main_server 的 registry 里还挂着指向
# 死端点的工具，model 还能在 schema 里看到它们并调用，每次都会撞 connection
# refused。优雅 shutdown 走 /api/tools/clear，崩溃（kill -9）没机会触发，
# 所以这里在 dispatch 路径上做反应式清理。
#
# 按 ``(source, callback_origin)`` 而不是单按 ``source`` 聚合失败计数——
# ``/api/tools/register`` 允许同一 plugin source 下每个工具有不同 callback_url，
# 单按 source 累计会把"一个端点死了"误升级成"整个 plugin 全死"，扫掉同 source
# 其他健康端点的工具。按 (source, origin) 双维聚合后，单端点不可达只清掉
# 该端点的工具，sibling endpoints 不受影响。
# （Codex review on PR #1382 提出的 endpoint-local outage 风险。）
#
# 只算"端点不可达"——ReadTimeout（插件慢）、HTTP 4xx/5xx（插件活着但有 bug）、
# body 解析失败、callback 业务上回 ``is_error=True``，这些都是工具/插件 bug，
# 不是 lifecycle 问题，不计入也不会触发驱逐。任何一次 HTTP 交换成功（不管
# 业务结果）就重置该 (source, origin) 的计数器，所以"偶发 connection refused"
# 不会在长期里累积成误杀。
_EVICTION_FAILURE_THRESHOLD = 3
_consecutive_connect_failures: Dict[Tuple[str, str], int] = {}


def _callback_origin(url: str) -> str:
    """Normalize ``callback_url`` to ``scheme://host:port`` as the eviction bucket key.
    If it cannot be parsed or the port is invalid (malformed URLs like
    ``http://127.0.0.1:abc/cb`` make ``ParseResult.port`` raise ``ValueError`` —
    the loopback validator does not police port syntax), fall back to the raw
    string, which guarantees:
    - the counter key never raises on weird input, and the dispatch path still
      returns a structured ToolResult
    - the same malformed URL always maps to the same key (eviction counting
      still accumulates instead of degenerating into key collisions from
      re-parsing and failing every time)
    (Codex review on PR #1382: malformed callback URLs.)"""
    if not url:
        return "<unknown>"
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return url
        port = parsed.port  # 可能抛 ValueError（非数字端口）
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{parsed.hostname}:{port}"
    except (ValueError, TypeError):
        return url


def _is_plugin_source(source: str) -> bool:
    """Only sources of the ``plugin:<id>`` form participate in auto-eviction. builtin
    is always exempt; other custom sources (e.g. agent_server, external) also do
    not participate for now — their lifecycle does not necessarily follow the
    plugin process model, and their revival mechanism differs."""
    return bool(source) and source.startswith("plugin:")


def _is_connection_level_failure(exc: BaseException) -> bool:
    """Whether this counts as "plugin endpoint unreachable". Only ``ConnectError`` /
    ``ConnectTimeout`` qualify — a ``ReadTimeout`` may just be a slow tool, and
    HTTP 5xx means the plugin is alive but buggy; neither is a lifecycle failure."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _note_dispatch_outcome(source: str, callback_url: str, *, connection_failed: bool) -> None:
    """Update the consecutive connection-failure counter for a ``(source,
    callback_origin)``. A single success (any HTTP status) resets it to zero;
    hitting the threshold consecutively triggers ``_evict_dead_callback_origin``."""
    if not _is_plugin_source(source):
        return
    key = (source, _callback_origin(callback_url))
    if not connection_failed:
        _consecutive_connect_failures.pop(key, None)
        return
    cnt = _consecutive_connect_failures.get(key, 0) + 1
    _consecutive_connect_failures[key] = cnt
    if cnt >= _EVICTION_FAILURE_THRESHOLD:
        _evict_dead_callback_origin(source, key[1])


def _evict_dead_callback_origin(source: str, origin: str) -> None:
    """Sweep the tools of the given ``(source, origin)`` out of every session
    manager's registry, and trigger ``_sync_tools_to_active_session`` to refresh
    the live OpenAI Realtime / GLM / Qwen schemas on the wire — touching only
    the registry without pushing to the wire would leave the model seeing the
    old schema until the session restarts.

    Only tools matching the origin are swept: sibling tools of the same source
    on other origins are kept. This covers the common case of a whole plugin
    process crashing (one plugin usually runs one server, so all its tools share
    one callback_url origin → swept together) while avoiding collateral damage
    to other healthy endpoints when a single endpoint is misconfigured."""
    _consecutive_connect_failures.pop((source, origin), None)
    try:
        session_manager = get_session_manager()
    except Exception as e:
        # session_manager 未初始化（极早期 dispatch / 单测裸调用）。
        # 静默 return —— 没有 manager 就没法 sweep，下次再试。
        logger.debug(
            "auto-eviction skipped (session_manager unavailable): %s: %s",
            type(e).__name__, e,
        )
        return
    total = 0
    affected: List[str] = []
    for mgr in list(session_manager.values()):
        if mgr is None:
            continue
        try:
            to_drop = [
                t.name for t in mgr.tool_registry.all()
                if t.metadata.get("source") == source
                and _callback_origin(t.metadata.get("callback_url") or "") == origin
            ]
            if not to_drop:
                continue
            for name in to_drop:
                mgr.tool_registry.unregister(name)
            # 复用 mgr 已有的 fire-and-forget sync 通道（与 register_tool /
            # clear_tools 同一条路径），把 fresh session.update 推到 wire。
            # 直接访问 ``_fire_task`` / ``_sync_tools_to_active_session`` 是
            # 因为没有"按谓词过滤"的公共 API；新加一个只服务于本驱逐通道
            # 的方法属于过度抽象。
            mgr._fire_task(mgr._sync_tools_to_active_session())  # noqa: SLF001
        except Exception as e:
            logger.warning(
                "auto-eviction sweep on mgr=%s (source=%s origin=%s) failed: %s: %s",
                getattr(mgr, "lanlan_name", "?"), source, origin,
                type(e).__name__, e,
            )
            continue
        total += len(to_drop)
        affected.append(getattr(mgr, "lanlan_name", "?"))
    if total:
        logger.warning(
            "auto-evicted %d tool(s) for plugin source %s callback origin %s "
            "across roles=%s after %d consecutive connect failures — endpoint "
            "unreachable (plugin process or sub-endpoint likely down)",
            total, source, origin, affected, _EVICTION_FAILURE_THRESHOLD,
        )


async def _remote_dispatch(call: ToolCall, metadata: Dict[str, Any]) -> ToolResult:
    """POST the tool call to the plugin's callback URL and translate the
    JSON response into a ``ToolResult``. The plugin contract is::

        request body  → {"name": "...", "arguments": {...}, "call_id": "..."}
        response body → {"output": <any JSON>, "is_error": false}
                     or {"error": "...", "is_error": true}

    Also runs the dead-plugin auto-eviction tracker on every outcome:
    consecutive connection-level failures for a ``plugin:*`` source cross
    the threshold → the source's tools get swept from every session
    manager's registry. See ``_note_dispatch_outcome`` for details.
    """
    source = str(metadata.get("source") or "")
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
        _note_dispatch_outcome(
            source, str(callback_url or ""),
            connection_failed=_is_connection_level_failure(e),
        )
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"error": err}, is_error=True, error_message=err,
        )
    # HTTP exchange completed (any status code) → endpoint is reachable,
    # reset the consecutive-failure counter. Application-level errors
    # (4xx/5xx or ``is_error=True`` in body) are NOT lifecycle failures.
    _note_dispatch_outcome(source, str(callback_url or ""), connection_failed=False)
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
