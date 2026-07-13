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

"""Own per-character runtime state, sync tasks, and agent-event dispatch."""

import asyncio
import atexit
import sys
from dataclasses import dataclass
from typing import Any, Optional

from config import MONITOR_SERVER_PORT, USER_NOTIFICATION_ERROR_MAX_CHARS
from main_logic import core, cross_server
from main_logic.agent_event_bus import notify_analyze_ack
from utils.config_manager import get_reserved

from ._shared import runtime

_IS_MAIN_PROCESS = runtime.is_main_process
_config_manager = runtime.config_manager
logger = runtime.logger


class _SyncMessageQueue(asyncio.Queue):
    """``asyncio.Queue`` with sync ``put()`` aliased to ``put_nowait()``.

    ``sync_message_queue`` was historically a ``queue.Queue`` (thread-safe), with
    producers calling sync ``q.put(item)`` in 14+ places across core.py /
    system_router.py etc. After cross_server became an ``asyncio.Task`` on the main
    loop, message_queue switched to ``asyncio.Queue``. The native
    ``asyncio.Queue.put`` is a coroutine, so the old sync calls would become
    "un-awaited coroutines" — never enqueuing and raising RuntimeWarning.

    Overriding ``put`` as a sync alias of ``put_nowait`` keeps backward
    compatibility: every sync_message_queue is unbounded (no maxsize), so
    ``put_nowait`` can never raise for being full, making the replacement
    semantically equivalent.
    """

    def put(self, item):  # type: ignore[override]
        # 故意 sync override：原 asyncio.Queue.put 是 coroutine。
        self.put_nowait(item)


@dataclass
class RoleState:
    """Per-k runtime state container for a single catgirl.

    Merges what used to be 6 parallel module-global dicts (sync_message_queue /
    sync_shutdown_event / session_id / sync_process / websocket_locks /
    session_manager) into one record held uniformly by role_state[k], avoiding
    half-initialized states + scattered maintenance cost.
    See issue #857 / PR #855 review.

    Invariants:
    - sync_message_queue / websocket_lock are constructed once in
      _ensure_character_slots and **never replaced** afterwards. Especially
      websocket_lock — replacing it would leave coroutines already inside
      ``async with`` blocked on an orphaned old Lock; if any logic needs to
      rebuild role_state[k] wholesale, it must carry the old lock over as-is.
    - session_id / sync_task / session_manager start as None and are assigned
      later by websocket_router / _init_character_resources respectively.

    Legacy fields: ``sync_shutdown_event: ThreadEvent`` and ``sync_process:
    Thread`` are semantically gone since cross_server merged into the main event
    loop (no separate thread anymore). Lifecycle is now managed by ``sync_task:
    asyncio.Task``, with shutdown via ``task.cancel()``.

    However, ``main_routers/shared_state.py``'s ``_RoleStateFieldView`` still
    exposes dict-like views for ``sync_shutdown_event`` / ``sync_process``
    (the public router APIs ``get_sync_shutdown_event()`` /
    ``get_sync_process()``). The view's ``__getitem__`` uses
    ``getattr(rs, field)`` (no default) and would raise ``AttributeError`` if
    the field didn't exist. Keeping these two ``Optional[Any] = None``
    placeholder fields preserves the shim's "always-empty dict" semantics:
    ``__contains__`` sees None and returns False, ``__getitem__`` goes to
    ``raise KeyError``, and every caller gets a consistent empty state instead
    of a crash. The two fields are never assigned anymore; remove them once
    it's confirmed nothing external depends on them.
    """

    sync_message_queue: _SyncMessageQueue
    websocket_lock: asyncio.Lock
    session_id: Optional[str] = None
    sync_task: Optional[asyncio.Task] = None
    # 用 Any 而非 core.LLMSessionManager：避免 dataclass 运行时求值 annotation
    # 时踩到 forward-ref / 循环引用边界
    session_manager: Optional[Any] = None
    # 仅为 main_routers/shared_state.py 的 legacy field-view 提供占位；永远 None
    sync_shutdown_event: Optional[Any] = None
    sync_process: Optional[Any] = None


# 角色名 -> RoleState 的主存储；所有 per-k 同步资源都通过它访问
role_state: dict[str, RoleState] = {}


def _iter_sync_connector_tasks():
    """Iterate over all still-alive sync connector tasks (role_state is the source of truth)."""
    for name, rs in role_state.items():
        task = rs.sync_task
        if task is None:
            continue
        yield name, task


def _signal_sync_connectors_shutdown(*, log: bool = True) -> None:
    """Cancel all sync connector tasks. task.cancel() is synchronous, idempotent, and harmless
    after the loop is closed, so a second atexit invocation is safe."""
    if log:
        logger.info("正在关闭同步连接器 task...")
    for rs in role_state.values():
        try:
            task = rs.sync_task
            if task is not None and not task.done():
                task.cancel()
        except Exception as e:
            logger.debug(f"取消同步连接器 task 失败: {e}", exc_info=True)


async def join_sync_connector_tasks(timeout: float = 3.0) -> list[str]:
    """Await all sync connector tasks in parallel; return the role names that didn't finish within the timeout.

    Normally ``_signal_sync_connectors_shutdown`` has already cancelled them before
    this is called; here we just wait for each task to run its finally cleanup
    (closing ws/session/reader).
    """
    wait_timeout = max(0.0, float(timeout))
    targets = list(_iter_sync_connector_tasks())
    if not targets:
        return []

    async def _wait_one(name: str, task: asyncio.Task) -> str | None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
        except asyncio.TimeoutError:
            return name
        except asyncio.CancelledError:
            # task 正常 cancel 走完 finally 后会 raise CancelledError
            return None
        except Exception as e:
            logger.debug(f"同步连接器 task {name} 退出时抛异常: {e}", exc_info=True)
            return None
        return None

    results = await asyncio.gather(
        *(_wait_one(name, task) for name, task in targets),
        return_exceptions=False,
    )
    pending = [name for name in results if name]

    if pending:
        logger.warning(
            "以下同步连接器 task 未在 %.1fs 内退出: %s",
            wait_timeout,
            ", ".join(pending),
        )
    return pending


# 兼容别名：旧名 join_sync_connector_threads 在文件内有调用，先保留 alias 减小 diff
join_sync_connector_threads = join_sync_connector_tasks


def cleanup(*, log: bool = True):
    """Tell all sync connector tasks to stop. log=False suppresses duplicate logs when atexit fires a second time."""
    _signal_sync_connectors_shutdown(log=log)


def _reset_sync_connector_shutdown_events() -> None:
    """Now a no-op: the old version used ThreadEvent.clear() so the thread slot could be reused
    on the next start; in task mode there is no state to reset — a dead task is detected by
    ``_init_character_resources`` and simply restarted via ``asyncio.create_task``. The function
    name is kept to avoid touching the many call sites."""
    return


# 只在主进程中注册 cleanup 函数，防止子进程退出时执行清理
# log=False：on_shutdown 已经打印过 "正在清理资源..."，atexit 补一刀时不重复 log
if _IS_MAIN_PROCESS:
    atexit.register(cleanup, log=False)
# 角色数据全局变量（会在重载时更新）
master_name = None
her_name = None
master_basic_config = None
lanlan_basic_config = None
name_mapping = None
lanlan_prompt = None
time_store = None
setting_store = None
recent_log = None
catgirl_names = []


def _is_websocket_connected(ws) -> bool:
    """Check if a WebSocket is in CONNECTED state."""
    if not ws:
        return False
    if not hasattr(ws, "client_state"):
        return False
    try:
        return ws.client_state == ws.client_state.CONNECTED
    except Exception:
        return False


def _iter_session_managers():
    """Yield (name, session_manager) for every role with a live session_manager.

    Replaces the old ``session_manager.items()`` pattern after the per-k dicts
    were consolidated into ``role_state``.
    """
    for name, rs in role_state.items():
        if rs.session_manager is not None:
            yield name, rs.session_manager


def _get_session_manager(name):
    """Return ``role_state[name].session_manager`` or None — dict.get() equivalent."""
    if not name:
        return None
    rs = role_state.get(name)
    return rs.session_manager if rs is not None else None


try:
    from main_logic.topic.delivery import register_topic_session_manager_getter

    register_topic_session_manager_getter(_get_session_manager)
except Exception:
    logger.warning("Failed to register topic session manager getter", exc_info=True)


def _select_fallback_session_manager():
    """Return a single connected session manager as a safe fallback, if unambiguous."""
    connected = []
    for name, mgr in _iter_session_managers():
        ws = getattr(mgr, "websocket", None)
        if _is_websocket_connected(ws):
            connected.append((name, mgr))
    if len(connected) == 1:
        return connected[0]
    return None, None


async def _broadcast_to_all_connected(event_payload: dict) -> int:
    """Broadcast an event to all connected WebSocket sessions in parallel.
    Can fire multiple times per second (agent status); serial awaits would let one slow ws drag down the other sessions."""
    # Take a snapshot to avoid RuntimeError from concurrent dict mutation
    targets = [
        (name, getattr(mgr, "websocket", None))
        for name, mgr in list(_iter_session_managers())
        if mgr
    ]
    targets = [
        (n, ws)
        for n, ws in targets
        if _is_websocket_connected(ws) and hasattr(ws, "send_json")
    ]

    async def _send_one(name, ws):
        try:
            await ws.send_json(event_payload)
            return True
        except Exception as e:
            logger.debug("[EventBus] broadcast to %s failed: %s", name, e)
            return False

    results = await asyncio.gather(
        *(_send_one(n, ws) for n, ws in targets), return_exceptions=False
    )
    return sum(1 for r in results if r is True)


async def _handle_agent_event(event: dict):
    """Receive agent_server events over ZeroMQ and dispatch them to core/websocket."""
    try:
        event_type = event.get("event_type")
        lanlan = event.get("lanlan_name")

        if event_type == "analyze_ack":
            logger.info(
                "[EventBus] analyze_ack received on main: event_id=%s lanlan=%s",
                event.get("event_id"),
                lanlan,
            )
            notify_analyze_ack(str(event.get("event_id") or ""))
            return

        if event_type == "voice_bridge_result":
            event_id = str(event.get("event_id") or "")
            logger.debug(
                "[EventBus] ignored voice_bridge_result: event_id=%s", event_id
            )
            return

        # Agent status updates may be broadcast (lanlan_name omitted).
        if event_type == "agent_status_update":
            snapshot = event.get("snapshot", {})
            payload = {
                "type": "agent_status_update",
                "snapshot": snapshot,
                "lanlan_name": lanlan or "",
            }
            mgr_for_status = _get_session_manager(lanlan)
            if isinstance(snapshot, dict):
                flags = snapshot.get("flags")
                if isinstance(flags, dict):
                    flags_for_sync = dict(flags)
                    if isinstance(snapshot.get("analyzer_enabled"), bool):
                        flags_for_sync["agent_enabled"] = bool(
                            snapshot.get("analyzer_enabled")
                        )
                    if lanlan and mgr_for_status is not None:
                        try:
                            mgr_for_status.update_agent_flags(flags_for_sync)
                        except Exception as e:
                            logger.debug(
                                "[EventBus] agent_status_update flag sync failed: %s", e
                            )
                    elif not lanlan:
                        for _, mgr in _iter_session_managers():
                            try:
                                mgr.update_agent_flags(flags_for_sync)
                            except Exception as e:
                                logger.debug(
                                    "[EventBus] agent_status_update broadcast flag sync failed: %s",
                                    e,
                                )
            if lanlan and mgr_for_status is not None:
                mgr = mgr_for_status
                ws = getattr(mgr, "websocket", None) if mgr else None
                if _is_websocket_connected(ws):
                    try:
                        await ws.send_json(payload)
                    except Exception as e:
                        logger.debug(
                            "[EventBus] agent_status_update send failed: %s", e
                        )
            elif not lanlan:
                # Only a target-less update (lanlan_name omitted) fans out to all
                # sessions; a targeted update whose session manager is missing must
                # NOT broadcast, or one character's status leaks into other sessions.
                await _broadcast_to_all_connected(payload)
            else:
                logger.info(
                    "[EventBus] agent_status_update dropped: no session_manager for lanlan=%s",
                    lanlan,
                )
            return

        # 免费版 Agent 每日配额耗尽：全局提示（与角色无关），广播成 status toast
        # 到所有已连接会话。上游 config_manager 已节流（≤每 10 秒一次），这里不会刷屏。
        # 前端已就绪：AGENT_QUOTA_EXCEEDED 在 criticalErrorCodes 里，配 i18n 文案
        # （{{used}}/{{limit}}）走 showStatusToast。
        if event_type == "agent_quota_exceeded":
            import json as _json

            status_message = _json.dumps(
                {
                    "code": "AGENT_QUOTA_EXCEEDED",
                    "details": {
                        "used": event.get("used", 0),
                        "limit": event.get("limit", 300),
                    },
                }
            )
            quota_payload = {"type": "status", "message": status_message}
            mgr_for_quota = _get_session_manager(lanlan)
            if lanlan and mgr_for_quota is not None:
                ws_for_quota = getattr(mgr_for_quota, "websocket", None)
                if _is_websocket_connected(ws_for_quota):
                    try:
                        await ws_for_quota.send_json(quota_payload)
                    except Exception as e:
                        logger.debug(
                            "[EventBus] agent_quota_exceeded send failed: %s", e
                        )
            else:
                await _broadcast_to_all_connected(quota_payload)
            return

        # Resolve target session manager; fallback to broadcast if lanlan is unknown
        mgr = _get_session_manager(lanlan)
        if not mgr and event_type == "task_update":
            # Broadcast task_update to all connected sessions when lanlan is unresolvable
            task_payload = {"type": "agent_task_update", "task": event.get("task", {})}
            delivered = await _broadcast_to_all_connected(task_payload)
            if delivered == 0:
                logger.warning(
                    "[EventBus] task_update broadcast: no connected WebSocket sessions"
                )
            return

        # --- Music Global Broadcasts (Must come before early 'if not mgr' returns) ---
        elif event_type == "music_allowlist_add":
            # Music allowlist is a global UI state, broadcast to all active sessions
            targets = [mgr] if mgr else [m for _, m in _iter_session_managers()]
            payload = {
                "type": "music_allowlist_add",
                "domains": event.get("domains")
                or event.get("metadata", {}).get("domains", []),
            }

            async def _send_allowlist(target_mgr):
                if (
                    target_mgr
                    and target_mgr.websocket
                    and hasattr(target_mgr.websocket, "send_json")
                ):
                    try:
                        await target_mgr.websocket.send_json(payload)
                    except Exception as e:
                        logger.debug(
                            "[EventBus] music_allowlist_add broadcast failed: %s", e
                        )

            await asyncio.gather(
                *(_send_allowlist(t) for t in targets), return_exceptions=True
            )
            if targets:
                logger.info(
                    "[EventBus] music_allowlist_add broadcasted to %d sessions",
                    len(targets),
                )
            return

        elif event_type == "music_play_url":
            # Music playback is a global UI action, broadcast to all active sessions
            targets = [mgr] if mgr else [m for _, m in _iter_session_managers()]
            payload = {
                "type": "music_play_url",
                "url": event.get("url"),
                "name": event.get("name") or "Plugin Music",
                "artist": event.get("artist") or "External",
            }

            async def _send_play(target_mgr):
                if (
                    target_mgr
                    and target_mgr.websocket
                    and hasattr(target_mgr.websocket, "send_json")
                ):
                    try:
                        await target_mgr.websocket.send_json(payload)
                    except Exception as e:
                        logger.debug(
                            "[EventBus] music_play_url broadcast failed: %s", e
                        )

            await asyncio.gather(
                *(_send_play(t) for t in targets), return_exceptions=True
            )
            if targets:
                logger.info(
                    "[EventBus] music_play_url broadcasted to %d sessions", len(targets)
                )
            return
        if not mgr and event_type in ("proactive_message", "task_result"):
            fallback_name, fallback_mgr = _select_fallback_session_manager()
            if fallback_mgr is not None:
                mgr = fallback_mgr
                logger.warning(
                    "[EventBus] %s rerouted: lanlan=%s missing, fallback_session=%s",
                    event_type,
                    lanlan,
                    fallback_name,
                )
            else:
                # No target session found — drop the event entirely.
                # Do NOT broadcast text to other sessions to prevent cross-session leaks.
                logger.info(
                    "[EventBus] %s dropped: no target session for lanlan=%s, active_sessions=%s",
                    event_type,
                    lanlan,
                    [name for name, _ in _iter_session_managers()],
                )
                return
        if not mgr:
            logger.info(
                "[EventBus] %s dropped: no session_manager for lanlan=%s",
                event_type,
                lanlan,
            )
            return
        if event_type in ("task_result", "proactive_message"):
            raw_text = event.get("text") or ""
            # Why: chat-blind passthrough must preserve verbatim whitespace;
            # only the empty-check / log / callback paths use the stripped form.
            text = raw_text.strip()

            # v2 push_message: media parts (image/audio/video) ride on the
            # same proactive_message event.  Image parts go straight to the
            # realtime session via ``stream_image`` (the public vision-input
            # API on OmniRealtimeClient/OmniOfflineClient) before the (text
            # → callback) path so the AI sees them in the same context
            # window as the text it's about to respond to.
            #
            # Audio / video aren't supported here — ``stream_audio`` is the
            # live-mic PCM pipeline (specific sample rate + RNNoise gate),
            # not a generic file injector, and we have no video API.
            # ai_behavior=blind suppresses injection entirely.
            media_parts = (
                event.get("media_parts")
                if isinstance(event.get("media_parts"), list)
                else []
            )
            ai_behavior_v2 = event.get("ai_behavior")
            # Images that must travel WITH a proactive (respond) callback so they
            # can be streamed at the moment the pacing manager releases the cue
            # (see LLMSessionManager._deliver_proactive_batch). Streaming them
            # here immediately would land the image in the previous/current turn
            # (or drop it when no session exists yet) while the text is held back
            # by the manager — the eventual proactive response would then lack
            # its matching visual context.
            deferred_proactive_images: list[str] = []
            if media_parts and ai_behavior_v2 in ("respond", "read"):
                sess = getattr(mgr, "session", None)
                stream_image = getattr(sess, "stream_image", None) if sess else None
                for mp in media_parts:
                    if not isinstance(mp, dict):
                        continue
                    part_type = mp.get("type")
                    b64 = mp.get("binary_base64")
                    url = mp.get("url")
                    mime = mp.get("mime") or ""
                    if part_type != "image":
                        # ``audio`` / ``video`` need provider-specific transport
                        # we don't have today; drop with a one-line warning so
                        # plugin authors notice instead of silently losing
                        # frames.
                        logger.warning(
                            "[EventBus] media_part type=%s not yet supported (mime=%s); dropped",
                            part_type,
                            mime,
                        )
                        continue
                    if isinstance(b64, str) and b64:
                        if ai_behavior_v2 == "respond" and text:
                            # Defer: stream when the manager releases this cue so
                            # the image shares the proactive response's context.
                            # (Only when there's text — the callback that carries
                            # these images is built in the ``if text:`` block.)
                            deferred_proactive_images.append(b64)
                            continue
                        # read (passive), OR image-only respond with no text to
                        # carry it through the pacing manager: inject now so it
                        # isn't lost (image-only respond has no text cue to drive
                        # a proactive turn anyway).
                        if stream_image is None:
                            logger.debug(
                                "[EventBus] image media_part dropped: session=%s has no stream_image",
                                type(sess).__name__ if sess else "None",
                            )
                            continue
                        # ``stream_image`` takes a base64 STRING (not bytes); pass through
                        try:
                            await stream_image(b64)
                            logger.debug(
                                "[EventBus] image media_part injected (base64 len=%d, mime=%s)",
                                len(b64),
                                mime,
                            )
                        except Exception as e:
                            logger.warning(
                                "[EventBus] image media_part stream_image failed: %s", e
                            )
                    elif isinstance(url, str) and url:
                        # TODO(v0.9): fetch URL → bytes → base64 → stream_image.
                        # Until then plugin authors should inline-encode small
                        # images (≤256KB) or pre-fetch URL-served frames into
                        # ``parts`` themselves.
                        logger.warning(
                            "[EventBus] image media_part url=%s not yet fetched; dropped",
                            url[:80],
                        )
                    # else: malformed part, silently skip

            if text:
                if event.get("direct_reply"):
                    detail_text = (event.get("detail") or text).strip()
                    # Plugin-supplied direct_reply text bypasses the LLM and
                    # speaks/types verbatim. Plugin authors may write
                    # ``{MASTER_NAME}``/``{LANLAN_NAME}`` placeholders since
                    # they don't know which session their text will route to;
                    # expand here so the placeholder doesn't reach TTS/UI
                    # literally. (See main_logic.core.apply_role_placeholders
                    # for the contract — same helper as the LLM-injection path
                    # so all plugin-text exits share one spelling.)
                    detail_text = core.apply_role_placeholders(
                        detail_text,
                        lanlan_name=getattr(mgr, "lanlan_name", "") or "",
                        master_name=getattr(mgr, "master_name", "") or "",
                    )
                    delivered = False
                    if detail_text and hasattr(mgr, "send_lanlan_response"):
                        try:
                            delivered = bool(
                                await mgr.send_lanlan_response(detail_text, True)
                            )
                        except Exception as e:
                            logger.warning(
                                "[EventBus] direct task_result reply failed: %s", e
                            )
                    if delivered and hasattr(mgr, "handle_proactive_complete"):
                        try:
                            await mgr.handle_proactive_complete()
                        except Exception as e:
                            logger.warning(
                                "[EventBus] direct task_result turn_end failed: %s", e
                            )
                    if delivered:
                        # detail_text 是面向用户的回复内容，不写 logger
                        logger.info(
                            "[EventBus] direct task_result reply delivered (detail_len=%d)",
                            len(detail_text),
                        )
                        print(
                            f"[EventBus] direct task_result reply: {detail_text[:60]}"
                        )
                        return

                # Build structured callback and enqueue for LLM injection
                cb_status = event.get("status") or (
                    "completed" if event.get("success", True) else "failed"
                )
                # delivery_mode controls how the callback reaches the LLM:
                #   proactive (default): enqueue + immediately schedule trigger_agent_callbacks
                #   passive            : enqueue only (next user turn will drain)
                #   silent             : skip LLM channel entirely (frontend HUD still fires)
                delivery_mode = (event.get("delivery_mode") or "proactive").strip()
                if delivery_mode not in ("proactive", "passive", "silent"):
                    delivery_mode = "proactive"
                # Defensive: blind ai_behavior must NEVER reach the LLM channel,
                # even if delivery_mode arrives as "proactive" / "passive". The
                # plugin proactive_bridge already maps blind→silent, but this
                # is an indirect contract — a future direct emitter (or a bug
                # in another bridge) could violate it. Forcing silent here
                # locks the (blind ⇒ no LLM enqueue) invariant on the host
                # side regardless of caller-supplied delivery_mode.
                if (event.get("ai_behavior") or "").strip() == "blind":
                    delivery_mode = "silent"
                # Default source_kind from channel when caller didn't specify one.
                # Plugin emit sites already pass explicit source_kind/source_name.
                _channel = event.get("channel") or "unknown"
                source_kind = (event.get("source_kind") or "").strip()
                source_name = (event.get("source_name") or "").strip()
                if not source_kind:
                    if _channel == "user_plugin":
                        source_kind = "plugin"
                    elif _channel in ("computer_use", "cu"):
                        source_kind = "cu"
                    elif _channel in ("browser_use", "browser"):
                        source_kind = "browser"
                    elif _channel.startswith("plugin:"):
                        source_kind = "plugin"
                        if not source_name:
                            source_name = _channel.split(":", 1)[1]
                    else:
                        source_kind = "system"
                event_metadata = (
                    event.get("metadata")
                    if isinstance(event.get("metadata"), dict)
                    else {}
                )
                # origin is a STRUCTURAL fact derived from event_type:
                #   "task_result"      → real task completion (agent_server._emit_task_result):
                #                        Computer Use / Browser Use / plugin entry / MCP tool result
                #   "proactive_message" → plugin push_message stream (proactive_bridge):
                #                        danmaku / gift / external notification
                # Plugin authors cannot influence this — it's determined by which
                # SDK method they call (finish() vs push_message()) and which host
                # path it flows through. _build_callback_instruction uses this to
                # pick the right wrapper template (task "汇报" vs event "回应").
                if event_type == "task_result":
                    origin = "task_result"
                else:
                    # event_type == "proactive_message" (or any future event-stream
                    # producer that lands on this branch); see the (event_type in
                    # {"task_result", "proactive_message"}) gate above.
                    origin = "event"
                # Proactive-delivery hints from push_message (priority +
                # coalesce_key). Lower priority = more urgent; unspecified
                # (0) is normalised to a neutral band by the manager.
                try:
                    # OverflowError: JSON Infinity/-Infinity → float → int() raises;
                    # must not let a malformed priority drop the whole callback.
                    cb_priority = int(event.get("priority", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    cb_priority = 0
                cb_coalesce_key = event.get("coalesce_key")
                if not isinstance(cb_coalesce_key, str):
                    cb_coalesce_key = ""
                callback = {
                    "event": "agent_task_callback",
                    "origin": origin,
                    "task_id": event.get("task_id") or "",
                    "channel": _channel,
                    "status": cb_status,
                    "success": bool(event.get("success", True)),
                    "summary": event.get("summary") or text,
                    "detail": event.get("detail") or text,
                    "error_message": event.get("error_message") or "",
                    "source_kind": source_kind,
                    "source_name": source_name,
                    "delivery_mode": delivery_mode,
                    "priority": cb_priority,
                    "coalesce_key": cb_coalesce_key,
                    # Images to stream at manager-release time (respond only;
                    # empty for read, which already streamed above).
                    "media_images": deferred_proactive_images,
                    "timestamp": event.get("timestamp") or "",
                    "metadata": event_metadata,
                    "context_type": event_metadata.get("context_type") or "",
                }
                if delivery_mode != "silent":
                    if delivery_mode == "passive":
                        # Passive cues keep the direct enqueue-only path:
                        # they must NOT interrupt; the next user turn drains
                        # them. The pacing manager only governs proactive.
                        mgr.enqueue_agent_callback(callback)
                        logger.info(
                            "[EventBus] %s enqueued callback (passive); next user turn will carry it",
                            event_type,
                        )
                    else:
                        # Proactive: hand to the delivery manager, which
                        # orders by priority, coalesces by key, and paces
                        # release on the frontend playback gate + min-gap.
                        logger.info(
                            "[EventBus] %s submitting proactive callback to delivery manager (priority=%s key=%r)",
                            event_type,
                            cb_priority,
                            cb_coalesce_key or "(source)",
                        )
                        mgr.submit_proactive_callback(
                            callback,
                            priority=cb_priority,
                            coalesce_key=cb_coalesce_key or None,
                        )
                else:
                    logger.info(
                        "[EventBus] %s delivery=silent: skipping LLM channel (frontend HUD still fires)",
                        event_type,
                    )

                # v2 chat+blind passthrough: render verbatim into chat
                # bubble WITHOUT entering chat-LLM context. Distinct from
                # mirror_assistant_output (which writes to sync_message_queue
                # so cross_server may add an AIMessage). Both this branch
                # and the HUD agent_notification below can fire when
                # visibility=["chat","hud"] — they're orthogonal sinks.
                #
                # Gated on visibility containing "chat" AND ai_behavior=="blind"
                # because non-blind ai_behavior already enqueues the LLM
                # callback above and the AI's own response is what the
                # user should see in the chat bubble.
                _vis_raw = event.get("visibility")
                _vis_present = isinstance(_vis_raw, list)
                _vis = _vis_raw if _vis_present else []
                _ai_behavior = (event.get("ai_behavior") or "").strip()
                if (
                    "chat" in _vis
                    and _ai_behavior == "blind"
                    and hasattr(mgr, "passthrough_to_chat_bubble")
                ):
                    passthrough_dispatched = False
                    try:
                        # Reuse the already-resolved source_kind local (computed
                        # above from channel: computer_use→cu, browser_use→browser,
                        # plugin:*→plugin, else system). Falling back to event
                        # raw + "plugin" default would mislabel non-plugin sources.
                        passthrough_source = source_kind or "plugin"
                        # Why: passthrough_to_chat_bubble swallows send_json
                        # failures and is a no-op when WS is missing/disconnected,
                        # so absence-of-exception is NOT proof a frame was sent.
                        # We must gate handle_proactive_complete on the bool
                        # return — otherwise we emit turn-end without a matching
                        # turn-start (frontend never opened the assistant
                        # lifecycle), corrupting proactive rescheduling.
                        # Same role-placeholder contract as the direct_reply
                        # path: blind-passthrough text reaches the chat bubble
                        # verbatim without going through the LLM, so the
                        # placeholder has to be expanded here or the literal
                        # ``{MASTER_NAME}`` token would render in the bubble.
                        passthrough_text = core.apply_role_placeholders(
                            raw_text,
                            lanlan_name=getattr(mgr, "lanlan_name", "") or "",
                            master_name=getattr(mgr, "master_name", "") or "",
                        )
                        passthrough_dispatched = bool(
                            await mgr.passthrough_to_chat_bubble(
                                passthrough_text,
                                request_id=event.get("task_id") or None,
                                source=passthrough_source,
                            )
                        )
                        logger.info(
                            "[EventBus] passthrough_to_chat_bubble dispatched=%s (text_len=%d, source=%s)",
                            passthrough_dispatched,
                            len(text),
                            passthrough_source,
                        )
                    except Exception as e:
                        logger.warning(
                            "[EventBus] passthrough_to_chat_bubble failed: %s",
                            e,
                        )
                    # Why: gemini_response opens an assistant turn lifecycle on
                    # the frontend (ensureAssistantTurnStarted in app-websocket.js);
                    # without a matching turn-end event the assistant bubble
                    # stays "in-progress" and proactive rescheduling / lifecycle
                    # finalization never fire. handle_proactive_complete is the
                    # canonical turn-end emitter shared with the direct task_result
                    # reply path above. The HUD agent_notification branch below
                    # does NOT open an assistant turn, so single-emit here is
                    # sufficient even when visibility=["chat","hud"].
                    if passthrough_dispatched and hasattr(
                        mgr, "handle_proactive_complete"
                    ):
                        try:
                            await mgr.handle_proactive_complete()
                        except Exception as e:
                            logger.warning(
                                "[EventBus] passthrough turn_end emit failed: %s",
                                e,
                            )
                # v2 visibility contract: HUD agent_notification fires only
                # when "hud" is in visibility. Why: visibility=["chat"] must
                # not double-render as both chat bubble AND HUD toast.
                # Legacy emitters that omit the visibility field entirely
                # (no v2 plumbing) keep the pre-v2 behavior of firing HUD
                # by default — checked via _vis_present, not via _vis truthiness,
                # so an explicit visibility=[] (v2 "no verbatim render") suppresses HUD.
                _hud_allowed = ("hud" in _vis) if _vis_present else True
                ws = getattr(mgr, "websocket", None)
                if not _hud_allowed:
                    logger.info(
                        "[EventBus] agent_notification suppressed by visibility=%s (no 'hud') for lanlan=%s",
                        _vis,
                        lanlan,
                    )
                elif _is_websocket_connected(ws):
                    try:
                        # HUD agent_notification renders verbatim to the user;
                        # expand role placeholders so plugin authors can write
                        # ``"通知 {MASTER_NAME}..."`` without the literal token
                        # showing up in the toast.
                        notif_text = core.apply_role_placeholders(
                            text,
                            lanlan_name=getattr(mgr, "lanlan_name", "") or "",
                            master_name=getattr(mgr, "master_name", "") or "",
                        )
                        notif = {
                            "type": "agent_notification",
                            "text": notif_text,
                            "source": "brain",
                            "status": cb_status,
                        }
                        err_msg = event.get("error_message") or ""
                        if err_msg:
                            notif["error_message"] = err_msg[
                                :USER_NOTIFICATION_ERROR_MAX_CHARS
                            ]
                        await ws.send_json(notif)
                        # text 是面向前端的通知正文，不写 logger
                        logger.info(
                            "[EventBus] agent_notification sent to frontend (text_len=%d)",
                            len(text),
                        )
                        print(f"[EventBus] agent_notification text: {text[:60]}")
                    except Exception as e:
                        logger.warning(
                            "[EventBus] agent_notification WS send failed: %s", e
                        )
                else:
                    logger.warning(
                        "[EventBus] agent_notification: WebSocket not connected for lanlan=%s",
                        lanlan,
                    )
        elif event_type == "agent_notification":
            ws = getattr(mgr, "websocket", None)
            if _is_websocket_connected(ws):
                try:
                    notif = {
                        "type": "agent_notification",
                        "text": event.get("text", ""),
                        "source": event.get("source", "brain"),
                        "status": event.get("status", "error"),
                    }
                    err_msg = event.get("error_message") or ""
                    if err_msg:
                        notif["error_message"] = err_msg[
                            :USER_NOTIFICATION_ERROR_MAX_CHARS
                        ]
                    await ws.send_json(notif)
                except Exception as e:
                    logger.debug("[EventBus] agent_notification send failed: %s", e)
            else:
                logger.debug(
                    "[EventBus] agent_notification: WebSocket not connected for lanlan=%s",
                    lanlan,
                )
        elif event_type == "task_update":
            task_payload = {"type": "agent_task_update", "task": event.get("task", {})}
            ws = getattr(mgr, "websocket", None)
            if _is_websocket_connected(ws):
                try:
                    await ws.send_json(task_payload)
                except Exception as e:
                    logger.warning(
                        "[EventBus] task_update send failed for lanlan=%s: %s",
                        lanlan,
                        e,
                    )
            else:
                logger.warning(
                    "[EventBus] task_update dropped: WebSocket not connected for lanlan=%s",
                    lanlan,
                )
    except Exception as e:
        logger.debug(f"handle_agent_event error: {e}")


async def _refresh_character_globals():
    """Refresh character-related module globals (re-fetch aget_character_data from config).

    Every fast-path entry must go through this first, so that after operations like
    set_current_catgirl / update_catgirl, subsequent reads of her_name / lanlan_prompt /
    lanlan_basic_config see the latest values.
    """
    global master_name, her_name, master_basic_config, lanlan_basic_config
    global name_mapping, lanlan_prompt, time_store, setting_store, recent_log
    global catgirl_names
    (
        master_name,
        her_name,
        master_basic_config,
        lanlan_basic_config,
        name_mapping,
        lanlan_prompt,
        time_store,
        setting_store,
        recent_log,
    ) = await _config_manager.aget_character_data()
    catgirl_names = list(lanlan_prompt.keys())
    facade = sys.modules[__package__]
    facade.master_name = master_name
    facade.her_name = her_name
    facade.master_basic_config = master_basic_config
    facade.lanlan_basic_config = lanlan_basic_config
    facade.name_mapping = name_mapping
    facade.lanlan_prompt = lanlan_prompt
    facade.time_store = time_store
    facade.setting_store = setting_store
    facade.recent_log = recent_log
    facade.catgirl_names = catgirl_names


def _ensure_character_slots(k: str) -> bool:
    """Prepare the per-k sync resource slot for a single catgirl. Returns whether this is a newly created character (which decides whether to force-start the task afterwards).

    A purely in-memory atomic operation: either role_state[k] already exists (do
    nothing), or both the queue and websocket_lock are filled in at once. This avoids
    the half-initialization risk of the old code, where 6 dicts used two different
    sentinels (sync_message_queue vs websocket_locks) to independently decide
    "does this character already have a slot".

    Note: ``asyncio.Queue`` does not need a running loop at creation time on
    Python 3.10+; although this function is sync, its call chain comes from async
    contexts like ``initialize_character_data`` / ``_init_character_resources``,
    so a loop is available.
    """
    if k not in role_state:
        role_state[k] = RoleState(
            sync_message_queue=_SyncMessageQueue(),
            websocket_lock=asyncio.Lock(),
        )
        logger.info(f"为角色 {k} 初始化新资源")
        return True
    return False


async def _init_character_resources(k: str, is_new_character: bool):
    """Complete the session_manager update + sync connector task check/restart for a single catgirl.

    Depends on module globals: master_name, lanlan_prompt, lanlan_basic_config (the caller must refresh them first).
    Writes the per-k slots: role_state[k].session_manager / sync_task — no state is
    shared between different k, so this is safe to run in parallel.
    """
    rs = role_state[k]  # 调用方必须先 _ensure_character_slots，保证这里可直接索引
    # 更新或创建session manager（使用最新的prompt）
    # 使用锁保护websocket的preserve/restore操作，防止与cleanup()竞争
    async with rs.websocket_lock:
        # 如果已存在且已有websocket连接，保留websocket引用
        old_websocket = None
        if rs.session_manager is not None and rs.session_manager.websocket:
            old_websocket = rs.session_manager.websocket
            logger.info(f"保留 {k} 的现有WebSocket连接")

        # 注意：不在这里清理旧session，因为：
        # 1. 切换当前角色音色时，已在API层面关闭了session
        # 2. 切换其他角色音色时，已跳过重新加载
        # 3. 其他场景不应该影响正在使用的session
        # 如果旧session_manager有活跃session，保留它，只更新配置相关的字段

        # 先检查会话状态（在锁内检查避免竞态条件）
        # 同时覆盖 "正在启动" 窗口：_starting_session_count>0 但 is_active=False
        # 的期间，start_session 协程仍持有对当前 manager 的引用；如果此时替换
        # 实例，旧 manager 会在后台完成启动并挂起 OmniRealtimeClient / TTS 线程 /
        # message_handler_task，永远没人调用 end_session — 造成资源泄漏。
        mgr = rs.session_manager
        has_active_session = mgr is not None and mgr.is_active
        has_starting_session = mgr is not None and mgr.is_starting and not mgr.is_active

        if has_active_session:
            # 有活跃session，不重新创建session_manager，只更新配置
            # 这是为了防止重新创建session_manager时破坏正在运行的session
            try:
                old_mgr = rs.session_manager
                # 更新prompt
                old_mgr.lanlan_prompt = (
                    lanlan_prompt[k]
                    .replace("{LANLAN_NAME}", k)
                    .replace("{MASTER_NAME}", master_name)
                )
                # 直接读 module global lanlan_basic_config，避免重复 load + deepcopy。
                # 经 read_legacy_voice_id 容忍 voice 的扁平串 / 结构对象两形态（惰性迁移）。
                from utils.voice_config import read_legacy_voice_id

                old_mgr.voice_id = read_legacy_voice_id(
                    get_reserved(
                        lanlan_basic_config[k],
                        "voice_id",
                        default="",
                        legacy_keys=("voice_id",),
                    )
                )
                logger.info(f"{k} 有活跃session，只更新配置，不重新创建session_manager")
            except Exception as e:
                logger.error(f"更新 {k} 的活跃session配置失败: {e}", exc_info=True)
                # 配置更新失败，但为了不影响正在运行的session，继续使用旧配置
                # 如果确实需要更新配置，可以考虑在下次session重启时再应用
        elif has_starting_session:
            # start_session 正在执行中：只保留实例避免孤儿泄漏，但绝对不热改
            # lanlan_prompt / voice_id — start_session 会在 core.py 内用
            # self.lanlan_prompt 拼装首帧 session prompt，并基于当前 self.voice_id
            # 计算音色/TTS 分支。本轮写入会让正在进行的启动拿到半旧半新配置
            # （用户侧看到启动出来的会话 prompt / 音色与最新配置不一致）。
            # 本轮的新 prompt / 音色由下一次 start_session 应用。
            logger.info(
                f"{k} session 正在启动中（is_starting），保留现有 session_manager，"
                "本轮不热更新 prompt/voice_id 以免污染 in-flight 启动"
            )
        else:
            # 没有活跃session，可以安全地重新创建session_manager
            # 旧 manager 持有的后台任务（如 idle session reset loop）必须显式
            # cancel，否则强引用 self 让旧 manager 永远不被 GC——多次 reload 后
            # 积累 N 份的 idle loop 各自 60s 醒一次。
            if rs.session_manager is not None:
                try:
                    rs.session_manager.shutdown()
                except Exception as e:
                    logger.warning(f"shutdown 旧 session_manager 失败 ({k}): {e}")
            new_mgr = core.LLMSessionManager(
                rs.sync_message_queue,
                k,
                lanlan_prompt[k]
                .replace("{LANLAN_NAME}", k)
                .replace("{MASTER_NAME}", master_name),
            )

            # 将websocket锁存储到session manager中，供cleanup()使用
            new_mgr.websocket_lock = rs.websocket_lock

            # 恢复websocket引用（如果存在）
            if old_websocket:
                new_mgr.websocket = old_websocket
                logger.info(f"已恢复 {k} 的WebSocket连接")

            rs.session_manager = new_mgr

    # 检查并启动同步连接器 task
    # 如果是新角色，或者 task 不存在/已结束，需要启动
    need_start_task = False
    if is_new_character:
        need_start_task = True
    elif rs.sync_task is None or rs.sync_task.done():
        need_start_task = True

    if need_start_task:
        try:
            _char_name = k

            def _make_status_cb(char_name):
                def _cb(msg):
                    mgr = _get_session_manager(char_name)
                    if not mgr:
                        return
                    ws = mgr.websocket
                    if (
                        ws
                        and hasattr(ws, "client_state")
                        and ws.client_state == ws.client_state.CONNECTED
                    ):
                        import json as _json

                        data = _json.dumps({"type": "status", "message": msg})

                        # cross_server 现在和我们在同一个主 loop 上，回调
                        # 也是从主 loop 同步调用的——直接 create_task 即可，
                        # 不再需要 run_coroutine_threadsafe。
                        # done_callback 消化 task 的 exception，避免 ws 断开时
                        # asyncio 输出 "Task exception was never retrieved" 噪音；
                        # status 是 best-effort 降级路径，丢一条不影响主逻辑。
                        # cancelled 态下 task.exception() 自身会 raise CancelledError，
                        # 必须先用 task.cancelled() 早返回，否则 callback 自己又制造
                        # 一条 "exception was never retrieved" 噪音。
                        def _swallow_status_send_exc(_t):
                            if _t.cancelled():
                                return
                            exc = _t.exception()
                            if exc is not None:
                                logger.debug(
                                    "status 回调 ws.send_text 失败（已忽略）: %s", exc
                                )

                        try:
                            _t = asyncio.create_task(ws.send_text(data))
                            _t.add_done_callback(_swallow_status_send_exc)
                        except RuntimeError:
                            # 极端情况：当前没有 running loop（理论上不会发生
                            # 在 cross_server 调用路径上，但兜底）。回退到旧
                            # 跨 loop 路径。
                            loop = runtime.server_loop
                            if loop is not None and not loop.is_closed():
                                asyncio.run_coroutine_threadsafe(
                                    ws.send_text(data), loop
                                )

                return _cb

            _status_cb = _make_status_cb(_char_name)

            new_task = asyncio.create_task(
                cross_server.run_sync_connector(
                    rs.sync_message_queue,
                    k,
                    f"ws://127.0.0.1:{MONITOR_SERVER_PORT}",
                    {"bullet": False, "monitor": True},
                    _status_cb,
                ),
                name=f"SyncConnector-{k}",
            )
            rs.sync_task = new_task
            logger.info(f"✅ 已为角色 {k} 启动同步连接器 task ({new_task.get_name()})")
        except Exception as e:
            logger.error(f"❌ 启动角色 {k} 的同步连接器 task 失败: {e}", exc_info=True)


async def _stop_character_thread(k: str):
    """Stop a single catgirl's sync connector task (waiting up to 3s for cleanup). Dict cleanup is left to the caller to do in order.

    The ``_thread`` suffix in the name is kept to avoid touching the many call sites; the underlying mechanism is now an ``asyncio.Task``.
    """
    rs = role_state.get(k)
    if rs is None or rs.sync_task is None:
        return
    task = rs.sync_task
    try:
        logger.info(f"正在停止角色 {k} 的同步连接器 task...")
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ 同步连接器 task {k} 未能在 3s 内退出，放任其自行结束")
        except asyncio.CancelledError:
            # cancel 后 await 抛 CancelledError 是正常路径
            pass
        except Exception as e:
            logger.debug(f"同步连接器 task {k} 退出时异常: {e}", exc_info=True)
        else:
            logger.info(f"✅ 已停止角色 {k} 的同步连接器 task")
    except Exception as e:
        logger.warning(f"停止角色 {k} 的同步连接器 task 时出错: {e}")


def _cleanup_character_dicts(k: str):
    """Synchronously clean up a single catgirl's per-k slot. Make sure the corresponding task has stopped or timed out before calling."""
    rs = role_state.get(k)
    if rs is None:
        return
    # 清理队列（asyncio.Queue 也没有 close/join_thread 方法，drain 即可）
    try:
        while not rs.sync_message_queue.empty():
            rs.sync_message_queue.get_nowait()
    except asyncio.QueueEmpty:
        # while empty + get_nowait 本身是 racy idiom：另一线程可能先 drain 掉，
        # 导致 get_nowait 抛 Empty。这里 role_state[k] 即将被 del 掉，忽略无害。
        pass
    # 一次 del 原子清掉所有 6 个字段 —— 替代旧代码里 6 张 dict 分别 del 的对称清理
    del role_state[k]


async def initialize_character_data():
    """Full refresh: load config + run per-k init for every catgirl + clean up deleted ones.

    Cold path (startup / master-name edit / large bulk import). For per-catgirl edits
    use the fast paths: init_one_catgirl / remove_one_catgirl / switch_current_catgirl_fast.
    """
    logger.info("正在加载角色配置...")

    # 清理无效的voice_id引用；如果发现旧版 CosyVoice 音色，推入通知缓冲池等前端连接后弹出
    # cleanup_invalid_voice_ids 内部涉及同步 IO（load/save characters），offload 以免阻塞事件循环
    _cleaned, _legacy_names = await asyncio.to_thread(
        _config_manager.cleanup_invalid_voice_ids
    )
    if _legacy_names:
        core.enqueue_voice_migration_notice(_legacy_names)

    # 加载最新的角色数据（offload，避免同步 IO + deepcopy 阻塞事件循环）
    await _refresh_character_globals()

    # 为所有 catgirl 预备 per-k 同步资源槽位
    is_new_map: dict[str, bool] = {k: _ensure_character_slots(k) for k in catgirl_names}

    # 每个角色的初始化相互独立（只读共享 prompt / master_name，写各自的 session_manager[k] 等 per-key 槽位）。
    # 用 gather 并行，消除 O(N) × (thread roundtrip + 0.1s sleep) 的串行墙钟。
    # return_exceptions=True：某个角色初始化失败不应导致其它角色被取消。
    _init_results = await asyncio.gather(
        *[_init_character_resources(k, is_new_map[k]) for k in catgirl_names],
        return_exceptions=True,
    )
    for k, res in zip(catgirl_names, _init_results):
        if isinstance(res, BaseException):
            logger.error(f"❌ 初始化角色 {k} 失败: {res}", exc_info=res)

    # 清理已删除角色的资源
    removed_names = [k for k in role_state.keys() if k not in catgirl_names]

    # N 个 join(timeout=3) 串行最坏要 3N 秒；并行化后墙钟 ≈ 3 秒。
    if removed_names:
        await asyncio.gather(
            *[_stop_character_thread(k) for k in removed_names],
            return_exceptions=True,
        )

    # 线程都已停/超时，再在事件循环里顺序清理 dict —— 这些操作都是纯内存，不需要并行。
    for k in removed_names:
        logger.info(f"清理已删除角色 {k} 的资源")
        _cleanup_character_dicts(k)

    logger.info(f"角色配置加载完成，当前角色: {catgirl_names}，主人: {master_name}")


# ─────────────────────────────────────────────────────────────
# Fast-path helpers — 只处理受影响的单个 catgirl，避免全量遍历
# ─────────────────────────────────────────────────────────────


async def switch_current_catgirl_fast():
    """Dedicated fast path for switching the current catgirl (change of the `current catgirl` field).

    Key premise: the switch only affects the single global `her_name`; per-k prompt /
    voice_id / thread state is completely unchanged. So this **only refreshes
    globals** and does no per-k work at all.

    Wall clock: one aget_character_data (~a few ms) and that's everything.
    """
    await _refresh_character_globals()
    logger.info(f"[fast-switch] 已刷新 globals，当前猫娘: {her_name}")


async def init_one_catgirl(name: str, *, is_new: bool = False):
    """Fast path for adding / editing a single catgirl.

    - is_new=True: addition; force-starts the sync connector thread
    - is_new=False: edit (prompt / voice_id etc.) — only refreshes the session_manager's
                    prompt/voice_id, does not restart the thread
    """
    await _refresh_character_globals()
    if name not in lanlan_prompt:
        logger.warning(f"[init-one] '{name}' 不在 config 中，跳过（可能是并发删除）")
        return
    slot_new = _ensure_character_slots(name)
    await _init_character_resources(name, is_new_character=is_new or slot_new)


async def remove_one_catgirl(name: str):
    """Fast path for deleting a single catgirl: stop the character's thread + clear dicts + refresh globals."""
    await _stop_character_thread(name)
    _cleanup_character_dicts(name)
    # config 文件已由调用方写入，这里刷新 globals 让 catgirl_names 等反映删除
    await _refresh_character_globals()
    logger.info(f"[fast-remove] 已移除角色 {name}")
