"""The host-config source of the plugin permission master switch.

``plugin_permission_switch.json`` in the runtime config directory holds the
setting the plugin manager writes::

    {"schema_version": 1, "checks_disabled": false, "updated_at": "<iso>"}

The value is read once and cached; a write updates the cache only after it
is on disk. Only this process writes the file: a change made to it by hand
takes effect on the next start. A missing file means the checks are on; so does a file that
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


class PermissionSwitchStore:
    def __init__(self, manager=None, *, clock=time.monotonic) -> None:
        self._manager_override = manager
        self._clock = clock
        # Guards the cached state only; no disk access is made while it is
        # held, so readers on the event loop never wait on the disk.
        self._lock = threading.Lock()
        # Serializes writes (and the disk access of a write).
        self._write_lock = threading.Lock()
        self._value: bool | None = None
        self._failed_at: float | None = None
        # Bumped by every write, so a read that started earlier cannot
        # overwrite the newer value.
        self._generation = 0
        self._reading = False

    def _manager(self):
        if self._manager_override is not None:
            return self._manager_override
        from utils.config_manager import get_config_manager

        return get_config_manager()

    def disabled(self) -> bool:
        """Whether the host config switches the checks off. Never raises."""
        with self._lock:
            if self._value is not None:
                return self._value
            if self._reading:
                return False  # another thread is reading it right now
            if self._failed_at is not None and self._clock() - self._failed_at < RETRY_SECONDS:
                return False
            self._reading, generation, failed_before = True, self._generation, self._failed_at
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
        with self._lock:
            self._reading = False
            if self._generation != generation:
                return self._value if self._value is not None else False
            if value is None:
                self._failed_at = self._clock()
            else:
                self._value, self._failed_at = value, None
        if problem is not None and failed_before is None:
            logger.error(
                "permission master switch setting could not be read, checks stay on: error={}",
                problem,
            )
        elif value is not None and failed_before is not None:
            logger.info("permission master switch setting can be read again")
        return bool(value)

    def set_disabled(self, disabled: bool) -> None:
        """Write the setting; raises ``ServerDomainError`` (503) when it
        cannot be saved, leaving the effective value unchanged."""
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
                self._value, self._failed_at = disabled, None


switch_store = PermissionSwitchStore()
