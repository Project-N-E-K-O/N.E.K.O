"""Bounded in-memory publication of committed local recommendation policies.

SQLite/identity reads belong to the HTTP adapter's worker. These synchronous
guards only inspect memory, including at the final text/TTS publication point.
"""
from __future__ import annotations

import random
import time
from collections import OrderedDict
from copy import deepcopy
from threading import RLock

from main_logic.recommendation_policy import (
    COOLDOWN_SECONDS, canonical_category, decayed_decline_count, preference_factor,
)
from services.chat_sync import host


class PolicyRegistry:
    MAX_SCOPES = 2048

    def __init__(self, *, wall_clock=time.time, clock=time.monotonic, roll=random.random):
        self._wall_clock, self._clock, self._roll = wall_clock, clock, roll
        self._lock = RLock()
        self._states = OrderedDict()
        self._owners = OrderedDict()

    def observe(self, path, owner, *, incarnation=None):
        """Called under the credential fence, before a worker reads/writes SQL."""
        path = str(path)
        with self._lock:
            identity = self._owners.get(path)
            generation = host.saved_session_generation()
            if identity is None or identity[0] != owner["key"] or identity[3] is not generation or (
                incarnation is not None and identity[1] is not None
                and incarnation != identity[1]
            ):
                identity = (owner["key"], incarnation, object(), generation)
            elif identity[1] is None and incarnation is not None:
                identity = (identity[0], incarnation, identity[2], generation)
            self._owners[path] = identity
            self._owners.move_to_end(path)
            while len(self._owners) > self.MAX_SCOPES:
                self._owners.popitem(last=False)
            return identity[2]

    def publish(self, path, owner, character_uid, policy, *, incarnation=None, identity=None):
        """Merge committed revisions; delayed readers cannot undo a refusal."""
        path = str(path)
        key = (path, owner["key"], character_uid)
        if identity is None:
            identity = self.observe(path, owner, incarnation=incarnation)
        with self._lock:
            active = self._owners.get(path)
            if active is None or active[2] is not identity:
                return PolicyView(self, key, identity)
            current = self._states.get(key)
            # Delivery leases retire on credential writes, but the same
            # account's operational deadlines survive ordinary token refresh
            # and logout/login. Only a verified account replacement discards
            # its retained rows. Persisted identity arrives with the snapshot
            # even while the live capture provider is still bootstrapping.
            incarnation = active[1]
            if current is None or (
                current[0] is not None and incarnation is not None
                and current[0] != incarnation
            ):
                current = (incarnation, {})
            elif incarnation is None:
                incarnation = current[0]
            rows = dict(current[1])
            now, tick = self._wall_clock(), self._clock()
            for category, state in policy.items():
                previous = rows.get(category)
                if previous is not None and previous[0]["revision"] >= state["revision"]:
                    continue
                remaining = min(COOLDOWN_SECONDS, max(0.0, state["cooldown_until"] - now))
                deadline = tick + remaining
                if previous is not None and state["cooldown_until"] == previous[0]["cooldown_until"] and (
                    state["decline_count"] <= decayed_decline_count(previous[0], state["updated_at"])
                ):
                    # A positive response changes the soft preference only.
                    # Converting the unchanged wall deadline again after a
                    # clock adjustment must not restart the hard cooldown.
                    deadline = previous[1]
                rows[category] = (deepcopy(state), deadline)
            self._states[key] = (incarnation, rows)
            self._states.move_to_end(key)
            while len(self._states) > self.MAX_SCOPES:
                self._states.popitem(last=False)
            return PolicyView(self, key, identity)

    def _read(self, key, identity):
        with self._lock:
            owner = self._owners.get(key[0])
            state = self._states.get(key)
            if owner is None or owner[0] != key[1] or owner[2] is not identity or owner[3] is not host.saved_session_generation() or state is None:
                return None
            if owner[1] is not None and state[0] is not None and owner[1] != state[0]:
                return None
            return state[1]


class PolicyView:
    def __init__(self, registry, key, identity):
        self._registry, self._key, self._identity = registry, key, identity
        self._rolls = {}

    def filter_modes(self, modes):
        rows = self._registry._read(self._key, self._identity)
        if rows is None:
            return [], "PASS_USER_COOLDOWN"
        allowed, cooled = [], False
        for mode in modes:
            category = canonical_category(mode)
            row = rows.get(category)
            if row is None:
                allowed.append(mode)
                continue
            if self._registry._clock() < row[1]:
                cooled = True
                continue
            if category not in self._rolls:
                self._rolls[category] = self._registry._roll()
            if self._rolls[category] < preference_factor(row[0], self._registry._wall_clock()):
                allowed.append(mode)
        reason = None if allowed or not modes else (
            "PASS_USER_COOLDOWN" if cooled else "PASS_USER_PREFERENCE"
        )
        return allowed, reason

    def guard(self, categories):
        observed = frozenset(filter(None, map(canonical_category, categories)))

        def allowed():
            rows = self._registry._read(self._key, self._identity)
            if rows is None:
                return False
            tick = self._registry._clock()
            return all(category not in rows or tick >= rows[category][1] for category in observed)

        return allowed


registry = PolicyRegistry()
