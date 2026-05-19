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
    """callback_url host 白名单校验：只能指向本机 loopback。

    ``verify_local_access`` 只管"谁能调 /api/tools/register"，不管
    ``callback_url`` 的值。如果不校验 host，本地 caller 可以注册一个
    指向公网/局域网的 callback_url，把 main_server 当 SSRF 出站代理对
    外发 LLM 工具调用 payload（含用户对话内容、模型生成的 args）。

    强制 host 必须是 loopback（``127.0.0.0/8`` IPv4、``::1`` IPv6 或
    字面量 ``localhost``）。当前 plugin 模型全是本机进程，没有跨机
    合法用例。需要跨机的请走独立的反向代理 + 显式授权流程。
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
    if not ip.is_loopback:
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
    """把 ``callback_url`` 归一为 ``scheme://host:port`` 作为驱逐 bucket key。
    parse 不出来或 port 不合法（``http://127.0.0.1:abc/cb`` 之类的畸形 URL
    会让 ``ParseResult.port`` 抛 ``ValueError``——loopback validator 没管端口
    语法）就回退到原字符串，保证：
    - counter key 在异常输入下不抛，dispatch 路径仍然能返回结构化 ToolResult
    - 同一畸形 URL 始终映射到同一 key（驱逐计数仍然能累加，不会因为
      每次重新尝试 parse 又失败而 key collision 退化）
    （Codex review on PR #1382: malformed callback URLs.）"""
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
    """只有 ``plugin:<id>`` 形态的 source 会参与自动驱逐。builtin 永远豁免；
    其它自定义 source（如 agent_server、external）现阶段也不参与——它们的
    生命周期不一定走 plugin 进程模型，复活机制也不一样。"""
    return bool(source) and source.startswith("plugin:")


def _is_connection_level_failure(exc: BaseException) -> bool:
    """是否属于"插件端点不可达"。只认 ``ConnectError`` / ``ConnectTimeout``——
    ``ReadTimeout`` 可能只是工具慢，HTTP 5xx 是插件活着但 bug，都不算
    lifecycle 失败。"""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _note_dispatch_outcome(source: str, callback_url: str, *, connection_failed: bool) -> None:
    """更新某 ``(source, callback_origin)`` 的连续连接失败计数。成功一次
    （任何 HTTP status）就清零；连续达到阈值则触发
    ``_evict_dead_callback_origin``。"""
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
    """把指定 ``(source, origin)`` 的工具从每个 session manager 的 registry
    里扫掉，并触发 ``_sync_tools_to_active_session`` 把 wire 上活跃的 OpenAI
    Realtime / GLM / Qwen schema 刷新——只动 registry 不推 wire 的话，模型
    还是会看到旧 schema 直到 session 重启。

    只扫匹配 origin 的工具：同 source 下其它 origin 的 sibling 工具保留。
    覆盖 plugin 进程整体崩溃的常见 case（同 plugin 通常一个 server，所有
    tool 的 callback_url 同 origin → 一起扫），也避免单端点配错时误伤
    其它健康端点。"""
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
