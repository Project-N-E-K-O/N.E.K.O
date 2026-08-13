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

"""Per-character language preference for internal conversation templates."""

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse

from config import MEMORY_SERVER_PORT
from utils.internal_http_client import get_internal_http_client
from utils.character_memory import character_config_mutation_lock
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.preferences import aload_ui_language_override
from utils.recent_file import RecentFileDeletedError, capture_recent_generation

from ..shared_state import get_config_manager, get_session_manager
from ..system_router._shared import _validate_local_mutation_request
from ._shared import (
    _read_json_object_or_400,
    _validate_existing_character_path_name,
    logger,
    router,
)
from .crud import _clear_character_recent_history


class LanguagePreferenceConflictError(Exception):
    """A newer language preference was persisted while this request was in flight.

    This is the designed outcome of the memory server's causal-order check, not
    a server fault, so it must reach the client as 409 instead of being folded
    into the generic 5xx branch.
    """


def _is_superseded_conflict(response) -> bool:
    """Tell this endpoint's causal-order conflict from the server's other 409s."""
    try:
        payload = response.json()
    except Exception:
        return False
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return False
    return detail.get("error_code") == "language_preference_superseded"


async def _request_memory_prompt_locale(
    method: str,
    name: str,
    *,
    language: str | None = None,
) -> dict:
    client = get_internal_http_client()
    url = (
        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/prompt-locale/"
        f"{quote(name, safe='')}"
    )
    if method == "GET":
        response = await client.get(url, timeout=5.0)
    else:
        response = await client.put(
            url,
            json={"language": language},
            timeout=5.0,
        )
    if response.status_code == 409 and _is_superseded_conflict(response):
        # Raised before raise_for_status so the conflict keeps its own identity:
        # the loser of a concurrent write must not be reported as a failure.
        # Every other 409 the memory server can answer with (cloudsave
        # maintenance fence, storage-limited startup) is a retryable failure and
        # must keep falling through to the generic error path below -- reporting
        # those as "superseded" would tell the client to re-read state that was
        # never written.
        raise LanguagePreferenceConflictError(
            "a newer language preference superseded this request"
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Memory server rejected language preference")
    return payload


async def _load_existing_character(name: str) -> tuple[object, dict]:
    validation_error = _validate_existing_character_path_name(name)
    if validation_error:
        raise ValueError(validation_error)
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    if name not in (characters.get("猫娘") or {}):
        raise LookupError("角色不存在")
    return config_manager, characters


async def apply_character_language_preference(name: str, language: str) -> dict:
    """Persist one template locale and isolate the next turn from old context."""
    if not is_supported_language_code(language):
        raise ValueError("不支持的语言")
    normalized = normalize_language_code(language, format="full")

    # The characters.json transaction only has to cover the existence check, the
    # recent-file identity token, and the durable write.  Everything after it
    # runs unlocked on purpose: the reconciliation below waits on a
    # cross_server round-trip, and the connector may concurrently be running a
    # *late* recent-clear callback that takes this same lock.  Holding it across
    # the barrier would let this request block the connector it is waiting for,
    # stalling until the barrier timeout.  Identity is still protected after the
    # release by re-validating the character and by the recent-file generation
    # token captured here.
    async with character_config_mutation_lock:
        config_manager, _characters = await _load_existing_character(name)
        recent_path = Path(config_manager.memory_dir) / name / "recent.json"
        admission_generation = capture_recent_generation(recent_path)
        memory_result = await _request_memory_prompt_locale(
            "PUT",
            name,
            language=normalized,
        )

    return await _reconcile_after_language_change(
        name,
        normalized,
        config_manager,
        memory_result=memory_result,
        admission_generation=admission_generation,
    )


async def _reconcile_after_language_change(
    name: str,
    normalized: str,
    config_manager: object,
    *,
    memory_result: dict,
    admission_generation: tuple[str, int],
) -> dict:
    result = {
        "success": True,
        "language": normalized,
        "previous_language": memory_result.get("previous_language"),
        "changed": bool(memory_result.get("changed")),
        "recent_history_cleared": False,
        "session_reset": False,
    }

    session_manager = get_session_manager()
    manager = session_manager.get(name) if session_manager else None
    live_language = getattr(manager, "user_language", None) if manager is not None else None
    normalized_live_language = (
        normalize_language_code(live_language, format="full")
        if isinstance(live_language, str) and is_supported_language_code(live_language)
        else None
    )
    # An unset manager locale is *absence of evidence*, not evidence that the
    # history was rendered in the process locale.  Startup creates a manager for
    # every character with ``user_language=None``, and such a manager sends
    # neither ``language`` nor ``render_language`` to /new_dialog, which then
    # falls back to the character's own durable preference -- so the existing
    # context was rendered in the durable locale, whatever the global locale is.
    # Whether that durable locale actually changed is exactly what ``changed``
    # already reports, so a manager with no locale of its own must not
    # contribute an isolation signal here.
    live_locale_changed = (
        normalized_live_language is not None
        and normalized_live_language != normalized
    )
    needs_explicit_promotion = manager is not None and not getattr(
        manager,
        "_user_language_explicit",
        False,
    )
    manager_needs_reconciliation = live_locale_changed or needs_explicit_promotion
    if manager_needs_reconciliation:
        try:
            manager.set_user_language(normalized)
        except Exception as exc:
            logger.warning(
                "刷新当前会话语言失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但当前会话语言刷新失败",
            })

    # A setter can update the live locale before a later tool/session refresh
    # raises.  Isolate whenever the pre-call locale truly differed, even if the
    # reconciliation did not return normally; exact repeats and provenance-only
    # promotion remain side-effect free.
    needs_context_isolation = result["changed"] or live_locale_changed
    if not needs_context_isolation:
        return result

    # This is intentionally a partial reset: recent conversation text can steer
    # language choice, while durable facts/persona must survive the preference
    # change.  The callback may run twice after a connector timeout: once as the
    # immediate fallback and once after a late settlement.  Both writes are
    # intentional, so the late old-session write can never become the final state.
    recent_clear_lock = asyncio.Lock()

    async def clear_recent_after_settlement() -> None:
        # One uniform path now that the caller no longer holds the config lock:
        # whoever runs this callback (this request's fallback, or the connector
        # task after a late settlement) takes the transaction itself.
        async with recent_clear_lock, character_config_mutation_lock:
            try:
                await _load_existing_character(name)
            except LookupError:
                return
            try:
                await _clear_character_recent_history(
                    config_manager,
                    name,
                    expected_generation=admission_generation,
                )
            except RecentFileDeletedError:
                # The name was renamed/deleted/reused after this PUT was
                # admitted.  A late old-session callback must not recreate
                # or clear the newer identity's recent history.
                return
            result["recent_history_cleared"] = True

    # ``is_active`` remains false while start_session owns its in-flight setup.
    # Calling end_session with the resulting unguarded ``None`` snapshot can
    # clear that startup's queued input or tear down the session it just promoted.
    # Let the startup keep its lifecycle ownership; the durable/live language
    # update above and the guarded recent-history clear below still take effect.
    if manager is not None and not getattr(manager, "is_starting", False):
        expected_session = (
            getattr(manager, "session", None)
            if getattr(manager, "is_active", False)
            else None
        )
        if expected_session is not None:
            notify_session_ended = getattr(
                manager,
                "send_session_ended_by_server",
                None,
            )
            if callable(notify_session_ended):
                try:
                    await notify_session_ended()
                except Exception as exc:
                    logger.warning(
                        "语言切换前通知前端结束当前会话失败: name=%s err=%s",
                        name,
                        exc,
                        exc_info=True,
                    )
                    result.update({
                        "success": False,
                        "partial_success": True,
                        "error": "语言偏好已保存，但前端会话状态可能未完整重置",
                    })
        try:
            # Even an already-inactive manager may still have old messages queued
            # in cross_server.  Settle that queue without invoking unguarded
            # lifecycle teardown; the manager rechecks idle state under its lock
            # in case a start began after the snapshot above.
            if expected_session is None:
                await manager.settle_session_memory_if_idle(
                    clear_recent_after_settlement,
                )
            else:
                await manager.end_session(
                    by_server=True,
                    expected_session=expected_session,
                    after_memory_settlement=clear_recent_after_settlement,
                )
            if expected_session is not None:
                result["session_reset"] = True
        except Exception as exc:
            logger.warning(
                "语言切换后结算并结束当前会话失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            if expected_session is not None and not getattr(manager, "is_active", False):
                result["session_reset"] = True
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但当前会话未能完整重置",
            })
        reset_circuit = getattr(manager, "reset_session_start_circuit", None)
        if expected_session is not None and callable(reset_circuit):
            try:
                reset_circuit()
            except Exception as exc:
                logger.warning(
                    "重置语言切换后的会话熔断状态失败: name=%s err=%s",
                    name,
                    exc,
                )

    # No manager, a stale-session guard, or an early lifecycle failure can return
    # before the queued callback runs.  Clear immediately in that case; if a
    # barrier was queued, it remains armed and will clear once more after any
    # delayed old-session settlement.
    if not result["recent_history_cleared"]:
        try:
            await clear_recent_after_settlement()
        except Exception as exc:
            logger.warning(
                "清理语言切换前的近期上下文失败: name=%s err=%s",
                name,
                exc,
                exc_info=True,
            )
            result.update({
                "success": False,
                "partial_success": True,
                "error": "语言偏好已保存，但近期上下文清理失败",
            })

    await _assert_still_current(name, normalized)
    return result


async def _assert_still_current(name: str, normalized: str) -> None:
    """Refuse to report success for a preference that has since been replaced.

    Reconciliation runs outside the transaction, so a second window can commit a
    newer locale while this request is still settling.  Returning 200 with the
    older language would let a late-arriving response overwrite the frontend's
    shared local cache with a value the server no longer holds, and a later
    websocket session could then re-publish that obsolete preference.

    Fail soft on a read error: a durable write that we merely cannot re-read
    must not be reported as superseded.
    """
    try:
        current = await _request_memory_prompt_locale("GET", name)
    except LanguagePreferenceConflictError:
        raise
    except Exception as exc:
        logger.warning(
            "语言偏好写入后校验失败，按已保存返回: name=%s err=%s",
            name,
            exc,
        )
        return
    durable = current.get("language")
    if is_supported_language_code(durable) and (
        normalize_language_code(durable, format="full") != normalized
    ):
        raise LanguagePreferenceConflictError(
            "a newer language preference superseded this request"
        )


@router.get("/character/{name}/language-preference")
async def get_character_language_preference(name: str):
    try:
        # Deliberately unlocked.  This is the endpoint the frontend hydrates
        # from under a 2.5s timeout, and queueing it behind a long write
        # transaction (workshop unsubscribe, card import) degrades the whole
        # page to the untrusted-cache fallback.  It is safe now that locale
        # sidecar resolution no longer creates the character directory
        # (see locale_state._locale_path), so a concurrent delete/rename can
        # no longer leave an empty old-name directory behind.
        await _load_existing_character(name)
        payload = await _request_memory_prompt_locale("GET", name)
        # Dropping the lock also dropped the guarantee that the character still
        # exists once the read returns.  Without a second check this would answer
        # 200 for a name deleted mid-read, and an in-flight card-manager
        # hydration could repopulate that name's local language cache after the
        # deletion cleanup -- which a later reuse of the same name would inherit.
        await _load_existing_character(name)
        ui_language = await aload_ui_language_override()
        payload["effective_language"] = (
            payload.get("language")
            or ui_language
            or payload.get("effective_language")
        )
        return payload
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=400,
        )
    except LookupError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=404,
        )
    except Exception as exc:
        logger.warning("读取角色语言偏好失败: name=%s err=%s", name, exc)
        return JSONResponse(
            {"success": False, "error": "读取语言偏好失败"},
            status_code=503,
        )


@router.put("/character/{name}/language-preference")
async def set_character_language_preference(name: str, request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        return validation_error
    try:
        result = await apply_character_language_preference(
            name,
            payload.get("language"),
        )
        return JSONResponse(
            result,
            status_code=(
                200
                if result.get("success") or result.get("partial_success")
                else 500
            ),
        )
    except LanguagePreferenceConflictError:
        # Expected race, not a fault: another window persisted a newer value.
        # Log at info and let the client re-read instead of rolling its control
        # back to a value that is already stale.
        logger.info("角色语言偏好被更新的请求取代: name=%s", name)
        return JSONResponse(
            {
                "success": False,
                "error_code": "language_preference_superseded",
                "error": "已有更新的语言偏好生效",
            },
            status_code=409,
        )
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=400,
        )
    except LookupError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=404,
        )
    except Exception as exc:
        logger.exception("保存角色语言偏好失败: name=%s", name)
        return JSONResponse(
            {"success": False, "error": "保存语言偏好失败"},
            status_code=503,
        )
