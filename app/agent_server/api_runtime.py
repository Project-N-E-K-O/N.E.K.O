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

"""Lifecycle, session events, and task endpoints for the agent server.

Tasks are commissions of the NekoClaw host (``nekoclaw/host``). This module
connects the host to the rest of the process: the two requests the main
dialogue's task tools make (dispatch, query), the list a new session is told,
the check of a turn in which she called no task tool, and the task endpoints
the HUD uses.
"""

import math
import threading
from collections import OrderedDict

from .api_shared import (  # noqa: F401
    AgentServerEventBridge,
    Any,
    Dict,
    HTTPException,
    HUD_ACTIVE,
    List,
    Modules,
    Optional,
    ThrottledLogger,
    USER_PLUGIN_SERVER_PORT,
    _bump_state_revision,
    _close_browser,
    _create_tracked_task,
    _emit_agent_status_update,
    _emit_main_event,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _fire_agent_llm_connectivity_check,
    _set_capability,
    _start_embedded_user_plugin_server,
    _stop_embedded_user_plugin_server,
    app,
    asyncio,
    builtin_provider,
    get_config_manager,
    httpx,
    hud,
    ledger_exists,
    logger,
    mount_nekoclaw_host,
    nekoclaw_on,
    os,
    peek_host,
    ready_host,
    restore_hud,
    shutdown_nekoclaw_host,
    time,
    uuid,
)


def _plugin_list_change_token() -> tuple:
    """Cheap in-process change signal for the host's cached plugin list.

    The plugin server is embedded in this process. Its lifecycle bus revision
    is bumped on every start/stop/reload/delete/load event, but a plugin
    process that dies on its own emits no event — so the set of hosts whose
    process is still alive (the same check ``GET /plugins`` uses to report
    ``running``) is part of the token too.

    Called synchronously on the event loop, so it must never wait on the
    plugin-hosts read/write lock: the snapshot is taken without blocking and,
    when no snapshot is available at all, the token is reported unreadable
    (the host then refetches ``/plugins`` asynchronously).

    A live process whose plugin ``GET /plugins`` would no longer report as
    ``running`` (``runtime_source_missing`` / ``runtime_load_state == "failed"``,
    see ``_resolve_plugin_status``) is left out of the alive set, so marking
    it moves the token and a failed refresh cannot keep offering it.

    The handlers revision covers dynamic entry register/unregister (e.g.
    ``disable_entry``), which changes ``/plugins`` without a lifecycle event.
    """
    from plugin.core.state import state as plugin_state

    hosts = plugin_state.get_plugin_hosts_snapshot_nowait()
    plugins_meta = plugin_state.get_plugins_snapshot_nowait()
    if hosts is None or plugins_meta is None:
        raise RuntimeError("plugin snapshot unavailable without blocking")
    alive = []
    for plugin_id, host in hosts.items():
        meta = plugins_meta.get(plugin_id)
        if isinstance(meta, dict) and (
            meta.get("runtime_source_missing") is True
            or meta.get("runtime_load_state") == "failed"
        ):
            continue
        try:
            if host.is_alive():
                alive.append(str(plugin_id))
        except Exception:
            continue
    revision = (plugin_state.get_bus_rev("lifecycle"), plugin_state.get_event_handlers_revision())
    return revision, tuple(sorted(alive))


def _check_agent_api_gate() -> Dict[str, Any]:
    """Unified agent API gate check."""
    try:
        cm = get_config_manager()
        ok, reasons = cm.is_agent_api_ready()
        return {"ready": ok, "reasons": reasons, "is_free_version": cm.is_agent_free()}
    except Exception as e:
        return {"ready": False, "reasons": [f"Agent API check failed: {e}"], "is_free_version": False}


def _agent_master_enabled() -> bool:
    return bool(Modules.analyzer_enabled)


def _user_plugins_enabled() -> bool:
    return bool((Modules.agent_flags or {}).get("user_plugin_enabled", False))


def _voice_transcript_plugin_gate_reason() -> str:
    if not _agent_master_enabled():
        return "agent_disabled"
    if not _user_plugins_enabled():
        return "user_plugin_disabled"
    return ""


async def _handle_voice_transcript_request(event: Dict[str, Any]) -> None:
    event_id = str((event or {}).get("event_id") or "")
    lanlan_name = (event or {}).get("lanlan_name")

    try:
        from plugin.server.application.plugins import voice_transcript_bridge

        if not voice_transcript_bridge.voice_transcript_event_has_text(event):
            logger.debug("[VoiceBridge] observed transcript skipped: empty event_id=%s", event_id)
        elif gate_reason := _voice_transcript_plugin_gate_reason():
            if gate_reason == "agent_disabled":
                logger.debug("[VoiceBridge] observed transcript skipped: agent disabled event_id=%s", event_id)
            else:
                logger.debug(
                    "[VoiceBridge] observed transcript skipped: user plugins disabled event_id=%s",
                    event_id,
                )
        else:
            lifecycle_ready = bool(Modules.plugin_lifecycle_started)
            if not lifecycle_ready:
                lifecycle_ready = await _ensure_plugin_lifecycle_started()

            if not lifecycle_ready:
                logger.debug(
                    "[VoiceBridge] observed transcript skipped: plugin lifecycle unavailable event_id=%s",
                    event_id,
                )
            else:
                result = await voice_transcript_bridge.resolve_voice_transcript_request(
                    event,
                    timeout=voice_transcript_bridge.VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS,
                )
                logger.debug(
                    "[VoiceBridge] observed transcript dispatched: event_id=%s lanlan=%s action=%s",
                    event_id,
                    lanlan_name,
                    result.get("action") if isinstance(result, dict) else "",
                )
    except Exception as exc:
        logger.debug(
            "[VoiceBridge] plugin dispatch failed: event_id=%s lanlan=%s err=%s",
            event_id,
            lanlan_name,
            exc,
        )


def _forward_provider_frame(event: Dict[str, Any]) -> bool:
    """Copy one already-delivered provider frame into the plugin ``frames`` store.

    main_server owns the session and cannot write to the message plane (the
    ingest credential is process-local to the plugin server, which is embedded
    here), so the frame arrives over the existing session PUB channel and this
    is its only hop into the bus.

    Gated on user plugins being enabled because ``frames`` exists for plugins
    and nothing else: with them off there is no reader, and copying the user's
    screen into a resident deque would be retention that buys nothing. Not
    gated on the master switch -- that governs task dispatch, and the frames
    bus is not part of it.

    Synchronous on purpose. ``publish_frame`` only packs the record and does a
    ``put_nowait``; a task per frame would cost more than the work it defers.
    """
    if not _user_plugins_enabled():
        return False
    image_b64 = (event or {}).get("image_base64")
    if not isinstance(image_b64, str) or not image_b64:
        return False
    try:
        from plugin.server.messaging.plane_bridge import build_frame_record, publish_frame

        generation = (event or {}).get("generation")
        record = build_frame_record(
            image_base64=image_b64,
            source=str((event or {}).get("source") or "unknown"),
            captured_at=(event or {}).get("captured_at"),
            turn_id=(event or {}).get("turn_id"),
            generation=int(generation) if isinstance(generation, (int, float)) else None,
            mime=str((event or {}).get("mime") or "image/jpeg"),
            lanlan_name=(event or {}).get("lanlan_name"),
            # The publisher's event_id is the frame's identity end to end, so a
            # puller can correlate a bus record with the session-side log line.
            frame_id=(event or {}).get("event_id"),
            metadata=(event or {}).get("metadata"),
        )
        return bool(publish_frame(record))
    except Exception as exc:
        logger.debug("[FrameBus] forward failed: %s", exc)
        return False


def _resolve_conversation_ts(event: Dict[str, Any]) -> float:
    """Prefer the producer's message time; fall back to the forward time."""
    raw = (event or {}).get("ts")
    if raw is None:
        return time.time()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return time.time()
    # Keep non-finite producer times out of consumer-side display sorting.
    return value if math.isfinite(value) else time.time()


def _forward_user_context(event: Dict[str, Any]) -> None:
    """Project user turns into the embedded plugin runtime's recent context.

    Main and agent can be separate processes. Only update the plugin bucket:
    redispatching all utterance sinks here would repeat memory side effects.
    The local write and this relay share identity and producer time so merged
    mode and repeated delivery cannot duplicate or refresh a user utterance.
    """
    if not _user_plugins_enabled() or event.get("turn_type") != "user_message":
        return
    content = event.get("content")
    metadata = event.get("metadata")
    if not isinstance(content, str) or not content.strip():
        return
    if not isinstance(metadata, dict) or not isinstance(metadata.get("is_voice"), bool):
        return
    # Do not convert a malformed or missing producer time into fresh activity.
    timestamp = event.get("ts")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        return
    try:
        from plugin.core.state import state

        character = event.get("lanlan_name")
        payload = {
            "type": "user_message",
            "event_id": event.get("event_id"),
            "_ts": timestamp,
            "content": content.strip(),
            "lanlan": character,
            "is_voice": metadata["is_voice"],
            "source": str(event.get("source") or "unknown"),
        }
        for bucket in dict.fromkeys(("default", character)):
            if isinstance(bucket, str) and bucket:
                state.add_user_context_event(bucket, payload)
    except Exception:
        # This observer must not interrupt the conversation bus or log text.
        logger.debug("[UserContext] projection failed")


def _forward_conversation_turn(event: Dict[str, Any]) -> bool:
    """Copy one already-handled conversation message into the ``conversations`` store.

    The text dual of :func:`_forward_provider_frame`, and it exists for the same
    reason: main_server owns the session but cannot write to the message plane,
    so the record arrives over the session PUB channel and this is its only hop
    onto the bus.

    Gated on user plugins being enabled, exactly like the frame hop: the store
    exists for plugins, and with them off a resident deque of what she said is
    retention that buys nothing.

    The record shape is dictated by ``ConversationRecord.from_raw`` /
    ``from_index`` on the reading side -- ``content`` at the top level,
    ``conversation_id`` / ``turn_type`` / ``lanlan_name`` / ``message_count``
    inside ``metadata``. ``conversation_id`` in particular has to be in
    ``metadata``: that is the only place ``TopicStore._extract_index`` looks
    for it, so putting it at the top level would drop it from the index a
    ``light=True`` read hands back.

    Synchronous on purpose, like the frame hop: ``publish_record`` only packs
    the record and does a ``put_nowait``.
    """
    if not _user_plugins_enabled():
        return False
    content = (event or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    try:
        from plugin.message_plane.stores import (
            CONVERSATIONS_STORE_NAME,
            CONVERSATIONS_TOPIC,
        )
        from plugin.server.messaging.plane_bridge import publish_record

        metadata_in = (event or {}).get("metadata")
        metadata: Dict[str, Any] = dict(metadata_in) if isinstance(metadata_in, dict) else {}
        # Absent stays absent rather than becoming ``""``: the reader turns an
        # empty string into an empty ``conversation_id``, which reads as "this
        # record belongs to a conversation whose id is blank" instead of "no id".
        conversation_id = str((event or {}).get("conversation_id") or "")
        if conversation_id:
            metadata["conversation_id"] = conversation_id
        metadata["turn_type"] = str((event or {}).get("turn_type") or "unknown")
        lanlan_name = (event or {}).get("lanlan_name")
        if lanlan_name:
            metadata["lanlan_name"] = str(lanlan_name)
        message_count = (event or {}).get("message_count")
        metadata["message_count"] = (
            int(message_count) if isinstance(message_count, (int, float)) else 0
        )
        metadata["ts"] = _resolve_conversation_ts(event)
        record: Dict[str, Any] = {
            "kind": "conversation",
            "type": "conversation_turn",
            "source": str((event or {}).get("source") or "unknown"),
            # The SDK cursor follows arrival time; producer time is display-only.
            "timestamp": time.time(),
            "content": content,
            "metadata": metadata,
        }
        for key in ("input_id", "reply_id"):
            value = (event or {}).get(key, metadata.get(key))
            if isinstance(value, str) and value:
                record[key] = value
        origin = (event or {}).get("origin", metadata.get("origin"))
        if isinstance(origin, dict):
            record["origin"] = dict(origin)
        producer_time = (event or {}).get("ts")
        if not isinstance(producer_time, bool):
            try:
                occurred_at = float(producer_time)
            except (TypeError, ValueError):
                pass
            else:
                if math.isfinite(occurred_at):
                    record["occurred_at"] = occurred_at
        # The publisher's event_id is this message's identity end to end, so a
        # puller can dedupe without unpacking, same as a frame.
        event_id = str((event or {}).get("event_id") or "")
        record["id"] = event_id or uuid.uuid4().hex
        publish_record(
            store=CONVERSATIONS_STORE_NAME,
            record=record,
            topic=CONVERSATIONS_TOPIC,
        )
        return True
    except Exception as exc:
        logger.debug("[ConversationBus] forward failed: %s", exc)
        return False


def _forward_conversation_fact(event: Dict[str, Any]) -> bool:
    """Forward an observed fact; never run plugin callbacks in this receive path."""
    if not _user_plugins_enabled():
        return False
    try:
        from plugin.server.lifecycle import get_conversation_service
        from plugin.server.messaging.bus_subscriptions import bus_subscription_manager

        payload = event.get("payload")
        if get_conversation_service(require_ready=False) is None or not isinstance(payload, dict):
            return False
        payload = dict(payload)
        payload["character"] = event.get("lanlan_name")
        payload["observed_at"] = time.time()
        bus_subscription_manager.accept_raw_conversation_fact(event.get("fact"), payload)
        return True
    except Exception:
        logger.debug("[ConversationBus] fact forwarding failed", exc_info=True)
        return False


def _resolve_session_language(session_language: Optional[str]) -> str:
    """Language for what the host writes for her: session locale first, process global as fallback.

    ``session_language`` rides the request from main_server and carries the
    frontend i18n truth as a full code (``zh-CN`` / ``zh-TW`` / ``ja`` …). It wins
    because this process cannot see it any other way: agent_server talks to
    main_server over ZeroMQ and HTTP, and its own ``get_global_language()`` only
    derives NEKO_LANGUAGE / Steam / system locale, which is simply wrong whenever
    the user's UI language differs from the machine's.

    Unvalidated input is fenced out first: the normalizers silently map anything
    unrecognized to ``'en'``, so a garbage value would look like a deliberate
    English choice and beat the (correct) fallback.

    Returns the code as given (the host maps it to a prompt-table key itself,
    keeping Traditional Chinese apart), or the process global's short code.
    """
    if session_language:
        try:
            from utils.language_utils import is_supported_language_code
            if is_supported_language_code(session_language):
                return str(session_language)
            logger.debug("[NekoClaw] ignoring unsupported session language: %r", session_language)
        except Exception:
            logger.debug("[NekoClaw] session language check failed", exc_info=True)
    from utils.result_parser import _get_lang
    return _get_lang(None)


# Turn-check requests already taken, by event id. The main server sends one
# again when its acknowledgement was slow; the check must not run twice.
_TURN_CHECKS_REMEMBERED = 256
_turn_checks_seen: "OrderedDict[str, None]" = OrderedDict()


async def _handle_turn_check_request(event: Dict[str, Any]) -> None:
    """Check a finished turn in which she called no task tool (design section 6.4).

    The host decides whether the turn is worth a model call at all, and what
    follows from the verdict. Nothing she or the user said is written to the log.
    """
    lanlan_name = event.get("lanlan_name")
    event_id = str(event.get("event_id") or "")
    if event_id:
        _create_tracked_task(_emit_main_event("turn_check_ack", lanlan_name, event_id=event_id))
        if event_id in _turn_checks_seen:
            return
        _turn_checks_seen[event_id] = None
        while len(_turn_checks_seen) > _TURN_CHECKS_REMEMBERED:
            _turn_checks_seen.popitem(last=False)
    if not nekoclaw_on() or not lanlan_name:
        return
    user_text = event.get("user_text")
    reply_text = event.get("reply_text")
    if not isinstance(user_text, str) or not isinstance(reply_text, str):
        return
    external_intent = event.get("external_intent")
    if isinstance(external_intent, bool) or not isinstance(external_intent, (int, float)):
        external_intent = None
    try:
        host = await ready_host()
        result = await host.check_turn(
            lanlan_name=str(lanlan_name),
            user_text=user_text,
            reply_text=reply_text,
            conversation_id=event.get("conversation_id"),
            lang=_resolve_session_language(event.get("language")),
            external_intent=external_intent,
        )
        logger.info(
            "[TurnCheck] lanlan=%s verdict=%s skipped=%s action=%s model_calls=%d",
            lanlan_name, result.verdict, result.skipped or "-", result.action or "-", result.model_calls,
        )
    except Exception as e:
        logger.warning("[TurnCheck] failed (exc_type=%s)", type(e).__name__)


async def _on_session_event(event: Dict[str, Any]) -> None:
    event_type = (event or {}).get("event_type")
    if event_type == "agent_intent_restore_signal":
        # First-real-client-session signal from main_server (sent on
        # ``greeting_check``). Restore persisted agent runtime intent now
        # — agent_server is fully ready (we're already receiving events),
        # but we delayed restore to here so we don't trigger LLM probes
        # and plugin lifecycle startup during the cold-start window
        # before the user actually opens a session. The restore helper
        # has its own once-flag, so this is safe to spam.
        # Late import avoids the runtime -> routes initialization cycle while
        # preserving the original call point and shared restore-state owner.
        from .api_routes import _maybe_restore_agent_intent
        await _maybe_restore_agent_intent()
        return
    if event_type in {"voice_transcript_observed", "voice_transcript_request"}:
        _create_tracked_task(_handle_voice_transcript_request(event))
        return
    if event_type == "provider_frame_observed":
        _forward_provider_frame(event)
        return
    if event_type == "conversation_turn_observed":
        _forward_user_context(event)
        _forward_conversation_turn(event)
        return
    if event_type == "conversation_fact_observed":
        _forward_conversation_fact(event)
        return
    if event_type == "turn_check_request":
        _create_tracked_task(_handle_turn_check_request(event))


async def _settle_region() -> None:
    """Let the region check finish in the background.

    The decision model and task agents read their endpoints from the
    configuration each time they are used, so nothing here has to wait for
    the region before it is built. A standalone agent_server does not get
    main_server's warmup, so it is started here; it must not hold up startup.
    """
    try:
        if not await get_config_manager().awarmup_region_check():
            logger.warning("[GeoIP] Agent 进程启动后区域判定仍未落定，暂按大陆线路")
    except Exception as e:
        logger.warning(f"[GeoIP] Agent 进程区域判定失败，按当前配置继续: {e}")


# Stall watchdog for this process's event loop. In merged mode the loop is
# shared with main_server, which has usually started it already; the start
# helper then hands back that same watchdog instead of a second one.
_loop_stall_watchdog = None


@app.on_event("startup")
async def startup():
    global _loop_stall_watchdog
    # Started first so the rest of startup is covered; returns immediately.
    if _loop_stall_watchdog is None:
        try:
            from utils.event_loop_watchdog import start_loop_stall_watchdog_from_env

            _loop_stall_watchdog = start_loop_stall_watchdog_from_env(
                asyncio.get_running_loop(), logger=logger, label="agent_server"
            )
        except Exception as e:
            logger.warning(f"[loop-stall] watchdog start failed: {e}")

    # Install token tracking hooks for this process
    try:
        from utils.token_tracker import TokenTracker, install_hooks
        install_hooks()
        TokenTracker.get_instance().start_periodic_save()
        # Include process in session_start/session_end dimensions to distinguish processes during diagnostics
        TokenTracker.get_instance().record_app_start(process="agent_server")
    except Exception as e:
        logger.warning(f"[Agent] Token tracker init failed: {e}")

    # Note: main_server triggers all module warming after runtime initialization (see the end of
    # _ensure_main_server_runtime_initialized). In merged mode all three apps share a process,
    # so that site covers all lazy modules; do not start another warmup here and compete for the startup GIL.

    os.environ["NEKO_PLUGIN_HOSTED_BY_AGENT"] = "true"
    from .model_probe import AgentModelProbe

    Modules.model_probe = AgentModelProbe()
    Modules.throttled_logger = ThrottledLogger(logger, interval=30.0)
    _region_task = asyncio.create_task(_settle_region())
    Modules._persistent_tasks.add(_region_task)
    _region_task.add_done_callback(Modules._persistent_tasks.discard)

    try:
        await _start_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Failed to start embedded user plugin server: {e}")
    # No browser is found or started here: that happens when a task agent
    # first calls a browser tool.

    # The keyboard-and-mouse and browser tools are driven by task agents on
    # the agent LLM — default to "not connected" and probe in background.
    # The single check updates both capability caches.
    _set_capability("computer_use", False, "connectivity check pending")
    _set_capability("browser_use", False, "connectivity check pending")
    # Plugin capability = ready (embedded HTTP server is always up), but lifecycle
    # is NOT started here — it syncs with user_plugin_enabled (default OFF).
    # The lifecycle starts on-demand when the user toggles the plugin flag ON.
    _set_capability("user_plugin", True, "")
    _llm_probe_task = asyncio.create_task(_fire_agent_llm_connectivity_check())
    Modules._persistent_tasks.add(_llm_probe_task)
    _llm_probe_task.add_done_callback(Modules._persistent_tasks.discard)

    # NekoClaw host (task brain, task agents, capability gateway). This only tells
    # it how to read this process's switches and what to call back; it is built
    # on the first dispatch. Must stay free of I/O: see the startup path rules.
    mount_nekoclaw_host(_plugin_list_change_token)

    # Start ZeroMQ bridge for main_server events
    try:
        Modules.agent_bridge = AgentServerEventBridge(on_session_event=_on_session_event)
        await Modules.agent_bridge.start()
    except Exception as e:
        logger.warning(f"[Agent] Event bridge startup failed: {e}")
    # Free Agent daily quota exhausted → throttle frontend prompts to at most once every 10 seconds.
    # consume_agent_daily_quota invokes this callback from a worker; run_coroutine_threadsafe schedules
    # asynchronous ZeroMQ emission on agent_server's event loop without .result(), preserving nonblocking execution.
    try:
        _quota_notify_loop = asyncio.get_running_loop()

        def _notify_agent_quota_exceeded(used: int, limit: int) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    _emit_main_event("agent_quota_exceeded", None, used=used, limit=limit),
                    _quota_notify_loop,
                )
            except Exception as e:
                logger.debug("[Agent] schedule agent_quota_exceeded emit failed: %s", e)

        get_config_manager().register_quota_exceeded_notifier(_notify_agent_quota_exceeded)
    except Exception as e:
        logger.warning(f"[Agent] register quota-exceeded notifier failed: {e}")
    # Push initial server status so frontend can render Agent popup without waiting.
    _bump_state_revision()


def _kill_browser_at_last() -> None:
    """Kill the browser the tools started, when asking it to close has run out of time; never raises.

    For a shutdown and for ``end_all``. Blocking: runs on a thread of its own.
    """
    try:
        builtin_provider.kill_browser()
    except Exception as e:
        logger.warning(f"[Agent] Killing the browser failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Gracefully stop running tasks and release async resources."""
    global _loop_stall_watchdog
    logger.info("[Agent] Shutdown initiated — stopping running tasks")
    from config.nekoclaw_settings import BROWSER_CLOSE_AT_SHUTDOWN_SECONDS, BROWSER_KILL_AT_SHUTDOWN_SECONDS

    # Set first: whatever the steps below take, and whether or not the event
    # loop gets round to them, the browser the tools started does not outlive
    # this process (see ``BROWSER_KILL_AT_SHUTDOWN_SECONDS``). Cancelled once
    # the browser has been closed.
    browser_killed_at = asyncio.get_running_loop().time() + BROWSER_KILL_AT_SHUTDOWN_SECONDS
    browser_last_resort = threading.Timer(BROWSER_KILL_AT_SHUTDOWN_SECONDS, _kill_browser_at_last)
    browser_last_resort.daemon = True
    browser_last_resort.start()
    # Release first so the synchronous cleanup below is not reported as stalls.
    if _loop_stall_watchdog is not None:
        try:
            _loop_stall_watchdog.stop()
        except Exception as e:
            logger.debug(f"[loop-stall] watchdog stop failed: {e}")
        _loop_stall_watchdog = None

    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        pass

    # The host first, while the plugin server it cancels runs on and the
    # bridge it reports through are still up. Bounded: see
    # HOST_SHUTDOWN_BUDGET_SECONDS. A no-op when nothing ever built the host.
    await shutdown_nekoclaw_host()

    for t in list(Modules._persistent_tasks):
        if not t.done():
            t.cancel()

    try:
        await _ensure_plugin_lifecycle_stopped()
    except Exception as e:
        logger.warning(f"[Agent] Plugin lifecycle cleanup error: {e}")

    try:
        await _stop_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Embedded user plugin server cleanup error: {e}")

    bridge = Modules.agent_bridge
    if bridge is not None:
        try:
            await bridge.stop()
            Modules.agent_bridge = None
            logger.debug("[Agent] ✅ ZMQ event bridge cleaned up")
        except Exception as e:
            logger.warning("[Agent] ⚠️ ZMQ event bridge cleanup error: %s", e)

    all_tasks = list(Modules._persistent_tasks) + list(Modules._background_tasks)
    tasks_to_await = [t for t in all_tasks if not t.done()]
    for t in tasks_to_await:
        t.cancel()
    if tasks_to_await:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_await, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[Agent] ⚠️ 部分后台任务取消超时")
    Modules._persistent_tasks.clear()
    Modules._background_tasks.clear()

    # The browser the tools started stays open between tasks; it is closed
    # at service shutdown, along with keys a stopped call left held. The
    # host's shutdown above has cancelled every call that was under way.
    # The launcher ends this process a few seconds after it began to shut
    # down, and a browser that is still closing then would run on with
    # nobody to close it. It is asked to close within what is left before
    # the time it is killed at, and killed when it has not; with nothing
    # left (the steps above took it all) it is not asked at all.
    left = min(BROWSER_CLOSE_AT_SHUTDOWN_SECONDS, browser_killed_at - asyncio.get_running_loop().time())
    closed_in_time = False
    if left > 0:
        try:
            await asyncio.wait_for(_close_browser(), timeout=left)
            closed_in_time = True
        except asyncio.TimeoutError:
            pass  # Killed just below, like one there was no time left to ask.
    if not closed_in_time:
        logger.warning("[Agent] The browser did not close in time; killing it")
    elif builtin_provider.browser_unclosed():
        # It was asked in time and closing it failed. The browser is then
        # kept to be closed the next time, and at a shutdown there is none.
        closed_in_time = False
        logger.warning("[Agent] The browser could not be closed; killing it")
    if not closed_in_time:
        try:
            await asyncio.to_thread(builtin_provider.kill_browser)
        except Exception as e:
            logger.warning(f"[Agent] Killing the browser failed: {e}")
    browser_last_resort.cancel()
    # Only the keys now. The browser has been dealt with: asked to close once
    # more (as ``aclose`` would), a browser that was just killed would be
    # waited for all over again.
    try:
        await asyncio.wait_for(builtin_provider.release_keys(), timeout=2.0)
    except Exception as e:
        logger.warning(f"[Agent] Built-in tools cleanup error: {e}")

    logger.info("[Agent] Shutdown cleanup complete")
    await _emit_agent_status_update()


@app.get("/health")
async def health():
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response(
        "agent",
        instance_id=INSTANCE_ID,
        extra={"agent_flags": Modules.agent_flags},
    )


# ---------------------------------------------------------------------------
# The main dialogue's task tools
# ---------------------------------------------------------------------------
# main_server and this server are separate event loops (separate processes in
# a source run), so her two tools reach the host through these routes.


def _disabled_receipt(lang: str) -> Dict[str, Any]:
    from nekoclaw.host.task_brain import Receipt

    receipt = Receipt(kind="no_capability", reason="disabled")
    return {
        "success": True,
        "kind": receipt.kind,
        "reason": receipt.reason,
        "text": receipt.text(lang),
        "commission_id": None,
        "model_calls": 0,
    }


@app.post("/nekoclaw/dispatch")
async def nekoclaw_dispatch(payload: Dict[str, Any]):
    """Hand one sentence of hers to the task brain and return the receipt of its first round."""
    text = (payload or {}).get("text")
    lanlan_name = (payload or {}).get("lanlan_name")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "text required")
    if not isinstance(lanlan_name, str) or not lanlan_name:
        raise HTTPException(400, "lanlan_name required")
    lang = _resolve_session_language((payload or {}).get("language"))
    if not nekoclaw_on():
        # Nothing is built for a switch that is off.
        return _disabled_receipt(lang)
    user_text = (payload or {}).get("user_text")
    conversation_id = (payload or {}).get("conversation_id")
    host = await ready_host()
    receipt = await host.dispatch(
        text,
        lanlan_name=lanlan_name,
        user_text=user_text if isinstance(user_text, str) else "",
        conversation_id=conversation_id if isinstance(conversation_id, str) and conversation_id else None,
        lang=lang,
        source="dispatch",
    )
    logger.info(
        "[NekoClaw] dispatch: lanlan=%s kind=%s reason=%s model_calls=%d",
        lanlan_name, receipt.kind, receipt.reason or "-", receipt.model_calls,
    )
    return {
        "success": True,
        "kind": receipt.kind,
        "reason": receipt.reason,
        "text": receipt.text(lang),
        "commission_id": receipt.commission_id,
        "model_calls": receipt.model_calls,
    }


@app.post("/nekoclaw/query")
async def nekoclaw_query(payload: Dict[str, Any]):
    """Answer what is going on, or whether something can be done. No model is asked."""
    lanlan_name = (payload or {}).get("lanlan_name")
    if not isinstance(lanlan_name, str) or not lanlan_name:
        raise HTTPException(400, "lanlan_name required")
    text = (payload or {}).get("text")
    lang = _resolve_session_language((payload or {}).get("language"))
    if not nekoclaw_on():
        return {"success": True, "text": _disabled_receipt(lang)["text"], "can_do": False}
    host = await ready_host()
    answer = await host.query(text if isinstance(text, str) else "", lanlan_name=lanlan_name, lang=lang)
    return {"success": True, "text": answer.text, "can_do": answer.can_do}


@app.get("/nekoclaw/brief")
async def nekoclaw_brief(lanlan_name: str, language: Optional[str] = None):
    """Return what is under way, one line each, for a new session's opening prompt.

    Called whenever a session starts, so it stays cheap: with the switch off,
    or with no ledger file and no host built yet, nothing is built and nothing
    is opened.
    """
    if not nekoclaw_on() or not lanlan_name:
        return {"success": True, "lines": []}
    if peek_host() is None and not await ledger_exists():
        return {"success": True, "lines": []}
    host = await ready_host()
    lines = await host.brief(lanlan_name=lanlan_name, lang=_resolve_session_language(language))
    return {"success": True, "lines": lines}


# ---------------------------------------------------------------------------
# Tasks, as the HUD sees them
# ---------------------------------------------------------------------------


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    await restore_hud()
    task = hud.get(task_id)
    if task is not None:
        return task
    raise HTTPException(404, "task not found")


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Stop one task.

    The status is written first, so nothing that is still running can change
    it afterwards; then the plugin run is cancelled on the plugin server or
    the task agent's session is closed. She is told that it was stopped.
    """
    await restore_hud()
    host = peek_host()
    task = hud.get(task_id)
    if host is None or task is None:
        raise HTTPException(404, "task not found")
    if task.get("status") not in HUD_ACTIVE:
        # Include the real terminal status so the HUD's local fallback can
        # mirror it instead of mislabeling the card "cancelled".
        return {"success": False, "error": "task is not active", "status": task.get("status")}
    commission = await host.abort(task_id, reason="Cancelled by user")
    if commission is None:
        raise HTTPException(404, "task not found")
    current = hud.get(task_id) or task
    if commission.status != "cancelled":
        return {"success": False, "error": "task is not active", "status": current.get("status")}
    logger.info("[Agent] Task %s (%s) cancelled by user", task_id, task.get("type"))
    return {"success": True, "task_id": task_id, "status": "cancelled"}


@app.get("/builtin/activity")
async def builtin_activity():
    """Return which commission is using the keyboard and mouse, and which the browser, right now.

    The same payloads the ``builtin_activity`` events carry, for a display
    that starts late and missed them.
    """
    from nekoclaw.host.capabilities import BUILTIN_GROUPS

    from .nekoclaw_builtins import activity_payload, occupant

    items = []
    for group in BUILTIN_GROUPS:
        commission_id = occupant(group)
        if commission_id:
            items.append(activity_payload(group, "started", commission_id))
    return {"activity": items}


@app.post("/builtin/stop")
async def stop_builtin(payload: Optional[Dict[str, Any]] = None):
    """Stop the commission that is using the keyboard and mouse or the browser.

    What the Esc key of the banner calls. ``group`` names one of the two;
    without it both are stopped. Stopping is what cancelling the task from
    the task window does: the status is written first, the call under way is
    cancelled, and the desktop or the browser is given back.
    """
    from nekoclaw.host.capabilities import BUILTIN_GROUPS

    from .nekoclaw_builtins import occupant

    group = (payload or {}).get("group")
    if group is not None and group not in BUILTIN_GROUPS:
        raise HTTPException(400, "unknown group")
    host = peek_host()
    stopped = []
    for name in ([group] if group else list(BUILTIN_GROUPS)):
        commission_id = occupant(name)
        if not commission_id or commission_id in stopped or host is None:
            continue
        commission = await host.abort(commission_id, reason="Stopped with Esc")
        if commission is not None and commission.status == "cancelled":
            stopped.append(commission_id)
            logger.info("[Agent] Task %s stopped with Esc (%s)", commission_id, name)
    return {"success": True, "stopped": stopped}


def _browser_settings_view() -> Dict[str, Any]:
    """Return the browser settings with the browsers found on this computer. Blocking (file checks)."""
    from nekoclaw.builtin.browser.launcher import find_browsers
    from nekoclaw.builtin.browser.settings import MODES, BrowserSettingsStore

    store = BrowserSettingsStore()
    return {
        "settings": store.load().to_json(),
        "modes": list(MODES),
        "browsers": [
            {"kind": install.kind, "name": install.name, "executable": install.executable}
            for install in find_browsers()
        ],
    }


@app.get("/builtin/browser/settings")
async def get_browser_settings():
    """Return which browser the browser tools use and how; for the settings page of the console."""
    return await asyncio.to_thread(_browser_settings_view)


@app.post("/builtin/browser/settings")
async def set_browser_settings(payload: Dict[str, Any]):
    """Change which browser the browser tools use and how.

    ``mode`` is ``own_profile`` (the user's browser with a profile directory
    of its own) or ``attach`` (the browser the user is using, which they
    allow in that browser). The change applies to the next browser that is
    started: one that is open is closed, so the next call starts anew.
    """
    from nekoclaw.builtin.browser.launcher import is_browser_executable
    from nekoclaw.builtin.browser.settings import MODES, BrowserSettings, BrowserSettingsStore

    payload = payload or {}

    def apply() -> Dict[str, Any]:
        store = BrowserSettingsStore()
        current = store.load()
        mode = payload.get("mode", current.mode)
        if mode not in MODES:
            raise ValueError("unknown mode")
        executable = payload.get("executable", current.executable)
        if executable is not None and (not isinstance(executable, str) or not executable.strip()):
            executable = None
        if executable is not None and not os.path.isfile(executable):
            raise ValueError("executable not found")
        if executable is not None and not is_browser_executable(executable):
            # It is started with a debugging port: not any program will do.
            raise ValueError("executable is not a browser the tools can drive (Chrome, Edge, Brave or Chromium)")
        headless = payload.get("headless", current.headless)
        store.save(BrowserSettings(mode=mode, executable=executable, headless=headless is True))
        return _browser_settings_view()

    try:
        view = await asyncio.to_thread(apply)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # A browser named in the settings may be one the search does not find,
    # or the one it found may have been set aside: looked for afresh.
    from .capabilities import _browser_dependency_status, _forget_browser_search

    _forget_browser_search()
    _create_tracked_task(_close_browser(update_capability=False))
    # With a model that answers, whether there is a browser to drive is all
    # the browser tools depend on, and that may just have changed: said at
    # once. Left as it was (no browser found, from before one was named
    # here), the tools would stay refused until something asked again. With
    # a model that has not answered, the check that follows its answer says;
    # and with none configured any more (the probe of the one before may
    # still say it answered) nothing is said for it, as in
    # ``/browser_use/availability``.
    probe = Modules.model_probe
    # In a worker thread: it reads the configuration from the disk.
    configured = (await asyncio.to_thread(_check_agent_api_gate)).get("ready") is True
    if configured and probe is not None and getattr(probe, "init_ok", False):
        dependency_ready, dependency_error = await asyncio.to_thread(_browser_dependency_status)
        _set_capability("browser_use", dependency_ready, "" if dependency_ready else dependency_error)
        _bump_state_revision()
        await _emit_agent_status_update()
    return {"success": True, **view}


@app.post("/api/agent/tasks/{task_id}/complete")
async def complete_deferred_task(task_id: str):
    """Callback for a plugin: its deferred work for this task is done.

    ``task_id`` is the ``agent_task_id`` the plugin was given through its
    ``bind_task`` entry.
    """
    if peek_host() is None and not await ledger_exists():
        raise HTTPException(status_code=404, detail="Task not found")
    host = await ready_host()
    commission = await host.ledger.get(task_id)
    if commission is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if commission.status != "working":
        # Already ended: return idempotently
        return {"ok": True, "skipped": True, "status": (hud_status(commission.status))}
    if commission.route != "plugin":
        raise HTTPException(status_code=403, detail="Only plugin tasks can be completed via this endpoint")
    if commission.deferred_until is None:
        raise HTTPException(status_code=400, detail="Not a deferred task - use normal completion flow")
    if not await host.complete_deferred(task_id):
        latest = await host.ledger.get(task_id)
        return {"ok": True, "skipped": True, "status": hud_status(latest.status if latest else "failed")}
    logger.info("[Deferred] Task %s marked completed via callback", task_id)
    return {"ok": True}


def hud_status(status: str) -> str:
    """Return a commission's status in the words the task endpoints have always used."""
    from .nekoclaw_mount import _HUD_STATUS

    return _HUD_STATUS.get(status, status)
