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

"""Bounded owner handshake before launcher storage bootstrap work.

The desktop owner cannot safely infer that storage migration has started merely
from an event sitting in the launcher's stdout pipe.  A normal quit can otherwise
begin while the launcher is already reading or mutating the storage authority.

New desktop owners opt in explicitly and give the launcher a private response
pipe at fd 5.  The launcher emits a per-operation request on its authenticated
``NEKO_EVENT`` stream and does not touch storage until the exact response arrives
on that private pipe.  Old owners and standalone launches do not opt in and keep
their existing behaviour.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable, MutableMapping


STORAGE_BOOTSTRAP_GUARD_CAPABILITY_ENV = "NEKO_STORAGE_BOOTSTRAP_GUARD_V1"
STORAGE_BOOTSTRAP_GUARD_FD_ENV = "NEKO_STORAGE_BOOTSTRAP_GUARD_FD"
STORAGE_BOOTSTRAP_GUARD_FD = 5
STORAGE_BOOTSTRAP_GUARD_PROTOCOL_VERSION = 1
STORAGE_BOOTSTRAP_GUARD_RESPONSE_TIMEOUT_SECONDS = 5.0
STORAGE_BOOTSTRAP_GUARD_MAX_LINE_BYTES = 4096

STORAGE_BOOTSTRAP_GUARD_PHASES = frozenset(
    {"initial_bootstrap", "restart_resolution"}
)
STORAGE_BOOTSTRAP_GUARD_OUTCOMES = frozenset(
    {"ready", "recovery_limited", "no_restart", "restart_handoff"}
)


class StorageBootstrapGuardError(RuntimeError):
    """The opted-in owner did not safely authorize storage bootstrap."""

    def __init__(self, reason: str):
        self.reason = str(reason or "unavailable")
        super().__init__(f"storage bootstrap guard failed: {self.reason}")


class StorageBootstrapGuardChannel:
    """Own one private, non-inheritable response channel for this generation."""

    def __init__(
        self,
        fd: int | None,
        *,
        enabled: bool,
        initialization_error: str = "",
        response_timeout: float = STORAGE_BOOTSTRAP_GUARD_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        self._fd = fd
        self.enabled = bool(enabled)
        self._initialization_error = str(initialization_error or "")
        self._response_timeout = max(0.001, float(response_timeout))
        self._buffer = bytearray()
        self._active_guard_id: str | None = None
        self._active_launch_id: str | None = None
        self._active_phase: str | None = None
        self._request_lock = threading.Lock()

        if self.enabled and self._fd is not None and hasattr(os, "register_at_fork"):
            # close-on-exec covers Windows/spawn/exec. A POSIX fork duplicates
            # descriptors without consulting FD_CLOEXEC, so close the private
            # owner channel explicitly in forked service children as well.
            os.register_at_fork(after_in_child=self._close_after_fork_in_child)

    @classmethod
    def from_environment(
        cls,
        environ: MutableMapping[str, str] | None = None,
        *,
        response_timeout: float = STORAGE_BOOTSTRAP_GUARD_RESPONSE_TIMEOUT_SECONDS,
    ) -> "StorageBootstrapGuardChannel":
        environment = os.environ if environ is None else environ
        capability = str(environment.pop(STORAGE_BOOTSTRAP_GUARD_CAPABILITY_ENV, "") or "")
        advertised_fd = str(environment.pop(STORAGE_BOOTSTRAP_GUARD_FD_ENV, "") or "")

        if capability != "1":
            return cls(None, enabled=False, response_timeout=response_timeout)

        if advertised_fd != str(STORAGE_BOOTSTRAP_GUARD_FD):
            return cls(
                None,
                enabled=True,
                initialization_error="invalid_fd_capability",
                response_timeout=response_timeout,
            )

        owned_fd: int | None = None
        try:
            # Duplicate before closing the public fd number. Keeping only a
            # private descriptor prevents unrelated launcher code from using 5
            # accidentally, while the owner still talks to the same pipe.
            owned_fd = os.dup(STORAGE_BOOTSTRAP_GUARD_FD)
            os.set_inheritable(owned_fd, False)
            os.close(STORAGE_BOOTSTRAP_GUARD_FD)
        except (OSError, ValueError) as exc:
            if owned_fd is not None:
                try:
                    os.close(owned_fd)
                except OSError:
                    pass
            return cls(
                None,
                enabled=True,
                initialization_error=f"channel_unavailable:{type(exc).__name__}",
                response_timeout=response_timeout,
            )

        return cls(owned_fd, enabled=True, response_timeout=response_timeout)

    @property
    def fd(self) -> int | None:
        """Expose the owned descriptor for narrow lifecycle tests only."""

        return self._fd

    def _close_after_fork_in_child(self) -> None:
        self.close()
        self.enabled = False

    def close(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass

    def request(
        self,
        *,
        phase: str,
        launch_id: str,
        emit_event: Callable[[str, dict], None],
    ) -> str | None:
        """Request authorization and wait for the exact private ACK."""

        if not self.enabled:
            return None
        if phase not in STORAGE_BOOTSTRAP_GUARD_PHASES:
            raise ValueError(f"unsupported storage bootstrap guard phase: {phase}")
        if self._initialization_error or self._fd is None:
            raise StorageBootstrapGuardError(
                self._initialization_error or "channel_unavailable"
            )
        if not _is_random_hex_id(launch_id):
            raise StorageBootstrapGuardError("invalid_launch_id")
        if not self._request_lock.acquire(blocking=False):
            raise StorageBootstrapGuardError("concurrent_request")

        try:
            if self._active_guard_id is not None:
                raise StorageBootstrapGuardError("previous_request_inconclusive")
            guard_id = uuid.uuid4().hex
            self._active_guard_id = guard_id
            self._active_launch_id = launch_id
            self._active_phase = phase
            emit_event(
                "storage_bootstrap_guard_request",
                {
                    "protocol_version": STORAGE_BOOTSTRAP_GUARD_PROTOCOL_VERSION,
                    "guard_id": guard_id,
                    "launch_id": launch_id,
                    "phase": phase,
                },
            )
            self._wait_for_response(guard_id=guard_id, launch_id=launch_id)
            return guard_id
        except Exception:
            # Do not clear the active fact after an inconclusive request. The
            # caller must fail this launcher generation; no later operation may
            # reuse a response channel whose authorization boundary is unknown.
            raise
        finally:
            self._request_lock.release()

    def release(
        self,
        guard_id: str | None,
        *,
        outcome: str,
        emit_event: Callable[[str, dict], None],
    ) -> None:
        """Publish completion only for the exact active authorized operation."""

        if guard_id is None:
            return
        if outcome not in STORAGE_BOOTSTRAP_GUARD_OUTCOMES:
            raise ValueError(f"unsupported storage bootstrap guard outcome: {outcome}")
        if (
            guard_id != self._active_guard_id
            or not self._active_launch_id
            or not self._active_phase
        ):
            raise StorageBootstrapGuardError("release_identity_mismatch")

        launch_id = self._active_launch_id
        phase = self._active_phase
        emit_event(
            "storage_bootstrap_guard_release",
            {
                "protocol_version": STORAGE_BOOTSTRAP_GUARD_PROTOCOL_VERSION,
                "guard_id": guard_id,
                "launch_id": launch_id,
                "phase": phase,
                "outcome": outcome,
            },
        )
        self._active_guard_id = None
        self._active_launch_id = None
        self._active_phase = None

    def _wait_for_response(self, *, guard_id: str, launch_id: str) -> None:
        deadline = time.monotonic() + self._response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StorageBootstrapGuardError("timeout")

            line = self._read_line(remaining)
            try:
                response = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise StorageBootstrapGuardError("invalid_response") from exc

            if not isinstance(response, dict) or set(response) != {
                "protocol_version",
                "decision",
                "guard_id",
                "launch_id",
            }:
                raise StorageBootstrapGuardError("invalid_response")
            if (
                type(response["protocol_version"]) is not int
                or response["protocol_version"] != STORAGE_BOOTSTRAP_GUARD_PROTOCOL_VERSION
                or response["decision"] not in {"ACK", "ABORT"}
                or not isinstance(response["guard_id"], str)
                or not isinstance(response["launch_id"], str)
            ):
                raise StorageBootstrapGuardError("invalid_response")

            # A delayed response for an earlier operation never authorizes the
            # current one. Ignore well-formed stale frames within the same
            # bounded wait so a following exact ACK can still make progress.
            if response["guard_id"] != guard_id or response["launch_id"] != launch_id:
                continue
            if response["decision"] == "ABORT":
                raise StorageBootstrapGuardError("aborted")
            return

    def _read_line(self, timeout: float) -> str:
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index >= 0:
                raw_line = bytes(self._buffer[:newline_index])
                del self._buffer[: newline_index + 1]
                if raw_line.endswith(b"\r"):
                    raw_line = raw_line[:-1]
                if not raw_line or len(raw_line) > STORAGE_BOOTSTRAP_GUARD_MAX_LINE_BYTES:
                    raise StorageBootstrapGuardError("invalid_response")
                try:
                    return raw_line.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise StorageBootstrapGuardError("invalid_response") from exc

            if len(self._buffer) > STORAGE_BOOTSTRAP_GUARD_MAX_LINE_BYTES:
                raise StorageBootstrapGuardError("invalid_response")

            fd = self._fd
            if fd is None:
                raise StorageBootstrapGuardError("channel_unavailable")
            result_queue: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

            def _read_once() -> None:
                try:
                    result_queue.put(os.read(fd, STORAGE_BOOTSTRAP_GUARD_MAX_LINE_BYTES + 1))
                except BaseException as exc:  # make all reader failures visible to the waiter
                    result_queue.put(exc)

            reader = threading.Thread(
                target=_read_once,
                name="neko-storage-bootstrap-guard-read",
                daemon=True,
            )
            reader.start()
            try:
                chunk = result_queue.get(timeout=max(0.001, timeout))
            except queue.Empty as exc:
                raise StorageBootstrapGuardError("timeout") from exc
            if isinstance(chunk, BaseException):
                raise StorageBootstrapGuardError("channel_unavailable") from chunk
            if not chunk:
                raise StorageBootstrapGuardError("eof")
            self._buffer.extend(chunk)


def _is_random_hex_id(value: object) -> bool:
    text = value if isinstance(value, str) else ""
    return len(text) == 32 and all(character in "0123456789abcdef" for character in text)
