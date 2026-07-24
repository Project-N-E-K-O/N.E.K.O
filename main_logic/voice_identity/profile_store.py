"""Encrypted single-owner speaker profile persistence.

The store deliberately knows nothing about ASR providers or session routing. It
only serializes one normalized :class:`SpeakerProfile` and delegates key custody
to the operating system through ``keyring``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .profile import SpeakerProfile


_LOGGER = logging.getLogger(__name__)
_FORMAT_VERSION = 1
_MAGIC = b"NEKO-VOICE-ID\x01"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_KEYRING_SERVICE = "N.E.K.O.voice-identity"
_MAX_PROFILE_BYTES = 64 * 1024


class SecureStorageUnavailable(RuntimeError):
    """Raised when no operating-system secret store is available."""


class ProfileLoadState(str, Enum):
    """Non-exceptional outcomes when restoring the local owner profile."""

    EMPTY = "empty"
    READY = "ready"
    SECURE_STORAGE_UNAVAILABLE = "secure_storage_unavailable"
    INVALID = "invalid"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class ProfileCompatibility:
    """Expected embedding identity for the active local speaker model."""

    model_id: str
    model_revision: str
    embedding_dimension: int

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id must not be empty")
        if not self.model_revision:
            raise ValueError("model_revision must not be empty")
        if self.embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")

    def accepts(
        self,
        *,
        model_id: str,
        model_revision: str,
        embedding_dimension: int,
    ) -> bool:
        return (
            model_id == self.model_id
            and model_revision == self.model_revision
            and embedding_dimension == self.embedding_dimension
        )


@dataclass(frozen=True, slots=True)
class ProfileLoadResult:
    """Loaded profile plus a UI-safe persistence state."""

    state: ProfileLoadState
    profile: SpeakerProfile | None = None
    created_at: str | None = None


class SecretProtector(Protocol):
    """Minimal operating-system secret custody used by :class:`ProfileStore`."""

    def load_or_create_key(self) -> bytes: ...

    def delete_key(self) -> None: ...


class KeyringSecretProtector:
    """Store the AES key in the current OS credential backend."""

    def __init__(
        self,
        *,
        account: str,
        service_name: str = _KEYRING_SERVICE,
    ) -> None:
        if not account:
            raise ValueError("keyring account must not be empty")
        if not service_name:
            raise ValueError("keyring service_name must not be empty")
        self._account = account
        self._service_name = service_name

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise SecureStorageUnavailable(
                "operating-system secure storage is unavailable"
            ) from exc
        try:
            backend = keyring.get_keyring()
            priority = float(getattr(backend, "priority", 0))
        except Exception as exc:
            raise SecureStorageUnavailable(
                "operating-system secure storage is unavailable"
            ) from exc
        if priority <= 0:
            raise SecureStorageUnavailable(
                "operating-system secure storage is unavailable"
            )
        return keyring

    def load_or_create_key(self) -> bytes:
        keyring = self._keyring()
        try:
            encoded = keyring.get_password(self._service_name, self._account)
        except Exception as exc:
            raise SecureStorageUnavailable(
                "operating-system secure storage is unavailable"
            ) from exc
        if encoded is None:
            key = AESGCM.generate_key(bit_length=256)
            encoded = base64.b64encode(key).decode("ascii")
            try:
                keyring.set_password(
                    self._service_name,
                    self._account,
                    encoded,
                )
            except Exception as exc:
                raise SecureStorageUnavailable(
                    "operating-system secure storage is unavailable"
                ) from exc
            return key
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SecureStorageUnavailable(
                "stored voice identity key is invalid"
            ) from exc
        if len(key) != _KEY_BYTES:
            raise SecureStorageUnavailable("stored voice identity key is invalid")
        return key

    def delete_key(self) -> None:
        keyring = self._keyring()
        try:
            encoded = keyring.get_password(self._service_name, self._account)
            if encoded is None:
                return
            keyring.delete_password(self._service_name, self._account)
        except Exception as exc:
            raise SecureStorageUnavailable(
                "operating-system secure storage is unavailable"
            ) from exc


class ProfileStore:
    """Atomically persist exactly one encrypted owner speaker profile."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        compatibility: ProfileCompatibility,
        protector: SecretProtector | None = None,
    ) -> None:
        self._path = Path(path)
        account = hashlib.sha256(
            str(self._path.parent.resolve()).encode("utf-8")
        ).hexdigest()
        self._protector = protector or KeyringSecretProtector(account=account)
        self._compatibility = compatibility

    @property
    def path(self) -> Path:
        return self._path

    def save(self, profile: SpeakerProfile) -> str:
        """Encrypt and atomically replace the current profile."""

        if not self._compatibility.accepts(
            model_id=profile.model_id,
            model_revision=profile.model_revision,
            embedding_dimension=profile.embedding_dimension,
        ):
            raise ValueError("speaker profile is incompatible with this store")
        embedding = profile.reference_embedding.astype("<f4", copy=False)
        created_at = datetime.now(UTC).isoformat()
        payload = {
            "created_at": created_at,
            "embedding": base64.b64encode(embedding.tobytes()).decode("ascii"),
            "embedding_dimension": profile.embedding_dimension,
            "format_version": _FORMAT_VERSION,
            "model_id": profile.model_id,
            "model_revision": profile.model_revision,
            "profile_revision": profile.profile_revision,
        }
        plaintext = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        key = self._load_key()
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = AESGCM(key).encrypt(nonce, plaintext, _MAGIC)
        self._atomic_write(_MAGIC + nonce + encrypted)
        return created_at

    async def asave(self, profile: SpeakerProfile) -> str:
        return await asyncio.to_thread(self.save, profile)

    def load(self) -> ProfileLoadResult:
        """Restore the profile without surfacing corrupt or stale contents."""

        if not self._path.is_file():
            return ProfileLoadResult(ProfileLoadState.EMPTY)
        try:
            key = self._load_key()
        except SecureStorageUnavailable:
            return ProfileLoadResult(ProfileLoadState.SECURE_STORAGE_UNAVAILABLE)
        try:
            if self._path.stat().st_size > _MAX_PROFILE_BYTES:
                return ProfileLoadResult(ProfileLoadState.INVALID)
            data = self._path.read_bytes()
            if len(data) <= len(_MAGIC) + _NONCE_BYTES or not data.startswith(_MAGIC):
                return ProfileLoadResult(ProfileLoadState.INVALID)
            nonce_start = len(_MAGIC)
            nonce_end = nonce_start + _NONCE_BYTES
            plaintext = AESGCM(key).decrypt(
                data[nonce_start:nonce_end],
                data[nonce_end:],
                _MAGIC,
            )
            payload = json.loads(plaintext.decode("utf-8"))
            return self._profile_from_payload(payload)
        except (
            InvalidTag,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            binascii.Error,
        ):
            return ProfileLoadResult(ProfileLoadState.INVALID)

    async def aload(self) -> ProfileLoadResult:
        return await asyncio.to_thread(self.load)

    def delete(self) -> None:
        """Remove biometric ciphertext first, then its OS-protected key."""

        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        try:
            self._protector.delete_key()
        except Exception:
            # Once ciphertext is gone, the remaining key is not a recoverable
            # biometric profile. Treat key cleanup as best-effort so runtime
            # state cannot disagree with the authoritative local file.
            _LOGGER.warning(
                "voice identity ciphertext removed but key cleanup failed",
                exc_info=True,
            )

    async def adelete(self) -> None:
        await asyncio.to_thread(self.delete)

    def _load_key(self) -> bytes:
        key = self._protector.load_or_create_key()
        if not isinstance(key, bytes) or len(key) != _KEY_BYTES:
            raise SecureStorageUnavailable(
                "operating-system secure storage returned an invalid key"
            )
        return key

    def _profile_from_payload(self, payload: object) -> ProfileLoadResult:
        if not isinstance(payload, dict):
            return ProfileLoadResult(ProfileLoadState.INVALID)
        if payload.get("format_version") != _FORMAT_VERSION:
            return ProfileLoadResult(ProfileLoadState.INVALID)
        model_id = payload["model_id"]
        model_revision = payload["model_revision"]
        embedding_dimension = payload["embedding_dimension"]
        profile_revision = payload["profile_revision"]
        created_at = payload["created_at"]
        if (
            not isinstance(model_id, str)
            or not isinstance(model_revision, str)
            or not isinstance(embedding_dimension, int)
            or not isinstance(profile_revision, int)
            or not isinstance(created_at, str)
        ):
            return ProfileLoadResult(ProfileLoadState.INVALID)
        if not self._compatibility.accepts(
            model_id=model_id,
            model_revision=model_revision,
            embedding_dimension=embedding_dimension,
        ):
            return ProfileLoadResult(ProfileLoadState.INCOMPATIBLE)
        encoded_embedding = payload["embedding"]
        if not isinstance(encoded_embedding, str):
            return ProfileLoadResult(ProfileLoadState.INVALID)
        embedding_bytes = base64.b64decode(encoded_embedding, validate=True)
        if len(embedding_bytes) != embedding_dimension * 4:
            return ProfileLoadResult(ProfileLoadState.INVALID)
        embedding = np.frombuffer(embedding_bytes, dtype="<f4").astype(
            np.float32,
            copy=True,
        )
        profile = SpeakerProfile(
            embedding,
            profile_revision=profile_revision,
            model_id=model_id,
            model_revision=model_revision,
            embedding_dimension=embedding_dimension,
        )
        embedding.fill(0)
        return ProfileLoadResult(
            ProfileLoadState.READY,
            profile=profile,
            created_at=created_at,
        )

    def _atomic_write(self, content: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            os.replace(temporary_path, self._path)
        except Exception:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            raise
