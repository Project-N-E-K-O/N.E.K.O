"""The single exit toward the host.

Nothing else in the plugin is allowed to call `push_message`. That makes two
guarantees checkable in one place:

* in dry-run the pipeline runs all the way to a finished `DeliveryRequest` and
  then stops, with a host call count of exactly zero; and
* a real delivery is attempted once. There is no retry, because a stale battle
  call-out is worse than a missed one.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from ..domain.contracts import DeliveryRequest, DeliveryResult

REASON_DELIVERED = "delivered"
REASON_DRY_RUN = "dry_run"
REASON_EXPIRED = "expired"
REASON_PAUSED = "paused"
REASON_FAILED = "failed"

_TARGET_LANLAN_ENV = (
    "NEKO_WOWS_TARGET_LANLAN",
    "NEKO_TARGET_LANLAN",
    "NEKO_LANLAN_NAME",
    "NEKO_HER_NAME",
)


def _clean_target_lanlan(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()[:80]


def _walk_current_lanlan(root: Any) -> str:
    current = root
    seen: set[int] = set()
    for _ in range(8):
        if current is None:
            return ""
        identity = id(current)
        if identity in seen:
            return ""
        seen.add(identity)
        try:
            value = getattr(current, "_current_lanlan", None)
        except Exception:
            value = None
        target = _clean_target_lanlan(value)
        if target:
            return target
        try:
            current = getattr(current, "_host_ctx", None)
        except Exception:
            return ""
    return ""


def resolve_target_lanlan(plugin: Any) -> str:
    """Pick the character session a host push must name.

    An empty target is fine with one connected WebSocket (the host falls
    back), but with two or more the message is dropped. Prefer an explicit
    config, then the active role on the plugin context, then the selected
    catgirl in character settings.
    """
    cfg = getattr(plugin, "cfg", None)
    target = _clean_target_lanlan(getattr(cfg, "target_lanlan", ""))
    if target:
        return target

    for attribute in ("_host_ctx", "ctx"):
        try:
            root = getattr(plugin, attribute, None)
        except Exception:
            continue
        target = _walk_current_lanlan(root)
        if target:
            return target

    for env_name in _TARGET_LANLAN_ENV:
        target = _clean_target_lanlan(os.getenv(env_name, ""))
        if target:
            return target

    try:
        from utils.config_manager import get_config_manager

        character_data = get_config_manager().get_character_data()
    except Exception:
        return ""
    if isinstance(character_data, tuple) and len(character_data) >= 2:
        return _clean_target_lanlan(character_data[1])
    return ""


class NekoDispatcher:
    """Wraps `plugin.push_message` with dry-run, expiry and a failure fuse."""

    def __init__(self, plugin, cfg, *, logger=None, clock=time.monotonic) -> None:
        self._plugin = plugin
        self.cfg = cfg
        self.logger = logger
        self._clock = clock
        self._lock = threading.RLock()
        self._failures: list[float] = []
        self._paused = False
        self._pause_reason = ""
        # Counts host boundary crossings. Dry-run tests assert this stays at 0.
        self.host_calls = 0
        self.delivered = 0
        self.suppressed = 0

    # ------------------------------------------------------------------
    def apply_config(self, cfg) -> None:
        with self._lock:
            self.cfg = cfg

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self, reason: str = "manual") -> None:
        with self._lock:
            self._paused = True
            self._pause_reason = reason

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._pause_reason = ""
            self._failures.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paused": self._paused,
                "pause_reason": self._pause_reason,
                "recent_failures": len(self._failures),
                "failure_limit": self.cfg.safety_failure_limit,
                "host_calls": self.host_calls,
                "delivered": self.delivered,
                "suppressed": self.suppressed,
                "dry_run": bool(self.cfg.dry_run),
            }

    def reset_counters(self) -> None:
        """Called when delivery mode changes so counters describe one mode."""
        with self._lock:
            self.host_calls = 0
            self.delivered = 0
            self.suppressed = 0
            self._failures.clear()

    # ------------------------------------------------------------------
    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        now = self._clock()

        with self._lock:
            if self._paused:
                self.suppressed += 1
                return self._result(request, False, REASON_PAUSED, now)

            if request.expires_at and now >= request.expires_at:
                self.suppressed += 1
                return self._result(request, False, REASON_EXPIRED, now)

            if self.cfg.dry_run:
                # Everything above this line already ran. Stopping here is the
                # whole point of dry-run: a complete, inspectable request that
                # never reaches the host.
                self.suppressed += 1
                return self._result(request, False, REASON_DRY_RUN, now)

        expires_in_s = (
            max(0.0, request.expires_at - now)
            if request.expires_at
            else None
        )
        try:
            receipt = self._plugin.push_message(**request.push_kwargs(
                expires_in_s=expires_in_s))
        except Exception as exc:
            self._log("warning", f"push_message failed for {request.event_id}: {exc}")
            with self._lock:
                self.host_calls += 1
            self._note_failure(now)
            return self._result(request, False, REASON_FAILED, now, host_calls=1)

        if not _submission_accepted(receipt):
            reason = _submission_reason(receipt)
            self._log(
                "warning",
                f"push_message declined for {request.event_id}: {reason}",
            )
            with self._lock:
                self.host_calls += 1
            self._note_failure(now)
            return self._result(request, False, REASON_FAILED, now, host_calls=1)

        with self._lock:
            self.host_calls += 1
            self.delivered += 1
        return self._result(request, True, REASON_DELIVERED, now, host_calls=1)

    # ------------------------------------------------------------------
    def _note_failure(self, now: float) -> None:
        with self._lock:
            window = self.cfg.safety_window_seconds
            self._failures = [t for t in self._failures if now - t < window]
            self._failures.append(now)
            if len(self._failures) >= self.cfg.safety_failure_limit:
                self._paused = True
                self._pause_reason = (
                    f"{len(self._failures)} failed deliveries within "
                    f"{window:.0f}s; resume from the panel"
                )
                self._log("warning", f"output paused: {self._pause_reason}")

    def _result(
        self,
        request: DeliveryRequest,
        delivered: bool,
        reason: str,
        now: float,
        *,
        host_calls: int = 0,
    ) -> DeliveryResult:
        return DeliveryResult(
            delivered=delivered,
            reason=reason,
            event_id=request.event_id,
            lane=request.lane,
            at=now,
            host_calls=host_calls,
        )

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


class ContextInjector:
    """Sends the scene-setting instruction block, tracked so it can be undone."""

    def __init__(self, plugin, *, logger=None) -> None:
        self._plugin = plugin
        self.logger = logger
        self._lock = threading.RLock()
        # Last accepted scene text per character. Both text and target form the
        # injection identity: equal instructions sent to another manager are
        # still a distinct standing context that must later be restored.
        self._texts_by_target: dict[str, str] = {}
        self.host_calls = 0

    @property
    def injected(self) -> bool:
        with self._lock:
            return bool(self._texts_by_target)

    def push(self, text: str, *, dry_run: bool) -> bool:
        with self._lock:
            if dry_run:
                return False
            target = resolve_target_lanlan(self._plugin)
            if self._texts_by_target.get(target) == text:
                return False
            if self._send(text, target=target):
                self._texts_by_target[target] = text
                return True
            return False

    def restore(self, text: str, *, dry_run: bool) -> bool:
        # Cleanup is governed by whether context was actually injected. A user
        # may re-enable dry-run before the battle ends; that must not strand the
        # old scene instructions in the host.
        del dry_run
        with self._lock:
            targets = tuple(self._texts_by_target)
            if not targets:
                return False
            restored = 0
            for target in targets:
                if self._send(text, target=target):
                    self._texts_by_target.pop(target, None)
                    restored += 1
            return restored == len(targets)

    def _send(self, text: str, *, target: str) -> bool:
        try:
            receipt = self._plugin.push_message(
                source="neko_wows",
                visibility=[],
                # Context only: she should know the setting, not immediately
                # comment on it.
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=0,
                coalesce_key="wows_context",
                metadata={"plugin": "neko_wows", "kind": "context"},
                target_lanlan=target or None,
            )
        except Exception as exc:
            self.host_calls += 1
            if self.logger is not None:
                try:
                    self.logger.warning(f"context injection failed: {exc}")
                except Exception:
                    pass
            return False
        self.host_calls += 1
        if _submission_accepted(receipt):
            return True
        if self.logger is not None:
            try:
                self.logger.warning(
                    f"context injection declined: {_submission_reason(receipt)}")
            except Exception:
                pass
        return False


def _submission_accepted(receipt: Any) -> bool:
    """The SDK accepts responsibility only with an explicit true receipt."""
    return isinstance(receipt, Mapping) and receipt.get("submitted") is True


def _submission_reason(receipt: Any) -> str:
    if isinstance(receipt, Mapping):
        reason = receipt.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()[:200]
    return "submission was not accepted"


__all__ = [
    "REASON_DELIVERED",
    "REASON_DRY_RUN",
    "REASON_EXPIRED",
    "REASON_FAILED",
    "REASON_PAUSED",
    "ContextInjector",
    "NekoDispatcher",
]
