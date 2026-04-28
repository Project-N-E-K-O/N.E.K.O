from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional

from .models import normalize_snapshot


class ContextFlowMixin:
    def _publish_snapshot(self, snapshot: Dict[str, Any], *, record_history: bool) -> Dict[str, Any]:
        self._snapshot = snapshot
        self._server_state = "connected"
        self._last_error = ""
        self._last_poll_at = time.time()
        if record_history:
            self._history.appendleft({
                "type": "snapshot",
                "time": self._last_poll_at,
                "screen": self._snapshot.get("screen"),
                "available_actions": self._snapshot.get("available_action_count", 0),
            })
            self._recent_snapshot_log.appendleft(self._build_review_snapshot_summary(snapshot, timestamp=self._last_poll_at))
        self._emit_status()
        return snapshot

    async def _fetch_step_context(self, *, publish: bool = False, record_history: bool = False) -> Dict[str, Any]:
        client = self._require_client()
        state_payload = await client.get_state()
        actions_payload = await client.get_available_actions()
        snapshot = normalize_snapshot(state_payload, actions_payload)
        if publish:
            self._publish_snapshot(snapshot, record_history=record_history)
        return {
            "snapshot": snapshot,
            "actions": snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else [],
            "signature": self._snapshot_signature(snapshot),
            "action_signature": self._action_signature(snapshot),
            "state_signature": self._state_signature(snapshot),
            "captured_at": time.time(),
        }

    def _snapshot_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        return (
            snapshot.get("screen"),
            snapshot.get("floor"),
            snapshot.get("act"),
            bool(snapshot.get("in_combat", False)),
            snapshot.get("available_action_count", 0),
            self._action_signature(snapshot),
            self._state_signature(snapshot),
        )

    def _action_signature(self, snapshot: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        return tuple(self._action_fingerprint(action) for action in actions if isinstance(action, dict))

    def _action_fingerprint(self, action: dict[str, Any]) -> tuple[Any, ...]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        return (
            str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or ""),
            raw.get("option_index"),
            raw.get("index"),
            raw.get("card_index"),
            raw.get("target_index"),
            raw.get("name"),
        )

    def _state_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        hand_signature = tuple(
            (
                card.get("index"),
                card.get("uuid"),
                card.get("id"),
                card.get("name"),
                bool(card.get("playable")),
                tuple(card.get("valid_target_indices")) if isinstance(card.get("valid_target_indices"), list) else (),
            )
            for card in hand
            if isinstance(card, dict)
        )
        potion_signature = tuple(
            (
                potion.get("index"),
                potion.get("id"),
                potion.get("name"),
                bool(potion.get("can_use")),
                bool(potion.get("can_discard")),
            )
            for potion in potions
            if isinstance(potion, dict)
        )
        return (
            raw_state.get("screen"),
            raw_state.get("screen_type"),
            raw_state.get("floor"),
            raw_state.get("act_floor"),
            raw_state.get("act"),
            raw_state.get("turn"),
            raw_state.get("turn_count"),
            raw_state.get("phase"),
            bool(raw_state.get("in_combat", False)),
            combat.get("turn"),
            combat.get("turn_count"),
            combat.get("player_energy"),
            combat.get("end_turn_available"),
            hand_signature,
            potion_signature,
        )

    def _is_actionable_context(self, context: dict[str, Any]) -> bool:
        return bool(context["actions"])

    def _is_transitional_context(self, context: dict[str, Any]) -> bool:
        snapshot = context["snapshot"]
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = self._normalized_screen_name(snapshot)
        in_combat = bool(snapshot.get("in_combat", False) or raw_state.get("in_combat", False))
        if context["actions"]:
            return False
        if in_combat:
            return screen == "combat"
        return self._is_eventish_screen(screen)

    def _normalized_screen_name(self, snapshot: dict[str, Any]) -> str:
        return self._context_analyzer._normalized_screen_name(snapshot)

    def _is_eventish_screen(self, screen: str) -> bool:
        return self._context_analyzer._is_eventish_screen(screen)

    async def _await_stable_step_context(self) -> Dict[str, Any]:
        attempts = max(2, int(self._cfg.get("stable_state_attempts", 4) or 4))
        delay = max(0.1, float(self._cfg.get("poll_interval_active_seconds", 1) or 1) / 2)
        previous: Optional[Dict[str, Any]] = None
        last_context: Optional[Dict[str, Any]] = None
        for attempt in range(attempts):
            context = await self._fetch_step_context(publish=(attempt == 0), record_history=(attempt == 0))
            last_context = context
            if previous is not None and context["signature"] == previous["signature"]:
                return context
            if self._is_actionable_context(context) and not self._is_transitional_context(context):
                return context
            if not self._is_transitional_context(context) and attempt == attempts - 1:
                return context
            previous = context
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        return last_context or await self._fetch_step_context(publish=True, record_history=True)

    def _prepare_action_request(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        raw_action = raw.get("action")
        action_type = str(action.get("type") or raw.get("type") or (raw_action if isinstance(raw_action, str) else ""))
        template_raw = dict(raw)
        if action_type in {"choose_reward_card", "select_deck_card"}:
            template_raw.pop("option_index", None)
        kwargs = self._normalize_action_kwargs(action_type, template_raw, context)
        prepared = {
            "action": action,
            "action_type": action_type,
            "kwargs": kwargs,
            "fingerprint": self._action_fingerprint(action),
            "context_signature": context["signature"],
            "context": context,
        }
        self._log_prepared_action(prepared, context)
        return prepared

    def _log_action_decision(self, source: str, action: dict[str, Any], context: dict[str, Any]) -> None:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        self.logger.info(
            f"[sts2_autoplay][decision] source={source} screen={screen} action={self._summarize_action(action, context)} available_actions={self._summarize_actions(context)}"
        )

    def _log_prepared_action(self, prepared: dict[str, Any], context: dict[str, Any]) -> None:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        self.logger.info(
            f"[sts2_autoplay][prepared] screen={screen} prepared={{'action_type': {prepared.get('action_type')!r}, 'kwargs': {prepared.get('kwargs')!r}, 'fingerprint': {prepared.get('fingerprint')!r}}} action={self._summarize_action(prepared.get('action'), context)} available_actions={self._summarize_actions(context)}"
        )

    def _summarize_action(self, action: Any, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict):
            return {}
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
        return {
            "type": action_type,
            "label": action.get("label") or raw.get("label") or raw.get("description") or raw.get("name") or "",
            "raw": {
                key: value
                for key, value in raw.items()
                if key in {"name", "type", "option_index", "index", "card_index", "target_index", "requires_index", "requires_target"}
            },
            "allowed_kwargs": self._allowed_kwargs_for_action(action_type, raw, context),
        }

    def _summarize_actions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        return [self._summarize_action(action, context) for action in actions if isinstance(action, dict)]

    async def _revalidate_prepared_action(self, prepared: dict[str, Any], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        actions = context["actions"]
        if not any(self._action_fingerprint(action) == prepared["fingerprint"] for action in actions if isinstance(action, dict)):
            return None
        latest = await self._fetch_step_context()
        if any(self._action_fingerprint(action) == prepared["fingerprint"] for action in latest["actions"] if isinstance(action, dict)):
            return prepared
        return None

    async def _await_action_interval(self) -> None:
        delay = max(0.0, float(self._cfg.get("action_interval_seconds", 0.5) or 0.5))
        if delay > 0:
            await asyncio.sleep(delay)

    async def _await_post_action_settle(self, before_context: dict[str, Any], prepared: dict[str, Any]) -> Dict[str, Any]:
        attempts = max(2, int(self._cfg.get("post_action_settle_attempts", 6) or 6))
        delay = max(0.1, float(self._cfg.get("post_action_delay_seconds", 0.5) or 0.5))
        last_context = before_context
        for attempt in range(attempts):
            if attempt > 0:
                await asyncio.sleep(delay)
            context = await self._fetch_step_context()
            last_context = context
            if context["signature"] != before_context["signature"]:
                if not self._is_transitional_context(context):
                    return context
                continue
            if not any(self._action_fingerprint(action) == prepared["fingerprint"] for action in context["actions"] if isinstance(action, dict)):
                return context
        return last_context
