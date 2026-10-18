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
    Body: ``{"name": "...", "role": null, "expected_source": "plugin:foo"}`` —
    drops the tool. ``expected_source`` is optional; when set, roles whose
    tool is owned by a different source are NOT removed and reported in
    ``refused_roles`` (a plugin can never delete another plugin's tool by
    name collision). Returns ``{"removed": bool, "refused_roles": [...]}``.

``POST /api/tools/clear``
    Body: ``{"source": "plugin:foo", "role": null}`` — drops every tool
    whose ``metadata.source == source``. Useful for plugin shutdown.

``GET /api/tools``
    Optional ``?role=Lanlan`` query — returns the active tool list.

The HTTP dispatcher does NOT proxy in-process tools — those are
registered directly via ``LLMSessionManager.register_tool``.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (API URL conventions) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import asyncio
import ipaddress
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from main_logic.tool_calling import (
    ToolCall,
    ToolDefinition,
    ToolResult,
    looks_like_tool_envelope,
    tool_result_from_envelope,
)
from main_routers.cookies_login_router import verify_local_access
from utils.logger_config import get_module_logger

from .shared_state import get_session_manager
from main_logic.reply_tail import reply_tail_registry


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
    host = host.strip("[]")  # IPv6 literal.
    # Compare literal localhost directly.
    if host.lower() == "localhost":
        return url
    # Parse IP then use ipaddress loopback checks, correctly handling
    # IPv4 / IPv6 / IPv4-mapped IPv6 (::ffff:127.0.0.1).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            f"callback_url host 必须是 loopback 地址（127.0.0.0/8、::1、"
            f"localhost），实际是非 IP 域名：{host!r}"
        ) from None
    # is_loopback following IPv4-mapped IPv6 was fixed only in CPython 3.11.11
    # (gh-117566 backport; earlier ::ffff:127.0.0.1 returns False).
    # Project allows ==3.11.* without patch pin (Debian 12 uses 3.11.2),
    # requiring manual ipv4_mapped unwrapping and recheck.
    mapped = getattr(ip, "ipv4_mapped", None)
    if not (ip.is_loopback or (mapped is not None and mapped.is_loopback)):
        raise ValueError(
            f"callback_url host 必须是 loopback 地址，实际：{host!r}"
        )
    return url

# These endpoints mutate runtime (register/unregister tools, configure callback_url); exposed
# unprotected on LAN they'd become arbitrary remote tool forwarders. Reuse cookies_login_router's
# verify_local_access: localhost/loopback only; all other
# requests 403; mapped IPv6 checked by mapped IPv4 address.
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
    # Bound values: huge timeouts block entire tool-call path and model turns;
    # synchronous tools longer than five minutes should be split into plugin tasks,
    # never hold main_server long-term.
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    reply_tail: bool = False

    @field_validator("callback_url")
    @classmethod
    def _check_callback_url_is_local(cls, v: str) -> str:
        return _validate_local_callback_url(v)


class ToolUnregisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: Optional[str] = None  # None = remove from all roles
    # If set, verify current name's owning source; refuse mismatched roles, reporting
    # refused_roles. Prevent plugin self-unregistration deleting same-name tools owned
    # by others (e.g. its registration never succeeded and another plugin owns name).
    expected_source: Optional[str] = None


class ToolClearRequest(BaseModel):
    source: str = Field(..., min_length=1)
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Remote dispatcher — issued when ToolRegistry.execute() runs a remote tool
# ---------------------------------------------------------------------------

# Evict dead plugins automatically: crashes leave tools pointing at dead endpoints in
# main_server registry, visible/callable in model schemas, always connection
# refused. Graceful shutdown uses /api/tools/clear; kill -9 cannot,
# so clean reactively during dispatch.
#
# Count failures by ``(source, callback_origin)``, never source alone:
# ``/api/tools/register`` permits different callback_url per tool under one plugin source;
# source-only counting turns one endpoint outage into whole-plugin outage, removing
# healthy sibling endpoint tools. (source, origin) confines cleanup to tools on
# failed endpoint, leaving siblings unaffected.
# (Endpoint-local outage risk from Codex review PR #1382.)
#
# Count unreachable endpoints only: ReadTimeout (slow plugin), HTTP 4xx/5xx (live but buggy),
# body parsing errors, and callback ``is_error=True`` are tool/plugin bugs,
# not lifecycle issues, never count or evict. Any successful HTTP exchange regardless
# of business result resets (source, origin), preventing occasional connection refused
# accumulating into false eviction over time.
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
        port = parsed.port  # May raise ValueError for nonnumeric ports.
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
    # Sweep ledger too, or character rebuild replays dead tools. Before obtaining
    # session_manager, so cleanup works even when manager unavailable.
    _ledger_forget(
        lambda entry: entry.tool.metadata.get("source") == source
        and _callback_origin(entry.tool.metadata.get("callback_url") or "") == origin,
        None,
    )
    try:
        session_manager = get_session_manager()
    except Exception as e:
        # session_manager uninitialized (very early dispatch / direct unit calls).
        # Return silently; no manager means no sweep, retry next time.
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
            holder_of = getattr(mgr, "_public_knowledge_holder", None)
            holder = holder_of() if callable(holder_of) else None
            for name in to_drop:
                mgr.tool_registry.unregister(name)
            # Evicted tools may shadow builtin query_public_knowledge;
            # restore builtin after name becomes free.
            refill = getattr(mgr, "_refill_vacated_builtins", None)
            if callable(refill):
                refill(holder)
            # Reuse manager fire-and-forget sync channel (same as register_tool /
            # clear_tools), sending fresh session.update onto wire.
            # Access ``_fire_task`` / ``_sync_tools_to_active_session`` directly because
            # no public predicate-filter API exists; adding one solely
            # for eviction overabstracts.
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
    if call.host_reply is not None:
        payload["host_reply"] = call.host_reply
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
    if not looks_like_tool_envelope(body):
        # Ordinary business dictionary. Former body.get("output", body) passed unchanged;
        # tool_result_from_envelope would dismantle search-tool
        # {"images": [...urls...]} as pixel envelope, removing images from model-visible output
        # then turning invalid base64 into warning strings. Plugin callback path (llm_tools.py)
        # already distinguishes this; missing counterpart here.
        body = {"output": body}
    return await asyncio.to_thread(tool_result_from_envelope, call, body)


class ReplyTailRequest(BaseModel):
    context: Dict[str, Any]
    plugin_id: str = Field(min_length=1, max_length=128)
    # Validated by the registry, which answers with a receipt rather than a
    # 422 the SDK would raise as an HTTP error.
    registration_id: Any = ""
    parts: Any = Field(default_factory=list)
    ai_behavior: Any = "blind"


@router.post("/reply-tail/register")
async def register_reply_tail(body: ReplyTailRequest) -> dict:
    return await reply_tail_registry.register(
        body.context, body.registration_id, body.parts,
        plugin_id=body.plugin_id, ai_behavior=body.ai_behavior,
    )


@router.post("/reply-tail/status")
async def reply_tail_status(body: ReplyTailRequest) -> dict:
    return reply_tail_registry.status(
        body.context, body.registration_id, plugin_id=body.plugin_id,
    )


@router.post("/reply-tail/cancel")
async def cancel_reply_tail(body: ReplyTailRequest) -> dict:
    return reply_tail_registry.cancel(
        body.context, body.registration_id, plugin_id=body.plugin_id,
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
# Remote registration ledger — replayed into rebuilt session managers
# ---------------------------------------------------------------------------

# Remote tools live only in each character's ``LLMSessionManager`` ToolRegistry. Character
# reload (API save/voice_id change) without active session rebuilds whole manager
# (app/main_server/character_runtime.py), whose fresh registry contains builtins only.
# Plugins register once on process start, so rebuild silently loses tools from model schemas
# (2026-09-12: API save left minecraft_task absent from every session all night).
#
# Record each accepted registration here; ``replay_remote_tools`` restores on manager creation.
# Every mutation maintains ledger synchronously, matching each registry operation
# (one definition per name; register overwrites):
#   Global register → record (None, name), remove same-name scoped records already overwritten in registries.
#   Register X → record (X, name), global same-name record skips X, whose global copy was overwritten.
#                Reregistered names move to tail, preserving last-writer-wins replay.
#   unregister → role=None removes all name records; role=X removes (X,name), global record skips X.
#   clear → same as unregister, matched by source.
#   Dead-plugin eviction → remove all matching (source, callback origin).
#   Character slot delete/rename → ``forget_role``: manager/registry gone together.
# Process-local: main_server restart clears ledger, still requiring plugin reregistration.
# Known edge: all-target wire-sync failures still record (registries already written), but plugins receive
# ok=false and do not track; tools survive rebuilds until source clear succeeds.
@dataclass
class _LedgerEntry:
    tool: ToolDefinition
    role: Optional[str]
    excluded_roles: set = field(default_factory=set)


_remote_tool_ledger: Dict[Tuple[Optional[str], str], _LedgerEntry] = {}


def _ledger_record(tool: ToolDefinition, role: Optional[str]) -> None:
    """Record an accepted registration, applying the registries' overwrite rules.

    A registry holds one definition per name, so a global registration
    replaces the tool in every role's registry (scoped entries of that name
    are gone), and a scoped registration replaces the global copy in that
    one role's registry.
    """
    if role is None:
        for key in [k for k in _remote_tool_ledger if k[0] is not None and k[1] == tool.name]:
            del _remote_tool_ledger[key]
    else:
        shadowed_global = _remote_tool_ledger.get((None, tool.name))
        if shadowed_global is not None:
            shadowed_global.excluded_roles.add(role)
    key = (role, tool.name)
    _remote_tool_ledger.pop(key, None)
    _remote_tool_ledger[key] = _LedgerEntry(tool=tool, role=role)


def _ledger_forget(matches: Callable[[_LedgerEntry], bool], role: Optional[str]) -> None:
    """Drop the ledger entries selected by ``matches`` the way the registries drop them.

    With ``role`` None every matching entry goes. With a role, only that role
    loses the tool: its own scoped entries are deleted, and a matching global
    entry keeps applying to every other role but is skipped for this one.
    """
    for key, entry in list(_remote_tool_ledger.items()):
        if not matches(entry):
            continue
        if role is None or entry.role == role:
            del _remote_tool_ledger[key]
        elif entry.role is None:
            entry.excluded_roles.add(role)


def forget_role(role_name: str) -> None:
    """Drop every ledger trace of a character slot that no longer exists.

    Deleting or renaming a character destroys its manager and registry, so
    its scoped entries and its exclusions from global entries must not be
    inherited by a later, unrelated character that reuses the name.
    """
    for key, entry in list(_remote_tool_ledger.items()):
        if entry.role == role_name:
            del _remote_tool_ledger[key]
        else:
            entry.excluded_roles.discard(role_name)


def replay_remote_tools(mgr: Any, role_name: str) -> List[str]:
    """Re-register the ledgered remote tools that apply to ``role_name``.

    Meant for a freshly built ``LLMSessionManager`` that is not installed yet:
    it has no session to sync, so the registry is written directly and the
    first session snapshot picks the tools up. Returns the replayed names.
    """
    registry = getattr(mgr, "tool_registry", None)
    if registry is None:
        return []
    replayed: List[str] = []
    for entry in _remote_tool_ledger.values():
        if entry.role is None:
            if role_name in entry.excluded_roles:
                continue
        elif entry.role != role_name:
            continue
        registry.register(entry.tool, replace=True)
        replayed.append(entry.tool.name)
    replayed = list(dict.fromkeys(replayed))
    if replayed:
        if registry._remote_dispatcher is None:  # noqa: SLF001
            registry._remote_dispatcher = _remote_dispatch  # noqa: SLF001
        logger.info(
            "ToolRegistry replay for %s: restored %d remote tool(s): %s",
            role_name, len(replayed), replayed,
        )
    return replayed


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
            **({"reply_tail": True} if req.reply_tail else {}),
        },
    )
    # Reserve ownership before the first await, including managers rebuilt
    # during session sync. Refused registrations must not alter the ledger.
    conflicts = []
    for mgr in targets:
        existing = mgr.tool_registry.get(req.name)
        if existing is not None and existing.metadata.get("source", "") != req.source:
            conflicts.append({"role": getattr(mgr, "lanlan_name", "?"), "error": "cross-source overwrite refused"})
    for entry in _remote_tool_ledger.values():
        overlaps = req.role is None or entry.role == req.role or (
            entry.role is None and req.role not in entry.excluded_roles
        )
        if entry.tool.name == req.name and overlaps and entry.tool.metadata.get("source", "") != req.source:
            conflicts.append({"role": entry.role or "*", "error": "cross-source ledger overwrite refused"})
    if conflicts:
        logger.warning("register_tool refused cross-source overwrite: name='%s' incoming='%s'", req.name, req.source)
        return {"ok": False, "registered": req.name, "affected_roles": [], "failed_roles": conflicts}

    # Record ledger before registering targets: registry.register takes effect before wire sync, even if
    # sync fails. Managers rebuilt mid-loop are outside targets and rely on
    # ledger replay. With no managers, no registry changed, so record nothing.
    if targets:
        _ledger_record(tool, req.role)
    affected: List[str] = []
    failed: List[Dict[str, str]] = []
    for mgr in targets:
        role_name = getattr(mgr, "lanlan_name", "?")
        try:
            # Reject cross-source name collisions: name-keyed registry replace=True overwrites unconditionally;
            # without protection, later plugins silently displace existing same-name tools from other sources,
            # rerouting model calls to new callback and permitting original plugin to unregister another's tool.
            # Same-source refresh (restart callback_url / schema updates) unaffected.
            existing = mgr.tool_registry.get(req.name)
            if existing is not None:
                existing_source = str((getattr(existing, "metadata", None) or {}).get("source", ""))
                if existing_source != req.source:
                    logger.warning(
                        "register_tool refused cross-source overwrite on %s: name='%s' owned_by='%s' incoming='%s'",
                        role_name, req.name, existing_source or "unknown", req.source,
                    )
                    failed.append({
                        "role": role_name,
                        "error": (
                            f"tool '{req.name}' already owned by source "
                            f"'{existing_source or 'unknown'}' (cross-source overwrite refused)"
                        ),
                    })
                    continue
            # Use _and_sync: await session.update push after registration,
            # so when caller gets ok=True, active/pending session tools
            # are current, avoiding success responses followed by model calls
            # unable to see tools.
            await mgr.register_tool_and_sync(tool, replace=True)
            affected.append(role_name)
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            logger.warning("register_tool to %s failed: %s", role_name, err_text)
            failed.append({"role": role_name, "error": err_text})
    # All failed → ok=False informs plugins registration failed (former always-True
    # made plugins assume availability until next model tool call failed at runtime).
    # Partial success → ok=True plus failed_roles for caller handling, e.g. retry role.
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
    # After successful resolution, remove ledger before first await: 404 requests cannot mutate it, while managers
    # rebuilt mid-loop must not replay removed tools; no await separates these operations.
    _ledger_forget(
        lambda entry: entry.tool.name == req.name and (
            req.expected_source is None or entry.tool.metadata.get("source") == req.expected_source
        ), req.role,
    )
    removed_any = False
    affected: List[str] = []
    failed: List[Dict[str, str]] = []
    refused: List[Dict[str, str]] = []
    for mgr in targets:
        role_name = getattr(mgr, "lanlan_name", "?")
        try:
            # Ownership check: reject removal if expected_source differs from current source.
            # Symmetric with /register cross-source rejection: unregister only own tools.
            # getattr tool_registry supports duck-typed managers/test stubs;
            # managers without registries skip validation, retaining previous unregister behavior.
            registry = getattr(mgr, "tool_registry", None)
            existing = registry.get(req.name) if registry is not None else None
            if req.expected_source is not None and existing is not None:
                existing_source = str((getattr(existing, "metadata", None) or {}).get("source", ""))
                if existing_source != req.expected_source:
                    logger.warning(
                        "unregister_tool refused foreign-owned name on %s: name='%s' owned_by='%s' expected='%s'",
                        role_name, req.name, existing_source or "unknown", req.expected_source,
                    )
                    refused.append({
                        "role": role_name,
                        "owned_by": existing_source or "unknown",
                    })
                    continue
            # _and_sync awaits session synchronization before return, symmetric with register.
            if await mgr.unregister_tool_and_sync(req.name):
                removed_any = True
                affected.append(role_name)
        except Exception as e:
            # One role's sync failure must not 500 the whole cross-role request; callers need
            # successful-role lists to infer state.
            err_text = f"{type(e).__name__}: {e}"
            logger.warning("unregister_tool on %s failed: %s", role_name, err_text)
            failed.append({"role": role_name, "error": err_text})
    return {
        "ok": not failed or removed_any,
        "removed": removed_any,
        "name": req.name,
        "affected_roles": affected,
        "failed_roles": failed,
        "refused_roles": refused,
    }


@router.post("/clear")
async def clear_tools(req: ToolClearRequest) -> Dict[str, Any]:
    targets = _resolve_target_managers(req.role)
    # Symmetric unregister: resolve, remove ledger before first await, then clear managers.
    _ledger_forget(
        lambda entry: entry.tool.metadata.get("source") == req.source, req.role
    )
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
