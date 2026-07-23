"""Bounded solo-stream scene continuity derived from successful plugin results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SCENE_STATE_VERSION = 0
SCENE_STATE_TTL_SECONDS = 120.0
SCENE_STATE_MAX_VIEWER_TURNS = 3
SCENE_STATE_PROMPT_MAX_CHARS = 160

_HOSTING_SOURCES = {"warmup_hosting", "idle_hosting", "active_engagement"}
_VIEWER_SOURCES = {"live_danmaku", "manual_live_simulation"}
_SUPPORT_EVENT_TYPES = {"gift", "guard", "sc", "super_chat"}
_VIEWER_CHOICE_SHAPES = {
    "either_or",
    "micro_challenge",
    "one_word_call",
    "small_challenge",
    "tiny_choice",
}
_PUBLIC_THREAD_KEYS = {
    "either_or",
    "light_stance",
    "light_tease",
    "micro_challenge",
    "one_word_call",
    "opening",
    "small_challenge",
    "small_mood",
    "soft_observation",
    "tiny_choice",
    "tiny_tease",
}
_MOVES = {
    "setup": "bridge once, then develop; do not repeat setup",
    "develop": "continue only if relevant; otherwise transition",
    "viewer_choice": "take a clear short reply as the answer; ask no second question",
    "callback": "pay off once, then leave room; do not restart the hook",
    "close": "close or transition; do not force the old hook",
}


@dataclass(frozen=True, slots=True)
class SceneStatePrompt:
    text: str = ""
    reason: str = "no_scene"

    @property
    def characters(self) -> int:
        return len(self.text)


class SceneState:
    """One runtime-only scene beat; never owns scheduling or transcript memory."""

    def __init__(self, *, now: Callable[[], float]) -> None:
        self._now = now
        self.reset()

    def reset(self) -> None:
        self._mode = ""
        self._transition_count = 0
        self._expired_count = 0
        self._prompt_uses = 0
        self._prompt_omits = 0
        self._prompt_last_chars = 0
        self._prompt_last_reason = ""
        self._clear_scene()

    def _clear_scene(self) -> None:
        self._phase = "idle"
        self._thread_key = ""
        self._viewer_turn_count = 0
        self._viewer_response_count = 0
        self._updated_at = 0.0
        self._last_result_key = ""
        self._last_input_trace_id = ""

    def observe_result(self, result: Any) -> None:
        if not isinstance(result, Mapping):
            return
        event = result.get("event")
        if not isinstance(event, Mapping):
            return
        mode = str(event.get("live_mode") or "")
        self._sync_mode(mode)
        if mode != "solo_stream" or str(result.get("status") or "") != "pushed":
            return
        self._expire_if_stale()
        result_key = self._result_key(result, event)
        if result_key and result_key == self._last_result_key:
            return
        if result_key:
            self._last_result_key = result_key

        source = str(event.get("source") or "")
        if source in _HOSTING_SOURCES:
            self._start_host_beat(source, event)
            return
        if source not in _VIEWER_SOURCES or self._is_support_result(result, event):
            return
        if self._phase in {"idle", "transition"}:
            return

        self._viewer_turn_count = min(
            SCENE_STATE_MAX_VIEWER_TURNS,
            self._viewer_turn_count + 1,
        )
        profile = str(result.get("danmaku_profile") or "")
        if profile == "active_hook_answer" or self._phase == "callback":
            self._set_phase("close")
        elif self._phase == "close":
            self._set_phase("transition")
        elif self._viewer_turn_count >= SCENE_STATE_MAX_VIEWER_TURNS:
            self._set_phase("close")
        else:
            self._set_phase("develop")

    def prompt_for_event(self, event: Any) -> SceneStatePrompt:
        mode = str(getattr(event, "live_mode", "") or "")
        self._sync_mode(mode)
        if mode != "solo_stream":
            return self._record_prompt(SceneStatePrompt(reason="inactive_mode"))
        if not self._is_viewer_event(event):
            return self._record_prompt(SceneStatePrompt(reason="unsupported_event"))
        self._expire_if_stale()
        if self._phase in {"idle", "transition"}:
            return self._record_prompt(SceneStatePrompt(reason="no_scene"))

        trace_id = str(getattr(event, "trace_id", "") or "")
        if self._is_active_hook_answer(event) and (
            not trace_id or trace_id != self._last_input_trace_id
        ):
            if trace_id:
                self._last_input_trace_id = trace_id
            self._viewer_response_count += 1
            self._set_phase("callback")

        move = _MOVES.get(self._phase)
        if not move:
            return self._record_prompt(SceneStatePrompt(reason="no_scene"))
        thread = f";thread={self._thread_key}" if self._thread_key else ""
        text = (
            f"Scene beat: phase={self._phase}{thread}. {move}. "
            "Current message wins; keep state private.\n"
        )
        if len(text) > SCENE_STATE_PROMPT_MAX_CHARS:
            return self._record_prompt(SceneStatePrompt(reason="character_budget"))
        return self._record_prompt(SceneStatePrompt(text=text, reason="rendered"))

    def suppress_prompt(self, reason: str) -> SceneStatePrompt:
        """Record an upstream safety omission without rendering scene content."""

        return self._record_prompt(
            SceneStatePrompt(reason=str(reason or "suppressed")[:48])
        )

    def status(self, *, live_mode: str = "") -> dict[str, int | str | bool]:
        self._sync_mode(str(live_mode or ""))
        self._expire_if_stale()
        active = self._mode == "solo_stream" and self._phase not in {
            "idle",
            "transition",
        }
        return {
            "scene_state_version": SCENE_STATE_VERSION,
            "scene_state_active": active,
            "scene_state_phase": self._phase if active else "idle",
            "scene_state_thread_key": self._thread_key if active else "",
            "scene_state_viewer_turn_count": (
                self._viewer_turn_count if active else 0
            ),
            "scene_state_viewer_response_count": (
                self._viewer_response_count if active else 0
            ),
            "scene_state_transition_count": self._transition_count,
            "scene_state_expired_count": self._expired_count,
            "scene_state_prompt_uses": self._prompt_uses,
            "scene_state_prompt_omits": self._prompt_omits,
            "scene_state_prompt_last_chars": self._prompt_last_chars,
            "scene_state_prompt_last_reason": self._prompt_last_reason,
        }

    def _start_host_beat(self, source: str, event: Mapping[str, Any]) -> None:
        if source == "warmup_hosting":
            thread_key = "opening"
            phase = "setup"
        elif source == "active_engagement":
            thread_key = self._public_thread_key(event.get("topic_shape"))
            phase = "viewer_choice"
        else:
            thread_key = self._public_thread_key(event.get("host_beat_shape"))
            phase = (
                "viewer_choice"
                if thread_key in _VIEWER_CHOICE_SHAPES
                else "setup"
            )
        self._thread_key = thread_key
        self._viewer_turn_count = 0
        self._viewer_response_count = 0
        self._set_phase(phase)

    def _set_phase(self, phase: str) -> None:
        if phase != self._phase:
            self._transition_count += 1
        self._phase = phase
        self._updated_at = self._safe_now()

    def _sync_mode(self, mode: str) -> None:
        normalized = mode if mode in {"solo_stream", "co_stream"} else ""
        if not normalized:
            return
        if self._mode and normalized != self._mode:
            if self._phase not in {"idle", "transition"}:
                self._transition_count += 1
            self._clear_scene()
        self._mode = normalized

    def _expire_if_stale(self) -> None:
        if self._phase in {"idle", "transition"} or self._updated_at <= 0.0:
            return
        age = self._safe_now() - self._updated_at
        if age <= SCENE_STATE_TTL_SECONDS:
            return
        self._clear_scene()
        self._expired_count += 1

    def _record_prompt(self, prompt: SceneStatePrompt) -> SceneStatePrompt:
        self._prompt_last_chars = min(
            prompt.characters,
            SCENE_STATE_PROMPT_MAX_CHARS,
        )
        self._prompt_last_reason = prompt.reason[:48]
        if prompt.text:
            self._prompt_uses += 1
        else:
            self._prompt_omits += 1
        return prompt

    def _safe_now(self) -> float:
        try:
            value = float(self._now())
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return max(0.0, value) if math.isfinite(value) else 0.0

    @staticmethod
    def _public_thread_key(value: Any) -> str:
        key = str(value or "").strip()
        return key if key in _PUBLIC_THREAD_KEYS else ""

    @staticmethod
    def _result_key(result: Mapping[str, Any], event: Mapping[str, Any]) -> str:
        trace_id = str(result.get("trace_id") or event.get("trace_id") or "")
        if trace_id:
            return f"trace:{trace_id[:80]}"
        created_at = str(result.get("created_at") or "")
        source = str(event.get("source") or "")
        return f"result:{created_at[:48]}:{source[:32]}" if created_at else ""

    @staticmethod
    def _is_support_result(
        result: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> bool:
        event_type = str(
            result.get("support_event_type") or event.get("event_type") or ""
        ).casefold()
        return event_type in _SUPPORT_EVENT_TYPES

    @staticmethod
    def _is_viewer_event(event: Any) -> bool:
        source = str(getattr(event, "source", "") or "")
        raw = getattr(event, "raw", None)
        event_type = (
            str(raw.get("event_type") or "").casefold()
            if isinstance(raw, Mapping)
            else ""
        )
        return source in _VIEWER_SOURCES and event_type not in _SUPPORT_EVENT_TYPES

    @staticmethod
    def _is_active_hook_answer(event: Any) -> bool:
        raw = getattr(event, "raw", None)
        return isinstance(raw, Mapping) and (
            raw.get("danmaku_context_hint") == "active_hook_answer"
        )
