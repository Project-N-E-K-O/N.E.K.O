"""The host-config source of the plugin permission master switch.

``plugin_permission_switch.json`` in the runtime config directory holds the
setting the plugin manager writes::

    {"schema_version": 1, "checks_disabled": false, "updated_at": "<iso>"}

The host loads the value once at startup (``load``) and caches it; a write
updates the cache only after it is on disk. Permission checks only read the
cache: a load that is still missing or due for a retry after a failure runs
on a background thread, never on the caller's (often the event loop). When a
load changes the effective value, running plugins get their states again.
Only this process writes the file: a change made to it by hand takes effect
on the next start. A missing file means the checks are on; so does a file that
cannot be read (the switch only ever turns checks off).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from utils.file_utils import read_json_tolerating_replace

logger = get_logger("server.infrastructure.permission_switch")

FILENAME = "plugin_permission_switch.json"
SCHEMA_VERSION = 1
WRITE_FAILED = "PLUGIN_PERMISSION_SWITCH_WRITE_FAILED"
# After a failed read the file is tried again no sooner than this.
RETRY_SECONDS = 30.0


def _start_thread(target) -> None:
    threading.Thread(target=target, name="permission-switch-load", daemon=True).start()


def _push_states() -> None:
    from plugin.server.application.plugins.permission_push import permission_push_service

    permission_push_service.notify(None)


class PermissionSwitchStore:
    def __init__(self, manager=None, *, clock=time.monotonic, spawn=_start_thread, on_change=_push_states) -> None:
        self._manager_override = manager
        self._clock = clock
        self._spawn = spawn
        self._on_change = on_change
        # Guards the cached state only; no disk access is made while it is
        # held, so readers on the event loop never wait on the disk.
        self._lock = threading.Lock()
        # Serializes writes (and the disk access of a write).
        self._write_lock = threading.Lock()
        self._value: bool | None = None
        self._failed_at: float | None = None
        # What callers were told last (False until a value is known).
        self._effective = False
        # Bumped by every write, so a load that started earlier cannot
        # overwrite the newer value.
        self._generation = 0
        self._loading = False

    def _manager(self):
        if self._manager_override is not None:
            return self._manager_override
        from utils.config_manager import get_config_manager

        return get_config_manager()

    def disabled(self) -> bool:
        """Whether the host config switches the checks off. Never raises and
        never touches the disk: an unknown value counts as off (checks on)
        while it is loaded in the background."""
        with self._lock:
            if self._value is not None:
                return self._value
            due = not self._loading and (
                self._failed_at is None or self._clock() - self._failed_at >= RETRY_SECONDS
            )
            if due:
                self._loading = True
        if due:
            try:
                self._spawn(self._load_claimed)
            except Exception:
                with self._lock:
                    self._loading = False
        with self._lock:
            # Set already when the load ran inline (tests inject that).
            return bool(self._value)

    def load(self) -> bool:
        """Read the setting now (host startup); returns the effective value."""
        with self._lock:
            if self._loading:
                return self._value if self._value is not None else False
            self._loading = True
        return self._load_claimed()

    def _load_claimed(self) -> bool:
        with self._lock:
            generation, failed_before = self._generation, self._failed_at
        value: bool | None = None
        problem: str | None = None
        try:
            raw = read_json_tolerating_replace(self._manager().get_runtime_config_path(FILENAME))
        except FileNotFoundError:
            value = False
        except Exception as exc:
            problem = type(exc).__name__
        else:
            candidate = raw.get("checks_disabled") if isinstance(raw, dict) else None
            if isinstance(candidate, bool):
                value = candidate
            else:
                problem = "malformed"
        changed = False
        with self._lock:
            self._loading = False
            if self._generation != generation:
                return self._value if self._value is not None else False
            if value is None:
                # Unreadable means checks on, also over an earlier value.
                self._value, self._failed_at = None, self._clock()
                result = False
            else:
                self._value, self._failed_at = value, None
                result = value
            changed, self._effective = result != self._effective, result
        if problem is not None and failed_before is None:
            logger.error(
                "permission master switch setting could not be read, checks stay on: error={}",
                problem,
            )
        elif value is not None and failed_before is not None:
            logger.info("permission master switch setting can be read again")
        if changed:
            try:
                self._on_change()
            except Exception as exc:
                logger.warning(
                    "permission master switch change not pushed: error={}", type(exc).__name__
                )
        return result

    def set_disabled(self, disabled: bool) -> None:
        """Write the setting; raises ``ServerDomainError`` (503) when it
        cannot be saved, leaving the effective value unchanged. The caller
        pushes the new states."""
        if not isinstance(disabled, bool):
            raise ValueError("disabled must be a bool")
        with self._write_lock:
            try:
                self._manager().save_json_config(
                    FILENAME,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "checks_disabled": disabled,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as exc:
                logger.error(
                    "permission master switch setting could not be saved: error={}",
                    type(exc).__name__,
                )
                raise ServerDomainError(
                    WRITE_FAILED,
                    "The permission master switch could not be saved",
                    503,
                    details={"file": FILENAME, "retryable": True},
                ) from exc
            with self._lock:
                self._generation += 1
                self._value, self._failed_at, self._effective = disabled, None, disabled


switch_store = PermissionSwitchStore()
