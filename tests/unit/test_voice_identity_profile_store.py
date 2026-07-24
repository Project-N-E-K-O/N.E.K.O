from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import keyring
import numpy as np
import pytest

from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.profile_store import (
    KeyringSecretProtector,
    ProfileCompatibility,
    ProfileLoadState,
    ProfileStore,
    SecureStorageUnavailable,
)


class _MemorySecretProtector:
    def __init__(self, key: bytes | None = None, *, available: bool = True) -> None:
        self.key = key
        self.available = available
        self.deleted = False

    def load_or_create_key(self) -> bytes:
        if not self.available:
            raise SecureStorageUnavailable("test secure storage unavailable")
        if self.key is None:
            self.key = bytes(range(32))
        return self.key

    def delete_key(self) -> None:
        if not self.available:
            raise SecureStorageUnavailable("test secure storage unavailable")
        self.deleted = True
        self.key = None


def _profile(revision: int = 7) -> SpeakerProfile:
    return SpeakerProfile(
        np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
        profile_revision=revision,
        model_id="test-speaker-model",
        model_revision="model-v1",
        embedding_dimension=4,
    )


def _compatibility() -> ProfileCompatibility:
    return ProfileCompatibility(
        model_id="test-speaker-model",
        model_revision="model-v1",
        embedding_dimension=4,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"account": ""}, "account"),
        ({"account": "owner", "service_name": ""}, "service_name"),
    ],
)
def test_keyring_secret_protector_validates_identity(
    kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        KeyringSecretProtector(**kwargs)


def test_keyring_secret_protector_creates_reuses_and_deletes_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        priority = 1

    entries: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_keyring", lambda: _Backend())
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, account: entries.get((service, account)),
    )
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, account, value: entries.__setitem__(
            (service, account),
            value,
        ),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, account: entries.pop((service, account)),
    )
    protector = KeyringSecretProtector(account="test-installation")

    created = protector.load_or_create_key()
    reused = protector.load_or_create_key()
    protector.delete_key()
    protector.delete_key()

    assert len(created) == 32
    assert reused == created
    assert entries == {}


@pytest.mark.parametrize(
    "encoded",
    [
        "not valid base64!",
        base64.b64encode(b"too-short").decode("ascii"),
    ],
)
def test_keyring_secret_protector_rejects_invalid_stored_key(
    monkeypatch: pytest.MonkeyPatch,
    encoded: str,
) -> None:
    class _Backend:
        priority = 1

    monkeypatch.setattr(keyring, "get_keyring", lambda: _Backend())
    monkeypatch.setattr(keyring, "get_password", lambda *_args: encoded)

    with pytest.raises(SecureStorageUnavailable, match="invalid"):
        KeyringSecretProtector(account="test-installation").load_or_create_key()


def test_keyring_secret_protector_rejects_unavailable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        priority = 0

    monkeypatch.setattr(keyring, "get_keyring", lambda: _Backend())

    with pytest.raises(SecureStorageUnavailable, match="unavailable"):
        KeyringSecretProtector(account="test-installation").load_or_create_key()


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_keyring_secret_protector_normalizes_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    class _Backend:
        priority = 1

    monkeypatch.setattr(keyring, "get_keyring", lambda: _Backend())
    if operation == "get":
        monkeypatch.setattr(
            keyring,
            "get_password",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("backend failed")),
        )
        action = KeyringSecretProtector(
            account="test-installation"
        ).load_or_create_key
    elif operation == "set":
        monkeypatch.setattr(keyring, "get_password", lambda *_args: None)
        monkeypatch.setattr(
            keyring,
            "set_password",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("backend failed")),
        )
        action = KeyringSecretProtector(
            account="test-installation"
        ).load_or_create_key
    else:
        monkeypatch.setattr(keyring, "get_password", lambda *_args: "present")
        monkeypatch.setattr(
            keyring,
            "delete_password",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("backend failed")),
        )
        action = KeyringSecretProtector(account="test-installation").delete_key

    with pytest.raises(SecureStorageUnavailable, match="unavailable"):
        action()


def test_profile_store_encrypts_and_round_trips_one_owner_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    store = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    profile = _profile()

    store.save(profile)

    encrypted = path.read_bytes()
    assert encrypted
    assert b"test-speaker-model" not in encrypted
    assert profile.reference_embedding.tobytes() not in encrypted

    loaded = store.load()
    assert loaded.state is ProfileLoadState.READY
    assert loaded.profile is not None
    assert loaded.profile.profile_revision == 7
    assert loaded.profile.model_id == "test-speaker-model"
    assert loaded.profile.model_revision == "model-v1"
    np.testing.assert_allclose(
        loaded.profile.reference_embedding,
        profile.reference_embedding,
        rtol=1e-6,
    )
    assert loaded.created_at is not None

    loaded.profile.close()
    profile.close()


def test_profile_store_treats_tampered_ciphertext_as_invalid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    store = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    profile = _profile()
    store.save(profile)
    profile.close()

    encrypted = bytearray(path.read_bytes())
    encrypted[-1] ^= 0xFF
    path.write_bytes(encrypted)

    loaded = store.load()
    assert loaded.state is ProfileLoadState.INVALID
    assert loaded.profile is None


def test_profile_store_treats_wrong_key_as_invalid(tmp_path: Path) -> None:
    path = tmp_path / "voice_identity.profile"
    store = ProfileStore(
        path,
        protector=_MemorySecretProtector(bytes(range(32))),
        compatibility=_compatibility(),
    )
    profile = _profile()
    store.save(profile)
    profile.close()

    wrong_key_store = ProfileStore(
        path,
        protector=_MemorySecretProtector(bytes(reversed(range(32)))),
        compatibility=_compatibility(),
    )

    loaded = wrong_key_store.load()
    assert loaded.state is ProfileLoadState.INVALID
    assert loaded.profile is None


def test_profile_store_rejects_incompatible_profile_before_writing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = ProfileStore(
        path,
        protector=_MemorySecretProtector(),
        compatibility=_compatibility(),
    )
    incompatible = SpeakerProfile(
        np.eye(3, dtype=np.float32)[0],
        profile_revision=1,
        model_id="other-model",
        model_revision="other-revision",
        embedding_dimension=3,
    )

    with pytest.raises(ValueError, match="incompatible"):
        store.save(incompatible)

    assert not path.exists()
    incompatible.close()


def test_profile_store_reports_secure_storage_unavailable_without_plaintext_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    unavailable = _MemorySecretProtector(available=False)
    store = ProfileStore(
        path,
        protector=unavailable,
        compatibility=_compatibility(),
    )

    with pytest.raises(SecureStorageUnavailable):
        store.save(_profile())

    assert not path.exists()

    path.write_bytes(b"not-a-profile")
    loaded = store.load()
    assert loaded.state is ProfileLoadState.SECURE_STORAGE_UNAVAILABLE
    assert loaded.profile is None


def test_profile_store_rejects_oversized_ciphertext_without_reading_it_all(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    path.write_bytes(b"x" * (64 * 1024 + 1))
    store = ProfileStore(
        path,
        protector=_MemorySecretProtector(),
        compatibility=_compatibility(),
    )

    loaded = store.load()

    assert loaded.state is ProfileLoadState.INVALID
    assert loaded.profile is None


@pytest.mark.parametrize(
    ("compatibility", "expected_state"),
    [
        (
            ProfileCompatibility(
                model_id="other-model",
                model_revision="model-v1",
                embedding_dimension=4,
            ),
            ProfileLoadState.INCOMPATIBLE,
        ),
        (
            ProfileCompatibility(
                model_id="test-speaker-model",
                model_revision="model-v2",
                embedding_dimension=4,
            ),
            ProfileLoadState.INCOMPATIBLE,
        ),
        (
            ProfileCompatibility(
                model_id="test-speaker-model",
                model_revision="model-v1",
                embedding_dimension=192,
            ),
            ProfileLoadState.INCOMPATIBLE,
        ),
    ],
)
def test_profile_store_rejects_incompatible_profiles(
    tmp_path: Path,
    compatibility: ProfileCompatibility,
    expected_state: ProfileLoadState,
) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    writer = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    profile = _profile()
    writer.save(profile)
    profile.close()

    reader = ProfileStore(
        path,
        protector=protector,
        compatibility=compatibility,
    )

    loaded = reader.load()
    assert loaded.state is expected_state
    assert loaded.profile is None


def test_failed_atomic_replace_preserves_previous_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    store = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    first = _profile(1)
    store.save(first)
    first.close()
    original_ciphertext = path.read_bytes()

    def fail_replace(_source, _target) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "main_logic.voice_identity.profile_store.os.replace",
        fail_replace,
    )
    second = _profile(2)
    with pytest.raises(OSError, match="replace failure"):
        store.save(second)
    second.close()

    assert path.read_bytes() == original_ciphertext
    assert not list(tmp_path.glob("*.tmp"))


def test_delete_removes_ciphertext_and_secret(tmp_path: Path) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    store = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    profile = _profile()
    store.save(profile)
    profile.close()

    store.delete()

    assert not path.exists()
    assert protector.deleted is True
    assert store.load().state is ProfileLoadState.EMPTY


def test_delete_treats_removed_ciphertext_as_authoritative_when_key_cleanup_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    protector = _MemorySecretProtector()
    store = ProfileStore(
        path,
        protector=protector,
        compatibility=_compatibility(),
    )
    profile = _profile()
    store.save(profile)
    profile.close()
    protector.available = False

    store.delete()

    assert not path.exists()
    assert protector.deleted is False
    assert store.load().state is ProfileLoadState.EMPTY


@pytest.mark.asyncio
async def test_async_profile_store_methods_do_not_block_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = ProfileStore(
        path,
        protector=_MemorySecretProtector(),
        compatibility=_compatibility(),
    )
    profile = _profile()
    to_thread = asyncio.to_thread
    calls: list[str] = []

    async def observed_to_thread(function, /, *args, **kwargs):
        calls.append(function.__name__)
        return await to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", observed_to_thread)

    await store.asave(profile)
    loaded = await store.aload()
    await store.adelete()

    assert calls == ["save", "load", "delete"]
    assert loaded.state is ProfileLoadState.READY
    assert loaded.profile is not None
    loaded.profile.close()
    profile.close()
