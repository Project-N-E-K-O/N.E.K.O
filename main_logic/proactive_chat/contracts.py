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

"""Stable command and result contracts for proactive chat.

This module is intentionally framework-independent.  It owns the public
request command plus the ``action`` / ``reason_code`` / ``stage`` vocabulary
and helpers that construct domain result bodies.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProactiveChatCommand:
    """Framework-independent snapshot of a proactive-chat request payload.

    Values intentionally retain the request's existing truthiness semantics.
    In particular, ``enabled_modes_provided`` distinguishes a missing field
    (legacy source inference) from an explicitly supplied empty list (all
    source toggles disabled).
    """

    lanlan_name: Any = None
    voice_mode: bool = False
    is_playing_music: bool = False
    current_track: Any = None
    music_cooldown: bool = False
    mini_game_invite_enabled: bool = True
    base_interval_seconds: Any = None
    enabled_modes: Any = None
    enabled_modes_provided: bool = False
    content_type: Any = None
    screenshot_data: Any = None
    use_window_search: bool = False
    use_personal_dynamic: bool = False
    avatar_position: Any = None
    window_title: Any = ""
    language: Any = None
    lang: Any = None
    i18n_language: Any = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProactiveChatCommand":
        """Build a command without importing HTTP framework types."""
        return cls(
            lanlan_name=payload.get("lanlan_name"),
            voice_mode=bool(payload.get("voice_mode", False)),
            is_playing_music=bool(payload.get("is_playing_music", False)),
            current_track=payload.get("current_track"),
            music_cooldown=bool(payload.get("music_cooldown", False)),
            mini_game_invite_enabled=bool(
                payload.get("mini_game_invite_enabled", True)
            ),
            base_interval_seconds=payload.get("base_interval_seconds"),
            enabled_modes=payload.get("enabled_modes"),
            enabled_modes_provided="enabled_modes" in payload,
            content_type=payload.get("content_type"),
            screenshot_data=payload.get("screenshot_data"),
            use_window_search=bool(payload.get("use_window_search", False)),
            use_personal_dynamic=bool(
                payload.get("use_personal_dynamic", False)
            ),
            avatar_position=payload.get("avatar_position"),
            window_title=payload.get("window_title", ""),
            language=payload.get("language"),
            lang=payload.get("lang"),
            i18n_language=payload.get("i18n_language"),
        )

    @property
    def language_candidates(self) -> tuple[Any, Any, Any]:
        """Return request locale aliases in their established precedence."""
        return self.language, self.lang, self.i18n_language


@dataclass(frozen=True, slots=True)
class ProactiveChatResult:
    """Framework-independent result adapted to HTTP by the Router."""

    body: dict[str, Any]
    status_code: int = 200


PROACTIVE_REASON_CHAT_DELIVERED = "CHAT_DELIVERED"
PROACTIVE_REASON_PASS_BUSY = "PASS_BUSY"
PROACTIVE_REASON_PASS_ACTIVITY_BUSY = "PASS_ACTIVITY_BUSY"
PROACTIVE_REASON_PASS_DELIVERY_BUSY = "PASS_DELIVERY_BUSY"
PROACTIVE_REASON_PASS_DISABLED = "PASS_DISABLED"
PROACTIVE_REASON_PASS_ROUTE_ACTIVE = "PASS_ROUTE_ACTIVE"
PROACTIVE_REASON_PASS_PRIVACY = "PASS_PRIVACY"
PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY = "PASS_RESTRICTED_SCREEN_ONLY"
PROACTIVE_REASON_PASS_THROTTLED = "PASS_THROTTLED"
PROACTIVE_REASON_PASS_SOURCE_EMPTY = "PASS_SOURCE_EMPTY"
PROACTIVE_REASON_PASS_MODEL_PASS = "PASS_MODEL_PASS"
PROACTIVE_REASON_PASS_GENERATION_EMPTY = "PASS_GENERATION_EMPTY"
PROACTIVE_REASON_PASS_DUPLICATE = "PASS_DUPLICATE"
PROACTIVE_REASON_DELIVERY_PREEMPTED = "DELIVERY_PREEMPTED"
PROACTIVE_REASON_DELIVERY_FAILED = "DELIVERY_FAILED"
PROACTIVE_REASON_ERROR_TIMEOUT = "ERROR_TIMEOUT"
PROACTIVE_REASON_ERROR_INTERNAL = "ERROR_INTERNAL"
PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND = "ERROR_CHARACTER_NOT_FOUND"
PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED = "ERROR_SOURCE_FETCH_FAILED"
PROACTIVE_REASON_PASS_UNSPECIFIED = "PASS_UNSPECIFIED"

PROACTIVE_STAGE_ENTRY_GUARD = "entry_guard"
PROACTIVE_STAGE_ACTIVITY_GATE = "activity_gate"
PROACTIVE_STAGE_SOURCE_SELECTION = "source_selection"
PROACTIVE_STAGE_MODEL_DECISION = "model_decision"
PROACTIVE_STAGE_GENERATION = "generation"
PROACTIVE_STAGE_DEDUP = "dedup"
PROACTIVE_STAGE_DELIVERY = "delivery"
PROACTIVE_STAGE_RUNTIME_ERROR = "runtime_error"
PROACTIVE_STAGE_UNKNOWN = "unknown"

_PROACTIVE_REASON_STAGE: dict[str, str] = {
    PROACTIVE_REASON_CHAT_DELIVERED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_BUSY: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_DISABLED: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_PRIVACY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_THROTTLED: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_MODEL_PASS: PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY: PROACTIVE_STAGE_GENERATION,
    PROACTIVE_REASON_PASS_DUPLICATE: PROACTIVE_STAGE_DEDUP,
    PROACTIVE_REASON_DELIVERY_PREEMPTED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_DELIVERY_FAILED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_ERROR_TIMEOUT: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_INTERNAL: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_UNSPECIFIED: PROACTIVE_STAGE_UNKNOWN,
}


def _proactive_stage_for_reason(reason_code: str | None) -> str:
    if not reason_code:
        return PROACTIVE_STAGE_UNKNOWN
    return _PROACTIVE_REASON_STAGE.get(reason_code, PROACTIVE_STAGE_UNKNOWN)


def _proactive_response_body(
    action: str | None,
    reason_code: str,
    *,
    success: bool,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"success": success, "reason_code": reason_code}
    if action is not None:
        body["action"] = action
    body.update(extra)
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(reason_code)
    return body


def _proactive_pass_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("pass", reason_code, success=success, **extra)


def _proactive_chat_body(
    reason_code: str = PROACTIVE_REASON_CHAT_DELIVERED,
    **extra: Any,
) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("chat", reason_code, success=success, **extra)


def _proactive_error_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", False))
    return _proactive_response_body(None, reason_code, success=success, **extra)


def _ensure_proactive_reason_code(
    body: dict[str, Any],
    *,
    default_reason_code: str | None = None,
) -> dict[str, Any]:
    existing_reason_code = body.get("reason_code")
    if existing_reason_code:
        if not body.get("stage"):
            body["stage"] = _proactive_stage_for_reason(str(existing_reason_code))
        return body
    action = body.get("action")
    if default_reason_code is None:
        if action == "chat":
            default_reason_code = PROACTIVE_REASON_CHAT_DELIVERED
        elif action == "pass":
            default_reason_code = PROACTIVE_REASON_PASS_UNSPECIFIED
        else:
            default_reason_code = PROACTIVE_REASON_ERROR_INTERNAL
    body["reason_code"] = default_reason_code
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(default_reason_code)
    return body
