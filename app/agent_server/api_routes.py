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

"""Agent control and intent-restore endpoints."""

from .api_shared import (  # noqa: F401
    Any,
    Dict,
    HTTPException,
    List,
    Modules,
    Optional,
    USER_PLUGIN_SERVER_PORT,
    _agent_flags_snapshot,
    _browser_dependency_status,
    _bump_state_revision,
    _close_browser,
    _collect_agent_status_snapshot,
    _create_tracked_task,
    _emit_agent_status_update,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _fire_agent_llm_connectivity_check,
    _keyboard_mouse_availability,
    _model_images_status,
    _probe_lost_a_race,
    _set_capability,
    _try_refresh_model_probe,
    app,
    asyncio,
    builtin_provider,
    httpx,
    hud,
    json,
    logger,
    os,
    restore_hud,
    stop_everything,
    sys,
    time,
    uuid,
)
from .api_runtime import (  # noqa: F401
    _agent_master_enabled,
    _check_agent_api_gate,
    _handle_voice_transcript_request,
    _on_session_event,
    _user_plugins_enabled,
    _voice_transcript_plugin_gate_reason,
    cancel_task,
    complete_deferred_task,
    get_task,
    health,
    nekoclaw_brief,
    nekoclaw_dispatch,
    nekoclaw_query,
    shutdown,
    startup,
)


@app.get("/agent/flags")
async def get_agent_flags():
    """Get the current agent flags state (for frontend sync)"""
    note = Modules.notification
    # Read-once notification
    if Modules.notification:
        Modules.notification = None

    return {
        "success": True,
        "agent_flags": _agent_flags_snapshot(),
        "analyzer_enabled": Modules.analyzer_enabled,
        "agent_api_gate": _check_agent_api_gate(),
        "revision": Modules.state_revision,
        "notification": note
    }


@app.get("/agent/state")
async def get_agent_state():
    # A page opened right after a restart finds what was left under way.
    await restore_hud()
    snapshot = _collect_agent_status_snapshot()
    return {"success": True, "snapshot": snapshot}


_USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS = 4
_USER_PLUGIN_REGISTRY_CHECK_INTERVAL_S = 0.75
_USER_PLUGIN_REGISTRY_CHECK_TIMEOUT_S = 8.0
_USER_PLUGIN_REGISTRY_CHECK_MAX_DURATION_S = 12.0
_USER_PLUGIN_EMPTY_CONFIRMATIONS = 3


async def _check_user_plugin_registry_readiness() -> Dict[str, Any]:
    """Classify the embedded plugin registry without conflating failures with emptiness.

    ``GET /plugins`` is deliberately richer than a health endpoint: it may wait
    for runtime-state locks and render metadata/i18n for every registered
    plugin.  A short client timeout therefore means "registry unavailable",
    not "there are no plugins".  Only repeated, structurally valid HTTP 200
    responses containing an empty plugin list are authoritative enough to
    return ``empty``.
    """
    empty_responses = 0
    last_error = "no valid response"
    timeout = httpx.Timeout(
        _USER_PLUGIN_REGISTRY_CHECK_TIMEOUT_S,
        connect=min(2.0, _USER_PLUGIN_REGISTRY_CHECK_TIMEOUT_S),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            async with asyncio.timeout(_USER_PLUGIN_REGISTRY_CHECK_MAX_DURATION_S):
                for attempt in range(_USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS):
                    await asyncio.sleep(_USER_PLUGIN_REGISTRY_CHECK_INTERVAL_S)
                    try:
                        response = await client.get(
                            f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"
                        )
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                        logger.warning(
                            "[Agent] UserPlugin registry check %d/%d failed: %s",
                            attempt + 1,
                            _USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS,
                            last_error,
                        )
                        continue

                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        logger.warning(
                            "[Agent] UserPlugin registry check %d/%d returned %s",
                            attempt + 1,
                            _USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS,
                            last_error,
                        )
                        continue

                    try:
                        payload = response.json()
                    except Exception as exc:
                        last_error = f"invalid JSON ({type(exc).__name__})"
                        logger.warning(
                            "[Agent] UserPlugin registry check %d/%d returned %s",
                            attempt + 1,
                            _USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS,
                            last_error,
                        )
                        continue

                    plugins = payload.get("plugins") if isinstance(payload, dict) else None
                    if not isinstance(plugins, list):
                        last_error = "invalid plugins payload"
                        logger.warning(
                            "[Agent] UserPlugin registry check %d/%d returned %s",
                            attempt + 1,
                            _USER_PLUGIN_REGISTRY_CHECK_ATTEMPTS,
                            last_error,
                        )
                        continue

                    if plugins:
                        return {
                            "status": "ready",
                            "plugin_count": len(plugins),
                            "empty_responses": empty_responses,
                            "last_error": "",
                        }

                    empty_responses += 1
                    last_error = "empty plugin list"
                    if empty_responses >= _USER_PLUGIN_EMPTY_CONFIRMATIONS:
                        return {
                            "status": "empty",
                            "plugin_count": 0,
                            "empty_responses": empty_responses,
                            "last_error": last_error,
                        }
    except TimeoutError:
        last_error = (
            "overall timeout "
            f"({_USER_PLUGIN_REGISTRY_CHECK_MAX_DURATION_S:g}s)"
        )
        logger.warning("[Agent] UserPlugin registry check reached %s", last_error)
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("[Agent] UserPlugin registry client failed: %s", last_error)

    return {
        "status": "unavailable",
        "plugin_count": 0,
        "empty_responses": empty_responses,
        "last_error": last_error,
    }


@app.post("/agent/flags")
async def set_agent_flags(payload: Dict[str, Any]):
    lanlan_name = (payload or {}).get("lanlan_name")
    cf = (payload or {}).get("computer_use_enabled")
    bf = (payload or {}).get("browser_use_enabled")
    uf = (payload or {}).get("user_plugin_enabled")
    # ``_persist_intent`` (default True) gates whether this call writes the
    # user's intent to ``agent_runtime_intent.json``. The restore path replays
    # past intents through this same function with ``_persist_intent=False``
    # so the replay doesn't re-write what it's reading.
    persist_intent = bool((payload or {}).get("_persist_intent", True))
    # Agent API gate: if any agent sub-feature is being enabled, gate must pass.
    gate = _check_agent_api_gate()
    changed = False
    old_flags = dict(Modules.agent_flags)
    old_analyzer_enabled = bool(Modules.analyzer_enabled)
    browser_use_close_reason: Optional[str] = None
    browser_use_lifecycle_seq = Modules.browser_use_lifecycle_seq
    if isinstance(bf, bool):
        Modules.browser_use_lifecycle_seq += 1
        browser_use_lifecycle_seq = Modules.browser_use_lifecycle_seq
    user_plugin_lifecycle_seq = Modules.user_plugin_lifecycle_seq
    if isinstance(uf, bool):
        Modules.user_plugin_lifecycle_seq += 1
        user_plugin_lifecycle_seq = Modules.user_plugin_lifecycle_seq
    # Agent LLM gate fail (endpoint/key not configured) blocks **only** the
    # two LLM-dependent sub flags. ``user_plugin_enabled`` runs entirely on
    # the plugin lifecycle (no agent LLM involved) so the gate must not
    # short-circuit its toggle path — historically this branch reset every
    # flag and early-returned, which silently swallowed legitimate user_plugin
    # enable/disable requests whenever the user hadn't configured an agent
    # endpoint. Here we instead cancel just the LLM-coupled requests by
    # nullifying them, then fall through to the per-flag handling so uf still
    # processes normally.
    if gate.get("ready") is not True and any(x is True for x in (cf, bf)):
        if not isinstance(bf, bool) and old_flags.get("browser_use_enabled", False):
            Modules.browser_use_lifecycle_seq += 1
            browser_use_lifecycle_seq = Modules.browser_use_lifecycle_seq
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.agent_flags["browser_use_enabled"] = False
        first_reason = (gate.get('reasons') or ['AGENT_ENDPOINT_NOT_CONFIGURED'])[0]
        browser_use_close_reason = first_reason
        _set_capability("computer_use", False, first_reason)
        _set_capability("browser_use", False, first_reason)
        # Swallow these requests so the per-flag handlers below don't re-toggle
        # them ON; ``uf`` is intentionally left alone so user_plugin processing
        # proceeds.
        cf = bf = None

    prev_up = Modules.agent_flags.get("user_plugin_enabled", False)

    # 1. Handle Computer Use Flag with Capability Check
    if isinstance(cf, bool):
        if cf: # Attempting to enable
            if not Modules.model_probe:
                _try_refresh_model_probe(force=True)
            if not Modules.model_probe:
                Modules.agent_flags["computer_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_CU_MODULE_NOT_LOADED"})
                logger.warning("[Agent] Cannot enable Computer Use: Module not loaded")
            elif not getattr(Modules.model_probe, "init_ok", False):
                Modules.agent_flags["computer_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_CU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                try:
                    avail = await asyncio.to_thread(_keyboard_mouse_availability)
                    reasons = avail.get('reasons', []) if isinstance(avail, dict) else []
                    _set_capability("computer_use", bool(avail.get("ready")) if isinstance(avail, dict) else False, reasons[0] if reasons else "")
                    if avail.get("ready"):
                        Modules.agent_flags["computer_use_enabled"] = True
                    else:
                        Modules.agent_flags["computer_use_enabled"] = False
                        reason = avail.get('reasons', [])[0] if avail.get('reasons') else 'unknown'
                        Modules.notification = json.dumps({"code": "AGENT_CU_UNAVAILABLE", "details": {"reason_code": reason}})
                        logger.warning(f"[Agent] Cannot enable Computer Use: {avail.get('reasons')}")
                except Exception as e:
                    Modules.agent_flags["computer_use_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_CU_ENABLE_FAILED", "details": {"error": str(e)}})
                    logger.error(f"[Agent] Cannot enable Computer Use: Check failed {e}")
        else: # Disabling
            Modules.agent_flags["computer_use_enabled"] = False

    # 2.5. Handle Browser Use Flag with Capability Check
    if isinstance(bf, bool):
        if bf:
            dependency_ready, dependency_error = await asyncio.to_thread(_browser_dependency_status)
            if not dependency_ready:
                Modules.agent_flags["browser_use_enabled"] = False
                _set_capability("browser_use", False, dependency_error)
                Modules.notification = json.dumps({"code": "AGENT_BU_UNAVAILABLE", "details": {"reason_code": dependency_error}})
            elif not getattr(Modules.model_probe, "init_ok", False):
                Modules.agent_flags["browser_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_BU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                Modules.agent_flags["browser_use_enabled"] = True
                _set_capability("browser_use", True, "")
        else:
            Modules.agent_flags["browser_use_enabled"] = False

    # Explicit disable and automatic gate demotion both close the browser
    # the tools started, at once.
    if bf is False or (
        old_flags.get("browser_use_enabled", False)
        and not Modules.agent_flags.get("browser_use_enabled", False)
    ):
        _create_tracked_task(
            _close_browser(
                capability_reason=browser_use_close_reason,
                expected_lifecycle_seq=browser_use_lifecycle_seq,
            )
        )

    if isinstance(uf, bool):
        if uf:  # Attempting to enable UserPlugin — non-blocking (like CUA)
            Modules.agent_flags["user_plugin_enabled"] = True
            Modules.notification = json.dumps({"code": "AGENT_UP_ENABLED_CHECKING"})

            async def _bg_plugin_enable():
                _ln = lanlan_name

                def _owns_generation() -> bool:
                    return user_plugin_lifecycle_seq == Modules.user_plugin_lifecycle_seq

                def _may_start_generation() -> bool:
                    return (
                        _owns_generation()
                        and Modules.analyzer_enabled
                        and Modules.agent_flags.get("user_plugin_enabled", False)
                    )

                try:
                    if not Modules.analyzer_enabled:
                        Modules.notification = ""
                        _set_capability("user_plugin", True, "")
                        logger.info(
                            "[Agent] UserPlugin enable deferred until the master switch is ON"
                        )
                        return

                    started = await _ensure_plugin_lifecycle_started(
                        should_start=_may_start_generation
                    )
                    if not _owns_generation():
                        return
                    if not Modules.analyzer_enabled:
                        return
                    if not started:
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                        logger.warning("[Agent] Cannot enable UserPlugin: lifecycle startup failed")
                        return

                    registry = await _check_user_plugin_registry_readiness()
                    if not _owns_generation():
                        return

                    registry_status = registry.get("status")
                    if registry_status == "empty":
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_NO_PLUGINS_FOUND"})
                        logger.warning(
                            "[Agent] Cannot enable UserPlugin: registry confirmed empty "
                            "(%d valid empty responses)",
                            registry.get("empty_responses", 0),
                        )
                        await _ensure_plugin_lifecycle_stopped(should_stop=_owns_generation)
                    elif registry_status == "ready":
                        _set_capability("user_plugin", True, "")
                        logger.info(
                            "[Agent] UserPlugin lifecycle ready (%d plugins)",
                            registry.get("plugin_count", 0),
                        )
                    else:
                        # A slow or temporarily unreachable registry is not an
                        # authoritative empty registry.  Preserve the user's ON
                        # intent so the provider can recover on a later request.
                        _set_capability("user_plugin", True, "")
                        Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                        logger.warning(
                            "[Agent] UserPlugin registry unavailable within %.1fs; "
                            "keeping user_plugin_enabled ON for recovery "
                            "(empty_responses=%d, last_error=%s)",
                            _USER_PLUGIN_REGISTRY_CHECK_MAX_DURATION_S,
                            registry.get("empty_responses", 0),
                            registry.get("last_error", "unknown"),
                        )
                except Exception as exc:
                    if _owns_generation():
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                        logger.error("[Agent] Background plugin enable failed: %s", exc)
                finally:
                    if _owns_generation():
                        _bump_state_revision()
                        await _emit_agent_status_update(lanlan_name=_ln)

            _bg = asyncio.create_task(_bg_plugin_enable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)
        else:  # Disabling UserPlugin — non-blocking
            Modules.agent_flags["user_plugin_enabled"] = False
            _set_capability("user_plugin", True, "")

            async def _bg_plugin_disable():
                def _owns_disabled_generation() -> bool:
                    return (
                        user_plugin_lifecycle_seq == Modules.user_plugin_lifecycle_seq
                        and not Modules.agent_flags.get("user_plugin_enabled", False)
                    )

                if not _owns_disabled_generation():
                    return
                try:
                    await _ensure_plugin_lifecycle_stopped(should_stop=_owns_disabled_generation)
                except Exception as exc:
                    logger.warning("[Agent] Background plugin disable error: %s", exc)

            _bg = asyncio.create_task(_bg_plugin_disable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)

    try:
        new_up = Modules.agent_flags.get("user_plugin_enabled", False)
        if prev_up != new_up:
            logger.info("[Agent] user_plugin_enabled toggled %s via /agent/flags", "ON" if new_up else "OFF")
    except Exception:
        pass

    # Persist user intent for each explicitly-requested flag.
    # Rule: a flag is persisted only when the user's request actually took
    # effect in-memory. If the user requested ON but capability auto-rejected
    # (LLM unreachable, module not loaded, etc.), the in-memory flag stays
    # False — we do NOT persist a True intent for that case, because the
    # toggle visibly didn't take. Disable requests (False) are always
    # persisted faithfully (no capability check involved).
    # The capability-auto-disable path inside
    # ``_fire_agent_llm_connectivity_check`` also intentionally does NOT
    # touch intent — it flips the in-memory flag but leaves persisted intent
    # so a transient LLM blip doesn't wipe the user's preference.
    if persist_intent:
        try:
            from app.agent_runtime_intent import set_intent
            for key, requested in (
                ("computer_use_enabled", cf),
                ("browser_use_enabled", bf),
                ("user_plugin_enabled", uf),
            ):
                if not isinstance(requested, bool):
                    continue
                if requested is False:
                    set_intent(key, False)
                elif bool(Modules.agent_flags.get(key, False)):
                    set_intent(key, True)
                # else: requested=True but capability rejected → leave intent untouched
        except Exception as exc:
            logger.warning("[Agent] Failed to persist agent flag intent: %s", exc)

    changed = Modules.agent_flags != old_flags or bool(Modules.analyzer_enabled) != old_analyzer_enabled
    if changed:
        _bump_state_revision()
    await _emit_agent_status_update(lanlan_name=lanlan_name)
    return {"success": True, "agent_flags": _agent_flags_snapshot()}


@app.post("/agent/command")
async def agent_command(payload: Dict[str, Any]):
    t0 = time.perf_counter()
    request_id = (payload or {}).get("request_id") or str(uuid.uuid4())
    command = (payload or {}).get("command")
    lanlan_name = (payload or {}).get("lanlan_name")
    if command == "set_agent_enabled":
        enabled = bool((payload or {}).get("enabled"))
        # ``_persist_intent`` (default True) gates whether this call writes
        # the user's intent to ``agent_runtime_intent.json``. The restore
        # path replays past intents through this same code path with
        # ``_persist_intent=False`` so the replay doesn't re-write what it's
        # reading.
        persist_intent = bool((payload or {}).get("_persist_intent", True))
        gate = _check_agent_api_gate()
        if enabled:
            Modules.analyzer_enabled = True
            Modules.analyzer_profile = (payload or {}).get("profile", {}) or {}
            if gate.get("ready") is True:
                adapter_refreshed = _try_refresh_model_probe(force=True)
                if not adapter_refreshed and Modules.model_probe is not None:
                    logger.info("[Agent] Agent model probe refresh failed; falling back to the existing one")
                if Modules.model_probe is not None:
                    _set_capability("computer_use", False, "AGENT_PRECHECK_PENDING")
                    _set_capability("browser_use", False, "AGENT_PRECHECK_PENDING")
                    asyncio.ensure_future(_fire_agent_llm_connectivity_check(queue=True))
                else:
                    _set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")
                    _set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")
            else:
                first_reason = (gate.get("reasons") or ["AGENT_ENDPOINT_NOT_CONFIGURED"])[0]
                _set_capability("computer_use", False, first_reason)
                _set_capability("browser_use", False, first_reason)
            if Modules.agent_flags.get("user_plugin_enabled"):
                # Master OFF preserves sub-feature intent but stops the plugin
                # lifecycle. Replaying the existing ON intent schedules a new,
                # generation-owned startup for the re-enabled master.
                await set_agent_flags({
                    "user_plugin_enabled": True,
                    "lanlan_name": lanlan_name,
                    "_persist_intent": False,
                })
        else:
            Modules.analyzer_enabled = False
            Modules.analyzer_profile = {}
            # Invalidate an in-flight user-plugin enable/readiness task before
            # stopping its lifecycle.  The sub-flag intent itself remains ON.
            Modules.user_plugin_lifecycle_seq += 1
            # NOTE: sub flags are NOT reset here. The master switch is a runtime
            # gate, not a clear-all command — sub flags carry the user's intent
            # for each component and must survive a master OFF/ON cycle (so the
            # user doesn't have to re-tick every sub-toggle after disabling the
            # master). The host, the built-in capabilities and the turn check
            # all test the master switch first, so leaving sub flags ON cannot
            # let any component "secretly keep running". The actual stop is
            # enforced by ``end_all`` + ``_ensure_plugin_lifecycle_stopped``;
            # ``intent`` (persistent) is also intentionally left untouched
            # here for the same reason.
            _set_capability("user_plugin", True, "")
            await admin_control({"action": "end_all"})
            await _ensure_plugin_lifecycle_stopped(
                should_stop=lambda: not Modules.analyzer_enabled
            )
        if persist_intent:
            try:
                from app.agent_runtime_intent import set_intent
                set_intent("analyzer_enabled", enabled)
            except Exception as exc:
                logger.warning("[Agent] Failed to persist analyzer_enabled intent: %s", exc)
        _bump_state_revision()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {
            "success": True,
            "request_id": request_id,
            "is_free_version": bool(gate.get("is_free_version")),
            "agent_api_gate": gate,
            "timing": {"agent_total_ms": total_ms},
        }
    if command == "set_flag":
        key = (payload or {}).get("key")
        value = bool((payload or {}).get("value"))
        if key not in {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled"}:
            raise HTTPException(400, "invalid flag key")
        t_set = time.perf_counter()
        await set_agent_flags({"lanlan_name": lanlan_name, key: value})
        set_ms = round((time.perf_counter() - t_set) * 1000, 2)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s key=%s set_flags_ms=%s total_ms=%s", request_id, command, key, set_ms, total_ms)
        return {"success": True, "request_id": request_id, "timing": {"set_flags_ms": set_ms, "agent_total_ms": total_ms}}
    if command == "refresh_state":
        snapshot = _collect_agent_status_snapshot()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {"success": True, "request_id": request_id, "snapshot": snapshot, "timing": {"agent_total_ms": total_ms}}
    raise HTTPException(400, "unknown command")


# ─── Agent runtime intent restore ───────────────────────────────────────
#
# At server start, ``Modules.analyzer_enabled`` and ``Modules.agent_flags``
# are all False; the user must re-tick every toggle they had on before
# restart. Restore replays the persisted intent (see ``agent_runtime_intent``
# module) the first time a real client session enters via
# ``greeting_check``, so the user's switches "just come back" the way the
# plugin manager's per-plugin disable already does.
#
# The replay walks the same ``set_agent_enabled`` / ``set_agent_flags`` code
# paths a manual UI toggle would, so capability checks, gate logic, and
# notifications all behave identically — and ``_persist_intent=False`` makes
# the replay non-recursive (it doesn't overwrite the intent file it's
# reading).
#
# Failure mode: LLM-dependent flags get a 15s probe window (3 × 4s ping with
# 5s spacing). Any permanent reason or all-three failure clears that intent
# to False and surfaces ``AGENT_AUTO_DISABLED_*`` notifications — the goal
# is to tell the user "your API is dead, fix it" rather than retry forever.

_intent_restore_done = False
_intent_restore_lock: Optional[asyncio.Lock] = None

# Restore probe budget. Worst-case wall time when probes keep timing out:
#   3 attempts × 6s timeout + 2 inter-attempt sleeps × 7s = ~32s.
# In practice the ping resolves in <1s on a healthy connection so users
# typically see toggles flip back within the first attempt. Tuning rationale:
# 6s per-call timeout gives cold-start DNS / TLS handshake comfortable room
# without dragging out the failure path; 7s gap lets a transient burst
# throttle window expire between attempts.
_RESTORE_PING_TIMEOUT_S = 6.0
_RESTORE_PING_INTERVAL_S = 7.0
_RESTORE_PING_MAX_ATTEMPTS = 3


async def _maybe_restore_agent_intent() -> None:
    """Idempotent restore entry. Safe to call from every greeting_check."""
    global _intent_restore_done, _intent_restore_lock
    if _intent_restore_done:
        return
    if os.environ.get("NEKO_DISABLE_AGENT_AUTO_RESTORE") == "1":
        # Escape hatch: if some restore step ever causes server lockup,
        # the user can launch with this env var to skip restore entirely
        # and re-toggle manually.
        _intent_restore_done = True
        logger.info("[Agent] NEKO_DISABLE_AGENT_AUTO_RESTORE=1, skipping intent restore")
        return
    if _intent_restore_lock is None:
        _intent_restore_lock = asyncio.Lock()
    async with _intent_restore_lock:
        if _intent_restore_done:
            return
        _intent_restore_done = True
        try:
            await _do_restore_agent_intent()
        except Exception as exc:
            logger.error("[Agent] Intent restore failed: %s", exc, exc_info=True)


async def _do_restore_agent_intent() -> None:
    from app.agent_runtime_intent import load_intent

    intent = load_intent()
    if not intent:
        logger.info("[Agent] No persisted agent intent to restore")
        return
    logger.info("[Agent] Restoring agent intent: %s", intent)

    # Master gate is the runtime prerequisite for *any* sub component:
    # sub-flag intents only matter when the master switch is ON. Since
    # set_agent_enabled(False) no longer wipes sub-flag intent, it's a
    # legitimate persisted state to have e.g. ``analyzer_enabled=False``
    # alongside ``user_plugin_enabled=True`` (the user toggled the master
    # off but kept their sub-flag preferences). In that case we must NOT
    # spin up plugin lifecycle / probe LLM — the
    # user explicitly disabled the master. Sub-flag intents stay in the
    # file untouched, so the next time the user turns the master back on
    # those flags will activate via the normal toggle path.
    master_enabled = bool(intent.get("analyzer_enabled"))
    if not master_enabled:
        logger.info(
            "[Agent] Restore: analyzer_enabled intent is %s, skipping sub-flag restore",
            intent.get("analyzer_enabled"),
        )
        return

    # Master ON — call agent_command directly (plain async fn despite the
    # FastAPI decorator) with _persist_intent=False so the replay doesn't
    # re-write what we just read.
    try:
        await agent_command({
            "command": "set_agent_enabled",
            "enabled": True,
            "_persist_intent": False,
        })
    except Exception as exc:
        logger.warning("[Agent] Failed to restore analyzer_enabled: %s", exc)
        # Master gate failed to activate → don't even try sub flags
        return

    # 2. Two fully-independent parallel tracks. CU/BU are LLM-coupled
    # (probe-gated). user_plugin runs on its own lifecycle and explicitly
    # does NOT wait for the LLM — plugins don't depend on the agent model.
    parallel: List[asyncio.Task] = []

    if intent.get("computer_use_enabled") or intent.get("browser_use_enabled"):
        t = asyncio.create_task(_restore_llm_dependent_flags(intent))
        Modules._persistent_tasks.add(t)
        t.add_done_callback(Modules._persistent_tasks.discard)
        parallel.append(t)

    if intent.get("user_plugin_enabled"):
        t = asyncio.create_task(_restore_user_plugin())
        Modules._persistent_tasks.add(t)
        t.add_done_callback(Modules._persistent_tasks.discard)
        parallel.append(t)

    # We deliberately don't gather() the parallel tasks — they update
    # capability + flags + intent on their own, and the user sees the
    # results via the normal status snapshot push. Awaiting here would
    # block the greeting_check handler for up to 15s.


async def _restore_llm_dependent_flags(intent: dict) -> None:
    """Probe LLM ≤3 times with 5s spacing. On success flip the in-memory
    CU/BU flags via set_agent_flags; on permanent failure or all-three
    fail, clear those intents and emit AGENT_AUTO_DISABLED_* notifications."""
    from app.agent_runtime_intent import set_intent
    from .model_probe import PERMANENT_CONNECTIVITY_REASONS

    adapter = Modules.model_probe
    if adapter is None:
        # Module not loaded is permanent — no point retrying.
        logger.warning("[Agent] Restore: agent model probe not loaded; clearing CU/BU intent")
        for key, code in (
            ("computer_use_enabled", "AGENT_AUTO_DISABLED_COMPUTER"),
            ("browser_use_enabled", "AGENT_AUTO_DISABLED_BROWSER"),
        ):
            if intent.get(key):
                set_intent(key, False)
                Modules.notification = json.dumps({
                    "code": code,
                    "details": {"reason_code": "AGENT_CU_MODULE_NOT_LOADED"},
                })
        _bump_state_revision()
        await _emit_agent_status_update()
        return

    last_reason = "AGENT_LLM_UNREACHABLE"
    success = False
    for attempt in range(_RESTORE_PING_MAX_ATTEMPTS):
        try:
            ok, reason = await asyncio.to_thread(
                adapter.check_connectivity,
                timeout_s=_RESTORE_PING_TIMEOUT_S,
            )
            if ok:
                success = True
                last_reason = ""
                break
            last_reason = reason or "AGENT_LLM_UNREACHABLE"
            if last_reason in PERMANENT_CONNECTIVITY_REASONS:
                logger.info(
                    "[Agent] Restore: permanent connectivity reason %s after %d/%d attempts; not retrying",
                    last_reason, attempt + 1, _RESTORE_PING_MAX_ATTEMPTS,
                )
                break
        except Exception as exc:
            logger.warning(
                "[Agent] Restore probe attempt %d/%d raised: %s",
                attempt + 1, _RESTORE_PING_MAX_ATTEMPTS, exc,
            )
            last_reason = "AGENT_LLM_UNREACHABLE"
        if attempt < _RESTORE_PING_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RESTORE_PING_INTERVAL_S)

    if success:
        # Hand off to the regular toggle path so capability cache + UI
        # snapshot stay consistent with manual toggling.
        payload: Dict[str, Any] = {"_persist_intent": False}
        # Wayland display capture requires a fresh user gesture. A persisted
        # keyboard-control flag cannot restore the prior process's MediaStream.
        needs_screen_reauthorization = (
            sys.platform.startswith("linux")
            and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY")))
        )
        if intent.get("computer_use_enabled") and needs_screen_reauthorization:
            availability = await asyncio.to_thread(_keyboard_mouse_availability)
            reasons = availability.get("reasons", []) if isinstance(availability, dict) else []
            ready = bool(availability.get("ready")) if isinstance(availability, dict) else False
            _set_capability("computer_use", ready, reasons[0] if reasons else "")
            if ready:
                Modules.notification = json.dumps({"code": "AGENT_SCREEN_SHARE_REQUIRED"})
            _bump_state_revision()
            await _emit_agent_status_update()
        elif intent.get("computer_use_enabled"):
            payload["computer_use_enabled"] = True
        if intent.get("browser_use_enabled"):
            payload["browser_use_enabled"] = True
        if len(payload) > 1:
            try:
                await set_agent_flags(payload)
                logger.info("[Agent] Restored CU/BU flags after successful probe")
            except Exception as exc:
                logger.warning("[Agent] Failed to apply CU/BU after probe: %s", exc)
        return

    # All retries exhausted (or permanent error): tell the user, clear intent.
    for key, code in (
        ("computer_use_enabled", "AGENT_AUTO_DISABLED_COMPUTER"),
        ("browser_use_enabled", "AGENT_AUTO_DISABLED_BROWSER"),
    ):
        if intent.get(key):
            set_intent(key, False)
            Modules.notification = json.dumps({
                "code": code,
                "details": {"reason_code": last_reason},
            })
            logger.info(
                "[Agent] Restore: cleared intent for %s after %d failed probes (reason=%s)",
                key, _RESTORE_PING_MAX_ATTEMPTS, last_reason,
            )
    _bump_state_revision()
    await _emit_agent_status_update()


async def _restore_user_plugin() -> None:
    """Hand off to the standard /agent/flags path. user_plugin does NOT
    require the LLM probe to be green — plugins run on their own lifecycle,
    so we trigger them straight away in parallel. Any startup failure goes
    through the existing _bg_plugin_enable async path."""
    try:
        await set_agent_flags({
            "user_plugin_enabled": True,
            "_persist_intent": False,
        })
        logger.info("[Agent] Restore: user_plugin_enabled requested")
    except Exception as exc:
        logger.warning("[Agent] Failed to restore user_plugin_enabled: %s", exc)


def _reset_intent_restore_for_testing() -> None:
    """Test helper: clear the once-flag so a test can re-run restore."""
    global _intent_restore_done, _intent_restore_lock
    _intent_restore_done = False
    _intent_restore_lock = None


@app.get("/computer_use/availability")
async def computer_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    if not Modules.model_probe:
        _try_refresh_model_probe(force=True)
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    if not Modules.model_probe:
        if Modules.agent_flags.get("computer_use_enabled"):
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.notification = json.dumps({"code": "AGENT_CU_AUTO_CLOSED"})
        raise HTTPException(503, "ComputerUse not ready")
    if not getattr(Modules.model_probe, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())

    status = await asyncio.to_thread(_keyboard_mouse_availability)
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    ready = bool(status.get("ready")) if isinstance(status, dict) else False
    if not ready and _probe_lost_a_race():
        # What was found is still answered, but nothing is taken from the
        # task that is under way: see ``_probe_lost_a_race``.
        logger.info("[Agent] Computer Use reads as not ready while a task is using the tools; left as it is")
    else:
        _set_capability("computer_use", ready, reasons[0] if reasons else "")

        # Auto-update flag if capability lost
        if not status.get("ready") and Modules.agent_flags.get("computer_use_enabled"):
            logger.info("[Agent] Computer Use capability lost, disabling flag")
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.notification = json.dumps({"code": "AGENT_CU_CAPABILITY_LOST", "details": {"reason_code": status.get('reasons', [])[0] if status.get('reasons') else 'unknown'}})

    # The tool works from screenshots: whether the model is sent any, and why not.
    status["model_images"] = await _model_images_status()
    return status


@app.post("/notify_config_changed")
async def notify_config_changed():
    """Called by the main server after API-key / model config is saved.
    Rebuilds the agent model probe with fresh config and kicks off a non-blocking
    LLM connectivity check — but only when the user actually has the master
    switch on AND at least one LLM-dependent sub flag enabled.

    The master gate is required because with the new master-OFF semantics
    (sub flags carry user intent and survive master cycling),
    ``computer_use_enabled``/``browser_use_enabled`` can legitimately stay
    True while the master is off. The old ``or`` condition would otherwise
    fire a probe on every voice/chat config save and pop a transient
    "cat-paw preflight failed" toast for a feature the user has explicitly
    disabled at the master.

    Sub-flag check still gates probes when the master is on but the user
    isn't using CU/BU — same rationale as the original docstring: routine
    config saves shouldn't probe for a feature nobody's using."""
    _try_refresh_model_probe(force=True)
    flags = Modules.agent_flags or {}
    if Modules.analyzer_enabled and (
        flags.get("computer_use_enabled") or flags.get("browser_use_enabled")
    ):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
        return {"success": True, "message": "agent model probe refreshed, connectivity check started"}
    return {"success": True, "message": "agent model probe refreshed; probe skipped (agent idle)"}


@app.get("/browser_use/availability")
async def browser_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    dependency_ready, dependency_error = await asyncio.to_thread(_browser_dependency_status)
    if not dependency_ready:
        # No browser comes with the application; one the user has is used.
        _set_capability("browser_use", False, dependency_error)
        return {"enabled": True, "ready": False, "reasons": [dependency_error], "provider": "nekoclaw"}
    # LLM connectivity — reuse the shared agent-LLM check
    probe = Modules.model_probe
    if probe and not getattr(probe, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    llm_ok = probe is not None and getattr(probe, "init_ok", False)
    reasons = []
    if not llm_ok:
        reasons.append(probe.last_error if probe and probe.last_error else "Agent LLM not connected")
    ready = llm_ok and dependency_ready
    # A model that reads as unreachable while a task is using the tools
    # takes nothing from that task: see ``_probe_lost_a_race``.
    if ready or not _probe_lost_a_race():
        _set_capability("browser_use", ready, reasons[0] if reasons else "")
    return {"enabled": True, "ready": ready, "reasons": reasons, "provider": "nekoclaw"}


@app.get("/tasks")
async def list_tasks():
    """Return the tasks under way and those that ended a short while ago.

    Read from this process's view of the host's ledger, so it is quick; the
    HUD calls it every few seconds while a task runs. The ledger is opened
    only once, when an earlier process left a record and nothing has used
    the host yet.
    """
    await restore_hud()
    items = [dict(task, source="runtime") for task in hud.all()]
    return {"tasks": items, "debug": {"total_returned": len(items)}}


@app.post("/admin/control")
async def admin_control(payload: Dict[str, Any]):
    action = (payload or {}).get("action")
    if action == "end_all":
        # Every commission under way is stopped. The status is written first
        # and the HUD is told from that, so a card never stays "running"
        # while something slow is still being torn down.
        stopped = await stop_everything("Cancelled by user")
        if stopped:
            logger.info("[Agent] end_all: stopped %d task(s)", stopped)

        # Background work that belongs to no commission (a turn check, a
        # voice-transcript hand-over).
        tasks_to_await = []
        for t in set(Modules._background_tasks):
            if not t.done():
                t.cancel()
                tasks_to_await.append(t)
        if tasks_to_await:
            # Bounded wait: a coroutine stuck in an uncancellable spot must
            # not wedge end_all itself (the frontend proxy gives up after 5s
            # and the user sees the ✕ do nothing).
            done, pending = await asyncio.wait(tasks_to_await, timeout=10.0)
            if pending:
                logger.warning(
                    "[Agent] end_all: %d task(s) still not finished 10s after cancel; continuing teardown",
                    len(pending),
                )
            for res in done:
                try:
                    exc = res.exception()
                except asyncio.CancelledError:
                    continue
                if exc is not None:
                    logger.warning(f"[Agent] Error awaiting cancelled background task: {exc}")
        Modules._background_tasks.clear()

        # Stopping a commission cancels its calls of the built-in tools and
        # gives the desktop and the browser back. Keys a stopped call left
        # held are let go of, and first: a key that stays down is in the
        # user's way at once, and has nothing to wait for in the browser.
        try:
            from nekoclaw.host.capabilities import BUILTIN_GROUP_KEYBOARD_MOUSE

            await builtin_provider.released(BUILTIN_GROUP_KEYBOARD_MOUSE, "")
        except Exception as e:
            logger.warning(f"[Agent] Error releasing held keys during end_all: {e}")
        # The browser the tools started is closed as well, so nothing they
        # opened outlives end_all (a disable-triggered close is itself
        # tracked above and may have been cancelled by this drain). Within a
        # time of its own: its steps add up to more than the page waits for
        # this request.
        from config.nekoclaw_settings import BROWSER_CLOSE_AT_END_ALL_SECONDS

        try:
            await asyncio.wait_for(_close_browser(update_capability=False), timeout=BROWSER_CLOSE_AT_END_ALL_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("[Agent] end_all: the browser did not close in time; killing it")
            # Not waited for: killing waits for the processes to be gone,
            # which takes seconds more, and the answer to this request does
            # not depend on it. A browser call that comes meanwhile waits
            # until the kill is over (the provider sees to it): the kill
            # takes what it finds when its turn comes, and must not find a
            # browser that a later task has started.
            builtin_provider.kill_browser_unwaited()
        return {"success": True, "message": "all tasks terminated and cleared"}
    elif action == "enable_analyzer":
        Modules.analyzer_enabled = True
        Modules.analyzer_profile = (payload or {}).get("profile", {})
        return {"success": True, "analyzer_enabled": True, "profile": Modules.analyzer_profile}
    elif action == "disable_analyzer":
        Modules.analyzer_enabled = False
        Modules.analyzer_profile = {}
        # cascade end_all
        await admin_control({"action": "end_all"})
        return {"success": True, "analyzer_enabled": False}
    else:
        raise HTTPException(400, "unknown action")
