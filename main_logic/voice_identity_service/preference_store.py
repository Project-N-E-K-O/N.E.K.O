"""Atomic persistence for the non-sensitive voice-identity enable preference."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading


class VoiceIdentityPreferenceStoreError(RuntimeError):
    """Raised when the voice-identity preference cannot be trusted or saved."""


class VoiceIdentityPreferenceStore:
    """Store the requested enable bit separately from encrypted biometrics."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be pathlib.Path")
        self._path = path
        self._lock = threading.RLock()

    def load(self) -> bool:
        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise VoiceIdentityPreferenceStoreError(
                    "voice identity preference could not be read"
                ) from exc
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise VoiceIdentityPreferenceStoreError(
                    "voice identity preference is corrupt"
                ) from exc
            if (
                type(value) is not dict
                or set(value) != {"requested_enabled", "schema_version"}
                or value["schema_version"] != 1
                or type(value["requested_enabled"]) is not bool
            ):
                raise VoiceIdentityPreferenceStoreError(
                    "voice identity preference is corrupt"
                )
            return value["requested_enabled"]

    async def aload(self) -> bool:
        return await asyncio.to_thread(self.load)

    def save(self, requested_enabled: bool) -> None:
        if type(requested_enabled) is not bool:
            raise TypeError("requested_enabled must be bool")
        encoded = json.dumps(
            {
                "requested_enabled": requested_enabled,
                "schema_version": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        with self._lock:
            self._atomic_write(encoded)

    async def asave(self, requested_enabled: bool) -> None:
        await asyncio.to_thread(self.save, requested_enabled)

    def _atomic_write(self, encoded: bytes) -> None:
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
        except OSError as exc:
            raise VoiceIdentityPreferenceStoreError(
                "voice identity preference could not be saved"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
