from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import numpy as np
import pytest

from main_logic.voice_identity.profile import SpeakerActivityReferenceContract, SpeakerProfile
from main_logic.voice_identity.pvad.assets import (
    ECAPA_IDENTITY,
    ECAPA_PREPROCESSING_REVISION,
    ECAPA_REFERENCE_METHOD,
    ECAPA_RESOURCE_REVISION,
)
from main_logic.voice_identity.reference import SpeakerReference
import main_logic.voice_identity_service.profile_store as store_module
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileIncompatibleError,
    VoiceIdentityProfileStore,
    VoiceIdentityProfileStoreError,
)
from .test_profile_store import AUDIO_CONTRACT, _TestKeyProtector, _profile


def activity_contract() -> SpeakerActivityReferenceContract:
    return SpeakerActivityReferenceContract(
        ECAPA_RESOURCE_REVISION, ECAPA_PREPROCESSING_REVISION,
        ECAPA_REFERENCE_METHOD, 16_000, True,
    )


def dual_profile(generation: str = "dual-generation") -> SpeakerProfile:
    source = _profile(generation)
    reference = source.clone_reference()
    activity = SpeakerReference(ECAPA_IDENTITY, np.arange(1, 193, dtype=np.float32))
    try:
        return SpeakerProfile(
            generation, reference, activity_reference=activity,
            activity_reference_contract=activity_contract(),
        )
    finally:
        source.close()
        reference.close()
        activity.close()


def rewrite_authenticated_payload(path: Path, mutate, *, schema_version: int = 5) -> None:
    envelope = json.loads(path.read_bytes())
    key = _TestKeyProtector().unprotect(base64.b64decode(envelope["wrapped_key"]))
    nonce = base64.b64decode(envelope["nonce"])
    payload = json.loads(AESGCM(key).decrypt(
        nonce, base64.b64decode(envelope["ciphertext"]),
        store_module._READ_AAD[envelope["schema_version"]],
    ))
    mutate(payload)
    if schema_version < 5:
        payload.pop("extraction_reference", None)
    envelope["schema_version"] = schema_version
    envelope["ciphertext"] = base64.b64encode(AESGCM(key).encrypt(
        nonce, json.dumps(payload).encode(), store_module._READ_AAD[schema_version],
    )).decode()
    path.write_text(json.dumps(envelope), encoding="ascii")


@pytest.mark.unit
def test_dual_references_round_trip_and_delete_together(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = dual_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
        ciphertext = store.path.read_bytes()
        assert b"dual-generation" not in ciphertext
        assert ECAPA_IDENTITY.model_id.encode() not in ciphertext
        assert ECAPA_REFERENCE_METHOD.encode() not in ciphertext
        loaded = store.load()
        assert loaded is not None
        try:
            assert loaded.profile.activity_reference_contract == activity_contract()
            first = profile.clone_activity_reference()
            second = loaded.profile.clone_activity_reference()
            assert first is not None and second is not None
            try:
                np.testing.assert_array_equal(first.copy_embedding(), second.copy_embedding())
            finally:
                first.close()
                second.close()
        finally:
            loaded.close()
        assert store.delete()
        assert store.load() is None
    finally:
        profile.close()


@pytest.mark.unit
def test_v3_load_migrates_in_memory_without_rewriting_original(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = _profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(
        store.path, lambda payload: payload.pop("activity_reference"), schema_version=3,
    )
    original = store.path.read_bytes()
    loaded = store.load()
    assert loaded is not None
    try:
        assert loaded.profile.generation == "generation-a"
        assert not loaded.profile.has_activity_reference
        assert loaded.profile.activity_reference_contract is None
        assert loaded.audio_contract == AUDIO_CONTRACT
        assert store.path.read_bytes() == original
        store.save(loaded.profile, audio_contract=loaded.audio_contract)
        assert json.loads(store.path.read_bytes())["schema_version"] == 5
    finally:
        loaded.close()


@pytest.mark.unit
def test_v3_cannot_claim_new_activity_reference_fields(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = dual_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(store.path, lambda payload: None, schema_version=3)
    with pytest.raises(VoiceIdentityProfileCorruptError):
        store.load()


@pytest.mark.unit
@pytest.mark.parametrize("field,value", [
    ("resource_revision", "other-bundle"),
    ("preprocessing_revision", "other-front-end"),
    ("reference_method", "three-references"),
    ("sample_rate_hz", 48_000),
    ("noise_reduction_enabled", False),
])
def test_authenticated_wrong_activity_contract_is_incompatible(
    tmp_path: Path, field: str, value: object,
) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = dual_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(
        store.path, lambda payload: payload["activity_reference"]["contract"].update({field: value}),
    )
    with pytest.raises(VoiceIdentityProfileIncompatibleError):
        store.load()


@pytest.mark.unit
@pytest.mark.parametrize("mutate", [
    lambda payload: payload["activity_reference"].update(model_revision="wrong-model"),
    lambda payload: payload["activity_reference"].update(embedding_dimension=191),
    lambda payload: payload["activity_reference"].update(embedding="not base64!"),
    lambda payload: payload["activity_reference"].update(
        embedding=base64.b64encode(np.full(192, np.nan, dtype="<f4").tobytes()).decode(),
    ),
    lambda payload: payload["activity_reference"]["contract"].update(sample_rate_hz=True),
    lambda payload: payload["activity_reference"].pop("contract"),
    lambda payload: payload.pop("activity_reference"),
])
def test_malformed_activity_reference_never_loads(tmp_path: Path, mutate) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = dual_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(store.path, mutate)
    with pytest.raises(VoiceIdentityProfileCorruptError):
        store.load()


@pytest.mark.unit
def test_activity_contract_is_checked_before_staging(tmp_path: Path) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    original = dual_profile()
    cam = original.clone_reference()
    activity = original.clone_activity_reference()
    wrong = SpeakerProfile(
        "wrong", cam, activity_reference=activity,
        activity_reference_contract=replace(activity_contract(), noise_reduction_enabled=False),
    )
    try:
        store.save(original, audio_contract=AUDIO_CONTRACT)
        before = store.path.read_bytes()
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(wrong, audio_contract=AUDIO_CONTRACT)
        assert store.path.read_bytes() == before
        assert not list(tmp_path.glob(".*.tmp"))
    finally:
        original.close()
        cam.close()
        assert activity is not None
        activity.close()
        wrong.close()


@pytest.mark.unit
@pytest.mark.parametrize("failure_point", ["encrypt", "stage", "replace"])
def test_dual_profile_failure_keeps_old_ciphertext_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str,
) -> None:
    protector = _TestKeyProtector()
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=protector)
    old = dual_profile("old")
    new = dual_profile("new")
    try:
        store.save(old, audio_contract=AUDIO_CONTRACT)
        original = store.path.read_bytes()

        def fail(*args, **kwargs):
            if failure_point == "encrypt":
                raise SecureStorageUnavailableError("secure_storage_unavailable")
            raise OSError("disk failure")

        if failure_point == "encrypt":
            monkeypatch.setattr(protector, "protect", fail)
        elif failure_point == "stage":
            monkeypatch.setattr(store_module.os, "fsync", fail)
        else:
            monkeypatch.setattr(store_module, "_replace", fail)
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(new, audio_contract=AUDIO_CONTRACT)
        assert store.path.read_bytes() == original
        assert not list(tmp_path.glob(".*.tmp"))
        assert new.has_activity_reference
        loaded = store.load()
        assert loaded is not None
        try:
            assert loaded.profile.generation == "old"
            assert loaded.profile.has_activity_reference
        finally:
            loaded.close()
    finally:
        old.close()
        new.close()


@pytest.mark.unit
def test_second_clone_failure_releases_first_owned_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = VoiceIdentityProfileStore(tmp_path / "profile", key_protector=_TestKeyProtector())
    profile = dual_profile()
    clones = []
    original_clone = SpeakerProfile.clone_reference

    def clone_primary(instance):
        clone = original_clone(instance)
        clones.append(clone)
        return clone

    def fail_activity(instance):
        raise RuntimeError("second clone failed")

    monkeypatch.setattr(SpeakerProfile, "clone_reference", clone_primary)
    monkeypatch.setattr(SpeakerProfile, "clone_activity_reference", fail_activity)
    try:
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(profile, audio_contract=AUDIO_CONTRACT)
        assert clones and all(reference.closed for reference in clones)
        assert not store.path.exists()
    finally:
        profile.close()
