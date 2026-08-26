from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pytest

import main_logic.voice_identity_service.profile_store as store_module
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileStore,
    VoiceIdentityProfileStoreError,
    WindowsDpapiKeyProtector,
)


class _TestKeyProtector:
    _PREFIX = b"test-protected-key:"

    def protect(self, plaintext: bytes) -> bytes:
        return self._PREFIX + plaintext

    def unprotect(self, protected: bytes) -> bytes:
        if not protected.startswith(self._PREFIX):
            raise VoiceIdentityProfileCorruptError(
                "voice identity profile key could not be unwrapped"
            )
        return protected[len(self._PREFIX) :]


def _profile(
    generation: str = "generation-a",
    embedding: tuple[float, float] = (0.1234567, 0.7654321),
) -> SpeakerProfile:
    reference = SpeakerReference(
        SpeakerModelIdentity("campplus", "revision-1", 2),
        embedding,
    )
    try:
        return SpeakerProfile(generation, reference)
    finally:
        reference.close()


def _assert_profile(
    profile: SpeakerProfile,
    *,
    generation: str,
    expected: np.ndarray,
) -> None:
    reference = profile.clone_reference()
    embedding = reference.copy_embedding()
    try:
        assert profile.generation == generation
        assert profile.model_identity == SpeakerModelIdentity(
            "campplus",
            "revision-1",
            2,
        )
        np.testing.assert_allclose(embedding, expected, rtol=1e-6, atol=1e-6)
    finally:
        embedding.fill(0.0)
        reference.close()


@pytest.mark.unit
def test_store_round_trip_contains_no_plain_profile(tmp_path: Path) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(
        path,
        key_protector=_TestKeyProtector(),
    )
    profile = _profile()
    try:
        reference = profile.clone_reference()
        plain_embedding = reference.copy_embedding()
        try:
            plain_bytes = plain_embedding.astype("<f4").tobytes()
            plain_base64 = base64.b64encode(plain_bytes)
        finally:
            plain_embedding.fill(0.0)
            reference.close()

        store.save(profile)
        stored = path.read_bytes()
        assert b"generation-a" not in stored
        assert b"campplus" not in stored
        assert plain_bytes not in stored
        assert plain_base64 not in stored
        envelope = json.loads(stored)
        assert envelope["schema_version"] == 1
        assert envelope["algorithm"] == "AES-256-GCM"
        assert envelope["key_wrapping"] == "DPAPI-CURRENT-USER"

        loaded = store.load()
        assert loaded is not None
        try:
            expected = np.array([0.1234567, 0.7654321], dtype=np.float32)
            expected /= np.linalg.norm(expected.astype(np.float64))
            _assert_profile(
                loaded,
                generation="generation-a",
                expected=expected,
            )
        finally:
            loaded.close()
    finally:
        profile.close()


@pytest.mark.unit
def test_missing_and_delete_are_idempotent(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(
        tmp_path / "voice_identity.profile",
        key_protector=_TestKeyProtector(),
    )
    assert store.load() is None
    assert not store.delete()

    profile = _profile()
    try:
        store.save(profile)
    finally:
        profile.close()
    assert store.delete()
    assert not store.delete()
    assert store.load() is None


@pytest.mark.unit
def test_stage_does_not_replace_active_profile_until_commit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    first = _profile("generation-a")
    second = _profile("generation-b", (0.8, 0.2))
    try:
        store.save(first)
        original = path.read_bytes()

        staged = store.stage(second)
        assert staged.staged
        assert not staged.committed
        assert not staged.aborted
        assert path.read_bytes() == original
        temporary_paths = list(tmp_path.glob(".*.tmp"))
        assert len(temporary_paths) == 1
        assert temporary_paths[0].parent == path.parent
        assert b"generation-b" not in temporary_paths[0].read_bytes()

        staged.commit()
        staged.commit()
        assert not staged.staged
        assert staged.committed
        assert list(tmp_path.glob(".*.tmp")) == []
        loaded = store.load()
        assert loaded is not None
        try:
            assert loaded.generation == "generation-b"
        finally:
            loaded.close()
    finally:
        first.close()
        second.close()


@pytest.mark.unit
def test_abort_is_idempotent_and_never_replaces_active_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    first = _profile("generation-a")
    second = _profile("generation-b")
    try:
        store.save(first)
        original = path.read_bytes()
        staged = store.stage(second)

        staged.abort()
        staged.abort()

        assert staged.aborted
        assert not staged.staged
        assert path.read_bytes() == original
        assert list(tmp_path.glob(".*.tmp")) == []
        with pytest.raises(VoiceIdentityProfileStoreError, match="aborted"):
            staged.commit()
    finally:
        first.close()
        second.close()


@pytest.mark.unit
def test_tampered_ciphertext_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    profile = _profile()
    try:
        store.save(profile)
    finally:
        profile.close()
    envelope = json.loads(path.read_text(encoding="ascii"))
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    path.write_text(json.dumps(envelope), encoding="ascii")

    with pytest.raises(VoiceIdentityProfileCorruptError):
        store.load()


@pytest.mark.unit
def test_wrapped_key_unprotect_failure_is_reported_as_corrupt_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    profile = _profile()
    try:
        store.save(profile)
    finally:
        profile.close()
    envelope = json.loads(path.read_text(encoding="ascii"))
    envelope["wrapped_key"] = base64.b64encode(b"invalid-prefix").decode("ascii")
    path.write_text(json.dumps(envelope), encoding="ascii")

    with pytest.raises(VoiceIdentityProfileCorruptError):
        store.load()


@pytest.mark.unit
def test_failed_replace_preserves_previous_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    first = _profile("generation-a")
    second = _profile("generation-b", (0.8, 0.2))
    try:
        store.save(first)
        original = path.read_bytes()

        def fail_replace(_source: Path, _destination: Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(store_module, "_replace", fail_replace)
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(second)

        assert path.read_bytes() == original
        assert list(tmp_path.glob("*.tmp")) == []
        loaded = store.load()
        assert loaded is not None
        try:
            assert loaded.generation == "generation-a"
        finally:
            loaded.close()
    finally:
        first.close()
        second.close()


@pytest.mark.unit
def test_failed_staged_commit_can_be_retried_or_aborted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "voice_identity.profile"
    store = VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    profile = _profile()
    try:
        staged = store.stage(profile)
    finally:
        profile.close()
    original_replace = store_module._replace

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(store_module, "_replace", fail_replace)
    with pytest.raises(VoiceIdentityProfileStoreError, match="committed"):
        staged.commit()
    assert staged.staged
    assert len(list(tmp_path.glob(".*.tmp"))) == 1

    monkeypatch.setattr(store_module, "_replace", original_replace)
    staged.commit()
    assert staged.committed
    assert path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_methods_match_sync_contract(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(
        tmp_path / "voice_identity.profile",
        key_protector=_TestKeyProtector(),
    )
    profile = _profile()
    try:
        staged = await store.astage(profile)
        await staged.acommit()
    finally:
        profile.close()

    loaded = await store.aload()
    assert loaded is not None
    loaded.close()
    assert await store.adelete()
    assert await store.aload() is None

    aborted_profile = _profile("generation-b")
    try:
        aborted = await store.astage(aborted_profile)
        await aborted.aabort()
        await aborted.aabort()
    finally:
        aborted_profile.close()
    assert aborted.aborted
    assert await store.aload() is None


@pytest.mark.unit
def test_default_store_fails_when_windows_dpapi_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "_IS_WINDOWS", False)

    with pytest.raises(
        SecureStorageUnavailableError,
        match="secure_storage_unavailable",
    ):
        VoiceIdentityProfileStore(tmp_path / "voice_identity.profile")


@pytest.mark.unit
@pytest.mark.skipif(not store_module._IS_WINDOWS, reason="Windows DPAPI only")
def test_windows_dpapi_round_trip() -> None:
    protector = WindowsDpapiKeyProtector()
    plaintext = b"x" * 32

    protected = protector.protect(plaintext)

    assert protected != plaintext
    assert protector.unprotect(protected) == plaintext
