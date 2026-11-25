# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""``<state>/subscriptions.json``: which subscriptions are on and which tiers they take.

Shape::

    {"version": 1, "plans": {"<plan id>": {
        "enabled": true, "tiers": {"<tier>": "<model id>"},
        "api_key": "...", "region": "..."}}}

Every write is one small change under a cross-process file lock (read, change,
atomic replace), so a stale settings page only ever changes the one thing the
user clicked. A tier belongs to at most one plan. Keys never leave this module
unmasked except through ``overlay`` (to the request itself).
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from filelock import FileLock

from .plans import TEXT_TIERS

VERSION = 1


class SubscriptionError(ValueError):
    """A change the store refuses; the message is safe to show, ``code`` names it for the panel."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def _empty() -> dict[str, Any]:
    return {"version": VERSION, "plans": {}}


def parse(text: str | None) -> dict[str, Any]:
    """The document from file text; anything unreadable counts as no subscriptions."""
    if not text:
        return _empty()
    try:
        value = json.loads(text)
    except ValueError:
        return _empty()
    if not isinstance(value, dict) or not isinstance(value.get("plans"), dict):
        return _empty()
    plans = {}
    for plan_id, entry in value["plans"].items():
        if not isinstance(plan_id, str) or not isinstance(entry, dict):
            continue
        tiers = entry.get("tiers") if isinstance(entry.get("tiers"), dict) else {}
        plans[plan_id] = {
            "enabled": entry.get("enabled") is True,
            "tiers": {
                tier: model.strip()
                for tier, model in tiers.items()
                if tier in TEXT_TIERS and isinstance(model, str) and model.strip()
            },
            "api_key": entry["api_key"] if isinstance(entry.get("api_key"), str) else "",
            "region": entry["region"] if isinstance(entry.get("region"), str) else "",
        }
    return {"version": VERSION, "plans": plans}


class SubscriptionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = FileLock(str(self.path) + ".lock", timeout=30, mode=0o600)

    def read(self) -> dict[str, Any]:
        """Current document, through the hot-path read cache."""
        from utils.config_manager import read_cache

        try:
            return parse(read_cache.read_text(self.path))
        except (OSError, UnicodeDecodeError):
            return _empty()

    def _read_fresh(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty()
        try:
            value = json.loads(text)
        except ValueError:
            raise SubscriptionError(
                "Subscription settings are unreadable. Fix or remove subscriptions.json.", "unreadable"
            ) from None
        if not isinstance(value, dict) or not isinstance(value.get("plans"), dict):
            raise SubscriptionError(
                "Subscription settings are unreadable. Fix or remove subscriptions.json.", "unreadable"
            )
        return parse(text)

    def _write(self, value: dict[str, Any]) -> None:
        from utils.config_manager import read_cache

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".subscriptions-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
            read_cache.invalidate(self.path)

    def _change(self, mutate) -> Any:
        with self.lock:
            value = self._read_fresh()
            result = mutate(value)
            self._write(value)
            return result

    @staticmethod
    def _entry(value: dict[str, Any], plan_id: str) -> dict[str, Any]:
        return value["plans"].setdefault(
            plan_id, {"enabled": False, "tiers": {}, "api_key": "", "region": ""}
        )

    def set_enabled(self, plan_id: str, enabled: bool) -> None:
        def mutate(value):
            self._entry(value, plan_id)["enabled"] = bool(enabled)

        self._change(mutate)

    def set_tier(self, plan_id: str, tier: str, model: str | None) -> str | None:
        """Give ``tier`` to ``plan_id`` with ``model`` (``None`` takes it back).

        Returns the plan the tier was moved away from, if any: a tier belongs
        to one plan at a time. A plan left with no tier is turned off in the same
        write, so it never reads as on while taking nothing over.
        """
        if tier not in TEXT_TIERS:
            raise SubscriptionError("Unknown model tier.")
        model = (model or "").strip() or None

        def mutate(value):
            moved_from = None
            if model is not None:
                for other_id, other in value["plans"].items():
                    if other_id != plan_id and other["tiers"].pop(tier, None) is not None:
                        moved_from = other_id
                self._entry(value, plan_id)["tiers"][tier] = model
            else:
                self._entry(value, plan_id)["tiers"].pop(tier, None)
            for affected in {plan_id, moved_from} - {None}:
                entry = value["plans"].get(affected)
                if entry and entry.get("enabled") and not entry.get("tiers"):
                    entry["enabled"] = False
            return moved_from

        return self._change(mutate)

    def set_key(self, plan_id: str, api_key: str, region: str | None = None) -> None:
        """Store the plan's key; an empty key removes it and turns the plan off."""
        api_key = (api_key or "").strip()

        def mutate(value):
            entry = self._entry(value, plan_id)
            entry["api_key"] = api_key
            if region is not None:
                entry["region"] = region
            if not api_key:
                entry["enabled"] = False

        self._change(mutate)

    def set_region(self, plan_id: str, region: str) -> None:
        def mutate(value):
            self._entry(value, plan_id)["region"] = region

        self._change(mutate)

    def masked(self) -> dict[str, Any]:
        """The document for the settings page: keys reduced to whether one is set."""
        value = deepcopy(self.read())
        for entry in value["plans"].values():
            entry["has_key"] = bool(entry.pop("api_key", ""))
        return value


def get_store() -> SubscriptionStore:
    from utils.config_manager import get_config_manager

    return SubscriptionStore(get_config_manager().local_state_dir / "subscriptions.json")
