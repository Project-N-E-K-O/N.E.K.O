from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentSceneTracker:
    _SUMMARY_SCENE_STATE_LIMIT = 32

    def __init__(self, *, seen_line_limit: int) -> None:
        self.scene_memory: list[dict[str, Any]] = []
        self.choice_memory: list[dict[str, Any]] = []
        self.recent_pushes: list[dict[str, Any]] = []
        self.summary_seen_line_keys: set[str] = set()
        self._summary_seen_line_key_order: list[str] = []
        self.summary_lines_since_push = 0
        self.summary_scene_id = ""
        self.summary_route_id = ""
        self.summary_scene_scope_key = ""
        self.summary_scene_states: dict[str, dict[str, Any]] = {}
        self.summary_last_processed_event_seq = 0
        self._seen_line_limit = max(1, int(seen_line_limit))
        self._summary_schedule_owner_counter = 0

    def reset(self, *, scene_id: str = "") -> None:
        self.scene_memory.clear()
        self.choice_memory.clear()
        self.recent_pushes.clear()
        self.summary_scene_states.clear()
        self.summary_last_processed_event_seq = 0
        self.reset_summary(scene_id=scene_id)

    def reset_summary(self, *, scene_id: str = "", route_id: str = "") -> None:
        self.sync_current_scene_summary_mirror(scene_id, route_id=route_id)

    @staticmethod
    def summary_scope_key(scene_id: str, route_id: str = "") -> str:
        normalized_scene_id = str(scene_id or "")
        normalized_route_id = str(route_id or "")
        if not normalized_route_id:
            return normalized_scene_id
        return f"{normalized_scene_id}\x1f{normalized_route_id}"

    def remember_line_key(self, key: str) -> bool:
        if not key or key in self.summary_seen_line_keys:
            return False
        self.summary_seen_line_keys.add(key)
        self._summary_seen_line_key_order.append(key)
        self._trim_seen_line_window(
            self.summary_seen_line_keys,
            self._summary_seen_line_key_order,
        )
        return True

    def state_for_scene(
        self,
        scene_id: str,
        *,
        route_id: str = "",
    ) -> dict[str, Any]:
        normalized_scene_id = str(scene_id or "")
        normalized_route_id = str(route_id or "")
        scope_key = self.summary_scope_key(
            normalized_scene_id,
            normalized_route_id,
        )
        state = self.summary_scene_states.get(scope_key)
        if state is None:
            state = {
                "scene_id": normalized_scene_id,
                "route_id": normalized_route_id,
                "scope_key": scope_key,
                "seen_line_keys": set(),
                "seen_line_key_order": [],
                "pending_line_key_order": [],
                "pending_line_occurrences": {},
                "scheduled_line_keys_by_owner": {},
                "scheduled_line_occurrences_by_owner": {},
                "lines_since_push": 0,
                "last_line_seq": 0,
                "last_line_ts": "",
                "last_scheduled_seq": 0,
                "last_schedule_owner_token": 0,
                "active_schedule_owner_tokens": set(),
                "scheduled_in_flight_count": 0,
                "pending_since_monotonic": 0.0,
            }
            self.summary_scene_states[scope_key] = state
            self._trim_scene_states(preserve_scope_key=scope_key)
        return state

    def remember_scene_line(
        self,
        scene_id: str,
        key: str,
        *,
        route_id: str = "",
        seq: int,
        ts: str,
        now_monotonic: float = 0.0,
        occurrence: dict[str, Any] | None = None,
    ) -> bool:
        if not scene_id or not key:
            return False
        state = self.state_for_scene(scene_id, route_id=route_id)
        seen_line_keys = state.get("seen_line_keys")
        if not isinstance(seen_line_keys, set):
            seen_line_keys = set(seen_line_keys or [])
            state["seen_line_keys"] = seen_line_keys
        seen_line_key_order = state.get("seen_line_key_order")
        if not isinstance(seen_line_key_order, list):
            seen_line_key_order = [
                str(item) for item in seen_line_keys if str(item)
            ]
            state["seen_line_key_order"] = seen_line_key_order
        if key in seen_line_keys:
            return False
        seen_line_keys.add(key)
        seen_line_key_order.append(key)
        self._trim_seen_line_window(seen_line_keys, seen_line_key_order)
        pending_line_key_order = state.get("pending_line_key_order")
        if not isinstance(pending_line_key_order, list):
            pending_line_key_order = [
                str(item)
                for item in list(pending_line_key_order or [])
                if str(item)
            ]
            state["pending_line_key_order"] = pending_line_key_order
        pending_line_key_order.append(key)
        if isinstance(occurrence, dict):
            pending_line_occurrences = state.get("pending_line_occurrences")
            if not isinstance(pending_line_occurrences, dict):
                pending_line_occurrences = {}
                state["pending_line_occurrences"] = pending_line_occurrences
            stored_occurrence = dict(occurrence)
            line = occurrence.get("line")
            if isinstance(line, dict):
                stored_occurrence["line"] = dict(line)
            pending_line_occurrences[key] = stored_occurrence
        lines_since_push = int(state.get("lines_since_push") or 0)
        if lines_since_push <= 0:
            state["pending_since_monotonic"] = float(now_monotonic or 0.0)
        state["lines_since_push"] = lines_since_push + 1
        state["last_line_seq"] = max(int(state.get("last_line_seq") or 0), int(seq or 0))
        state["last_line_ts"] = str(ts or "")
        self.sync_current_scene_summary_mirror(
            self.summary_scene_id,
            route_id=self.summary_route_id,
        )
        self._trim_scene_states()
        return True

    def mark_scene_summary_scheduled(
        self,
        scene_id: str,
        *,
        route_id: str = "",
        seq: int,
    ) -> int:
        state = self.state_for_scene(scene_id, route_id=route_id)
        self._summary_schedule_owner_counter += 1
        owner_token = self._summary_schedule_owner_counter
        active_owner_tokens = self._active_scene_summary_schedule_owners(state)
        active_owner_tokens.add(owner_token)
        scheduled_line_count = int(state.get("lines_since_push") or 0)
        pending_line_key_order = [
            str(item)
            for item in list(state.get("pending_line_key_order") or [])
            if str(item)
        ]
        state["pending_line_key_order"] = pending_line_key_order
        scheduled_line_keys_by_owner = state.get("scheduled_line_keys_by_owner")
        if not isinstance(scheduled_line_keys_by_owner, dict):
            scheduled_line_keys_by_owner = {}
            state["scheduled_line_keys_by_owner"] = scheduled_line_keys_by_owner
        if scheduled_line_count > 0 and pending_line_key_order:
            scheduled_line_keys = list(pending_line_key_order)
        elif scheduled_line_count > 0:
            seen_line_key_order = [
                str(item)
                for item in list(state.get("seen_line_key_order") or [])
                if str(item)
            ]
            scheduled_line_keys = list(seen_line_key_order[-scheduled_line_count:])
        else:
            scheduled_line_keys = []
        scheduled_line_key_set = set(scheduled_line_keys)
        state["pending_line_key_order"] = [
            key
            for key in pending_line_key_order
            if key not in scheduled_line_key_set
        ]
        scheduled_line_keys_by_owner[owner_token] = scheduled_line_keys
        pending_line_occurrences = state.get("pending_line_occurrences")
        scheduled_line_occurrences_by_owner = state.get(
            "scheduled_line_occurrences_by_owner"
        )
        if not isinstance(scheduled_line_occurrences_by_owner, dict):
            scheduled_line_occurrences_by_owner = {}
            state["scheduled_line_occurrences_by_owner"] = (
                scheduled_line_occurrences_by_owner
            )
        scheduled_occurrences: dict[str, dict[str, Any]] = {}
        if isinstance(pending_line_occurrences, dict):
            for key in scheduled_line_keys:
                pending = pending_line_occurrences.pop(str(key), None)
                if isinstance(pending, dict):
                    scheduled_occurrences[str(key)] = pending
        scheduled_line_occurrences_by_owner[owner_token] = scheduled_occurrences
        state["lines_since_push"] = 0
        state["last_scheduled_seq"] = int(seq or 0)
        state["last_schedule_owner_token"] = owner_token
        state["scheduled_in_flight_count"] = len(active_owner_tokens)
        self.sync_current_scene_summary_mirror(
            self.summary_scene_id,
            route_id=self.summary_route_id,
        )
        return owner_token

    def mark_scene_summary_delivered(
        self,
        scene_id: str,
        *,
        route_id: str = "",
        seq: int,
        owner_token: int,
    ) -> None:
        state = self.summary_scene_states.get(
            self.summary_scope_key(scene_id, route_id)
        )
        if not isinstance(state, dict):
            return
        if not self._release_scene_summary_schedule(state, owner_token=owner_token):
            return
        scheduled_line_keys_by_owner = state.get("scheduled_line_keys_by_owner")
        scheduled_line_keys = (
            list(scheduled_line_keys_by_owner.pop(owner_token, []))
            if isinstance(scheduled_line_keys_by_owner, dict)
            else []
        )
        scheduled_line_occurrences_by_owner = state.get(
            "scheduled_line_occurrences_by_owner"
        )
        if isinstance(scheduled_line_occurrences_by_owner, dict):
            scheduled_line_occurrences_by_owner.pop(owner_token, None)
        pending_line_occurrences = state.get("pending_line_occurrences")
        if isinstance(pending_line_occurrences, dict):
            for key in scheduled_line_keys:
                pending_line_occurrences.pop(str(key), None)
        pending_line_key_order = state.get("pending_line_key_order")
        if isinstance(pending_line_key_order, list):
            scheduled_line_key_set = {str(key) for key in scheduled_line_keys}
            state["pending_line_key_order"] = [
                str(key)
                for key in pending_line_key_order
                if str(key) not in scheduled_line_key_set
            ]
        if int(state.get("last_schedule_owner_token") or 0) != int(owner_token or 0):
            self.sync_current_scene_summary_mirror(
                self.summary_scene_id,
                route_id=self.summary_route_id,
            )
            return
        scheduled_seq = int(seq or 0)
        state["last_scheduled_seq"] = int(seq or 0)
        if int(state.get("lines_since_push") or 0) <= 0:
            state["pending_since_monotonic"] = 0.0
        self.sync_current_scene_summary_mirror(
            self.summary_scene_id,
            route_id=self.summary_route_id,
        )

    def restore_scene_summary_schedule(
        self,
        scene_id: str,
        *,
        route_id: str = "",
        seq: int,
        lines_since_push: int,
        owner_token: int,
    ) -> None:
        state = self.summary_scene_states.get(
            self.summary_scope_key(scene_id, route_id)
        )
        if not isinstance(state, dict):
            return
        if not self._release_scene_summary_schedule(state, owner_token=owner_token):
            return
        scheduled_line_keys_by_owner = state.get("scheduled_line_keys_by_owner")
        scheduled_line_keys = (
            list(scheduled_line_keys_by_owner.pop(owner_token, []))
            if isinstance(scheduled_line_keys_by_owner, dict)
            else []
        )
        scheduled_line_occurrences_by_owner = state.get(
            "scheduled_line_occurrences_by_owner"
        )
        scheduled_line_occurrences = (
            dict(scheduled_line_occurrences_by_owner.pop(owner_token, {}))
            if isinstance(scheduled_line_occurrences_by_owner, dict)
            else {}
        )
        if int(state.get("last_schedule_owner_token") or 0) != int(owner_token or 0):
            self.sync_current_scene_summary_mirror(
                self.summary_scene_id,
                route_id=self.summary_route_id,
            )
            return
        pending_line_occurrences = state.get("pending_line_occurrences")
        if not isinstance(pending_line_occurrences, dict):
            pending_line_occurrences = {}
            state["pending_line_occurrences"] = pending_line_occurrences
        pending_line_key_order = state.get("pending_line_key_order")
        if not isinstance(pending_line_key_order, list):
            pending_line_key_order = [
                str(item)
                for item in list(pending_line_key_order or [])
                if str(item)
            ]
        scheduled_line_key_set = {str(key) for key in scheduled_line_keys}
        state["pending_line_key_order"] = [
            str(key) for key in scheduled_line_keys if str(key)
        ] + [
            str(key)
            for key in pending_line_key_order
            if str(key) and str(key) not in scheduled_line_key_set
        ]
        for key in scheduled_line_keys:
            occurrence = scheduled_line_occurrences.get(str(key))
            if isinstance(occurrence, dict):
                pending_line_occurrences[str(key)] = occurrence
        state["lines_since_push"] = (
            int(state.get("lines_since_push") or 0)
            + int(lines_since_push or 0)
        )
        state["last_scheduled_seq"] = 0
        self.sync_current_scene_summary_mirror(
            self.summary_scene_id,
            route_id=self.summary_route_id,
        )

    def discard_scene_summary_schedule(
        self,
        scene_id: str,
        *,
        route_id: str = "",
        seq: int,
        owner_token: int,
    ) -> None:
        state = self.summary_scene_states.get(
            self.summary_scope_key(scene_id, route_id)
        )
        if not isinstance(state, dict):
            return
        if not self._release_scene_summary_schedule(state, owner_token=owner_token):
            return
        scheduled_line_keys_by_owner = state.get("scheduled_line_keys_by_owner")
        scheduled_line_keys = (
            list(scheduled_line_keys_by_owner.pop(owner_token, []))
            if isinstance(scheduled_line_keys_by_owner, dict)
            else []
        )
        scheduled_line_occurrences_by_owner = state.get(
            "scheduled_line_occurrences_by_owner"
        )
        if isinstance(scheduled_line_occurrences_by_owner, dict):
            scheduled_line_occurrences_by_owner.pop(owner_token, None)
        pending_line_occurrences = state.get("pending_line_occurrences")
        if isinstance(pending_line_occurrences, dict):
            for key in scheduled_line_keys:
                pending_line_occurrences.pop(str(key), None)
        pending_line_key_order = state.get("pending_line_key_order")
        if isinstance(pending_line_key_order, list):
            scheduled_line_key_set = {str(key) for key in scheduled_line_keys}
            state["pending_line_key_order"] = [
                str(key)
                for key in pending_line_key_order
                if str(key) not in scheduled_line_key_set
            ]
        if int(state.get("last_schedule_owner_token") or 0) == int(owner_token or 0):
            state["last_scheduled_seq"] = 0
        if int(state.get("lines_since_push") or 0) <= 0:
            state["pending_since_monotonic"] = 0.0
        self.sync_current_scene_summary_mirror(
            self.summary_scene_id,
            route_id=self.summary_route_id,
        )

    @staticmethod
    def _active_scene_summary_schedule_owners(state: dict[str, Any]) -> set[int]:
        active_owner_tokens = state.get("active_schedule_owner_tokens")
        if not isinstance(active_owner_tokens, set):
            active_owner_tokens = {
                int(item)
                for item in list(active_owner_tokens or [])
                if int(item or 0) > 0
            }
            state["active_schedule_owner_tokens"] = active_owner_tokens
        return active_owner_tokens

    @classmethod
    def _release_scene_summary_schedule(
        cls,
        state: dict[str, Any],
        *,
        owner_token: int,
    ) -> bool:
        normalized_owner_token = int(owner_token or 0)
        active_owner_tokens = cls._active_scene_summary_schedule_owners(state)
        if normalized_owner_token <= 0 or normalized_owner_token not in active_owner_tokens:
            return False
        active_owner_tokens.remove(normalized_owner_token)
        state["scheduled_in_flight_count"] = len(active_owner_tokens)
        return True

    def current_scene_lines_since_push(
        self,
        scene_id: str,
        *,
        route_id: str = "",
    ) -> int:
        state = self.summary_scene_states.get(
            self.summary_scope_key(scene_id, route_id)
        )
        if not isinstance(state, dict):
            return 0
        return int(state.get("lines_since_push") or 0)

    def sync_current_scene_summary_mirror(
        self,
        scene_id: str,
        *,
        route_id: str = "",
    ) -> None:
        normalized_scene_id = str(scene_id or "")
        normalized_route_id = str(route_id or "")
        scope_key = self.summary_scope_key(
            normalized_scene_id,
            normalized_route_id,
        )
        self.summary_scene_id = normalized_scene_id
        self.summary_route_id = normalized_route_id
        self.summary_scene_scope_key = scope_key
        state = self.summary_scene_states.get(scope_key)
        if not isinstance(state, dict):
            self.summary_seen_line_keys = set()
            self._summary_seen_line_key_order = []
            self.summary_lines_since_push = 0
            return
        seen_line_keys = state.get("seen_line_keys")
        if not isinstance(seen_line_keys, set):
            seen_line_keys = set(seen_line_keys or [])
            state["seen_line_keys"] = seen_line_keys
        seen_line_key_order = state.get("seen_line_key_order")
        if not isinstance(seen_line_key_order, list):
            seen_line_key_order = [str(item) for item in seen_line_keys if str(item)]
            state["seen_line_key_order"] = seen_line_key_order
        self._trim_seen_line_window(seen_line_keys, seen_line_key_order)
        self.summary_seen_line_keys = set(seen_line_keys)
        self._summary_seen_line_key_order = list(seen_line_key_order)
        self.summary_lines_since_push = int(state.get("lines_since_push") or 0)

    def summary_scene_statuses(
        self,
        *,
        current_scene_id: str = "",
        current_route_id: str = "",
    ) -> list[dict[str, Any]]:
        current = str(current_scene_id or "")
        current_route = str(current_route_id or "")
        items: list[dict[str, Any]] = []
        for state in self.summary_scene_states.values():
            seen_line_keys = state.get("seen_line_keys")
            scene_id = str(state.get("scene_id") or "")
            route_id = str(state.get("route_id") or "")
            items.append(
                {
                    "scene_id": scene_id,
                    "route_id": route_id,
                    "is_current": scene_id == current and route_id == current_route,
                    "seen_line_count": len(seen_line_keys)
                    if isinstance(seen_line_keys, set)
                    else 0,
                    "lines_since_push": int(state.get("lines_since_push") or 0),
                    "last_line_seq": int(state.get("last_line_seq") or 0),
                    "last_line_ts": str(state.get("last_line_ts") or ""),
                    "last_scheduled_seq": int(state.get("last_scheduled_seq") or 0),
                    "scheduled_in_flight_count": int(
                        state.get("scheduled_in_flight_count") or 0
                    ),
                }
            )
        return items[-self._SUMMARY_SCENE_STATE_LIMIT :]

    def _trim_scene_states(self, *, preserve_scope_key: str = "") -> None:
        while len(self.summary_scene_states) > self._SUMMARY_SCENE_STATE_LIMIT:
            removable_scope_key = ""
            for scope_key, state in self.summary_scene_states.items():
                if scope_key in {
                    self.summary_scene_scope_key,
                    str(preserve_scope_key or ""),
                }:
                    continue
                if (
                    int(state.get("lines_since_push") or 0) <= 0
                    and int(state.get("scheduled_in_flight_count") or 0) <= 0
                ):
                    removable_scope_key = scope_key
                    break
            if not removable_scope_key:
                # The fixed limit is a pruning target, not permission to drop
                # live facts that have not reached an archive threshold yet.
                break
            self.summary_scene_states.pop(removable_scope_key, None)

    def _trim_seen_line_window(
        self,
        seen_line_keys: set[str],
        seen_line_key_order: list[str],
    ) -> None:
        deduped_order: list[str] = []
        order_seen: set[str] = set()
        for item in seen_line_key_order:
            key = str(item or "")
            if not key or key in order_seen or key not in seen_line_keys:
                continue
            order_seen.add(key)
            deduped_order.append(key)
        for key in seen_line_keys:
            if key not in order_seen:
                order_seen.add(key)
                deduped_order.append(key)
        seen_line_key_order[:] = deduped_order
        while len(seen_line_key_order) > self._seen_line_limit:
            removed = seen_line_key_order.pop(0)
            seen_line_keys.discard(removed)

    def replace_scene_summary(
        self,
        *,
        scene_id: str,
        route_id: str,
        summary: str,
    ) -> None:
        if not scene_id or not summary:
            return
        for item in reversed(self.scene_memory):
            if str(item.get("scene_id") or "") != scene_id:
                continue
            item["summary"] = summary
            if route_id:
                item["route_id"] = route_id
            return
