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
"""Bounded in-memory audio reuse for explicitly cacheable mini-game speech.

Only opaque SHA-256 keys and synthesized audio bytes are retained. Raw text,
provider credentials and voice configuration never enter this store.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import count
from threading import RLock
import time
from typing import Callable
from weakref import WeakKeyDictionary


DEFAULT_MAX_ENTRIES = 96
DEFAULT_MAX_TOTAL_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_ENTRY_BYTES = 2 * 1024 * 1024
DEFAULT_ENTRY_TTL_SECONDS = 6 * 60 * 60
DEFAULT_MAX_CAPTURES = 16
DEFAULT_MAX_CAPTURE_TOTAL_BYTES = 8 * 1024 * 1024
DEFAULT_CAPTURE_TTL_SECONDS = 120


class GameSpeechCaptureOwner:
    """Opaque, weak-referenceable handle identifying one capture owner.

    Owners are identified by a monotonic token held in a weak map, so a
    plain ``object()`` cannot be used: it is not weak-referenceable. Use
    this when there is no natural owner object (e.g. an isolated preload
    batch); long-lived owners such as the session manager pass themselves.
    """


@dataclass(frozen=True)
class _AudioEntry:
    chunks: tuple[bytes, ...]
    size: int
    expires_at: float


@dataclass
class _AudioCapture:
    owner_id: int
    cache_key: str
    runtime_signature: str
    created_at: float
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0


class GameSpeechAudioCache:
    """Thread-safe bounded LRU plus bounded in-flight capture registry."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
        entry_ttl_seconds: float = DEFAULT_ENTRY_TTL_SECONDS,
        max_captures: int = DEFAULT_MAX_CAPTURES,
        max_capture_total_bytes: int = DEFAULT_MAX_CAPTURE_TOTAL_BYTES,
        capture_ttl_seconds: float = DEFAULT_CAPTURE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_total_bytes = max(1, int(max_total_bytes))
        self._max_entry_bytes = max(1, min(int(max_entry_bytes), self._max_total_bytes))
        self._entry_ttl_seconds = max(0.001, float(entry_ttl_seconds))
        self._max_captures = max(1, int(max_captures))
        self._max_capture_total_bytes = max(1, int(max_capture_total_bytes))
        self._capture_ttl_seconds = max(0.001, float(capture_ttl_seconds))
        self._clock = clock
        self._entries: OrderedDict[str, _AudioEntry] = OrderedDict()
        self._captures: OrderedDict[tuple[int, str], _AudioCapture] = OrderedDict()
        self._entry_bytes = 0
        self._capture_bytes = 0
        self._lock = RLock()
        # Owner identity must outlive the owner object without ever being
        # reused. ``id()`` cannot do that: CPython recycles the address of a
        # freed object, so a short-lived preload owner can be handed the same
        # id as a previous one and inherit its still-live captures. Hand out
        # monotonic tokens instead and let the weak map drop dead owners.
        self._owner_tokens: WeakKeyDictionary = WeakKeyDictionary()
        self._next_owner_token = count(1)

    def _owner_token(self, owner: object) -> int:
        """Return this owner's stable, never-recycled identity.

        The owner must be weak-referenceable; ``GameSpeechCaptureOwner`` is
        provided for callers with no natural owner object. A non-weak-
        referenceable owner raises here rather than silently getting a token
        nothing else can re-derive, which would make every capture vanish.
        """
        # Under the lock: the lookup and the assignment are a check-then-act
        # pair, so two threads first touching the same owner would otherwise
        # mint two tokens, and captures for one (owner, speech_id) would split
        # across both. ``RLock`` is reentrant, so callers that already hold it
        # can still route through here.
        with self._lock:
            token = self._owner_tokens.get(owner)
            if token is None:
                token = next(self._next_owner_token)
                self._owner_tokens[owner] = token
            return token

    def _capture_id(self, owner: object, speech_id: object) -> tuple[int, str]:
        return self._owner_token(owner), str(speech_id or "")

    def _remove_entry_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._entry_bytes -= entry.size

    def _remove_capture_locked(self, capture_id: tuple[int, str]) -> None:
        capture = self._captures.pop(capture_id, None)
        if capture is not None:
            self._capture_bytes -= capture.size

    def _prune_locked(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._remove_entry_locked(key)
        for capture_id, capture in list(self._captures.items()):
            if now - capture.created_at >= self._capture_ttl_seconds:
                self._remove_capture_locked(capture_id)

    def get(self, cache_key: str) -> tuple[bytes, ...] | None:
        key = str(cache_key or "")
        if not key:
            return None
        with self._lock:
            self._prune_locked(self._clock())
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.chunks

    def begin_capture(
        self,
        owner: object,
        speech_id: object,
        cache_key: str,
        runtime_signature: str,
    ) -> bool:
        capture_id = self._capture_id(owner, speech_id)
        key = str(cache_key or "")
        signature = str(runtime_signature or "")
        if not capture_id[1] or not key or not signature:
            return False
        with self._lock:
            self._prune_locked(self._clock())
            self._remove_capture_locked(capture_id)
            if len(self._captures) >= self._max_captures:
                return False
            self._captures[capture_id] = _AudioCapture(
                owner_id=capture_id[0],
                cache_key=key,
                runtime_signature=signature,
                created_at=self._clock(),
            )
            return True

    def append_capture(self, owner: object, speech_id: object, chunk: object) -> bool:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            self.fail_capture(owner, speech_id)
            return False
        payload = bytes(chunk)
        capture_id = self._capture_id(owner, speech_id)
        with self._lock:
            self._prune_locked(self._clock())
            capture = self._captures.get(capture_id)
            if capture is None:
                return False
            next_size = capture.size + len(payload)
            if (
                not payload
                or next_size > self._max_entry_bytes
                or self._capture_bytes + len(payload) > self._max_capture_total_bytes
            ):
                self._remove_capture_locked(capture_id)
                return False
            capture.chunks.append(payload)
            capture.size = next_size
            self._capture_bytes += len(payload)
            return True

    def append_unscoped_capture(self, owner: object, speech_id: object, chunk: object) -> bool:
        """Append legacy untagged audio only when its owner has one unambiguous capture."""
        owner_id = self._owner_token(owner)
        with self._lock:
            self._prune_locked(self._clock())
            owner_captures = [
                key for key, capture in self._captures.items()
                if capture.owner_id == owner_id
            ]
            if len(owner_captures) != 1:
                for ambiguous_id in owner_captures:
                    self._remove_capture_locked(ambiguous_id)
                return False
            return self.append_capture(owner, owner_captures[0][1], chunk)

    def fail_capture(self, owner: object, speech_id: object) -> bool:
        capture_id = self._capture_id(owner, speech_id)
        with self._lock:
            existed = capture_id in self._captures
            self._remove_capture_locked(capture_id)
            return existed

    def complete_capture(
        self,
        owner: object,
        speech_id: object,
        current_runtime_signature: str,
    ) -> bool:
        capture_id = self._capture_id(owner, speech_id)
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            capture = self._captures.get(capture_id)
            if capture is None:
                return False
            self._remove_capture_locked(capture_id)
            if (
                not capture.chunks
                or capture.runtime_signature != str(current_runtime_signature or "")
                or capture.size > self._max_entry_bytes
            ):
                return False
            chunks = tuple(capture.chunks)
            existing = self._entries.get(capture.cache_key)
            if existing is not None:
                self._remove_entry_locked(capture.cache_key)
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._entry_bytes + capture.size > self._max_total_bytes
            ):
                oldest_key = next(iter(self._entries))
                self._remove_entry_locked(oldest_key)
            if self._entry_bytes + capture.size > self._max_total_bytes:
                return False
            self._entries[capture.cache_key] = _AudioEntry(
                chunks=chunks,
                size=capture.size,
                expires_at=now + self._entry_ttl_seconds,
            )
            self._entry_bytes += capture.size
            return True

    def discard_owner(self, owner: object) -> int:
        owner_id = self._owner_token(owner)
        with self._lock:
            matches = [key for key, capture in self._captures.items() if capture.owner_id == owner_id]
            for capture_id in matches:
                self._remove_capture_locked(capture_id)
            return len(matches)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._captures.clear()
            self._entry_bytes = 0
            self._capture_bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._prune_locked(self._clock())
            return {
                "entries": len(self._entries),
                "entry_bytes": self._entry_bytes,
                "captures": len(self._captures),
                "capture_bytes": self._capture_bytes,
            }


GAME_SPEECH_AUDIO_CACHE = GameSpeechAudioCache()
