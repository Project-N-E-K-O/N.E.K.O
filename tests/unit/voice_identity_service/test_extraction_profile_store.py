"""Encrypted TSE references retain raw scale and preserve older Owner profiles."""

from __future__ import annotations

import base64
import copy
from dataclasses import replace
import json

import numpy as np
import pytest

from main_logic.voice_identity.extraction_reference import (
    SpeakerExtractionReference, SpeakerExtractionReferenceContract,
)
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.tse.contracts import (
    TSE_ENCODER_IDENTITY, RESOURCE_REVISION, PREPROCESSING_REVISION, REFERENCE_METHOD,
)
import main_logic.voice_identity_service.profile_store as store_module
from main_logic.voice_identity_service.profile_store import (
    VoiceIdentityProfileStore, VoiceIdentityProfileCorruptError, VoiceIdentityProfileStoreError,
)
from .test_activity_profile_store import dual_profile, rewrite_authenticated_payload
from .test_profile_store import _TestKeyProtector, AUDIO_CONTRACT


RAW_EMBEDDING = np.arange(1, 193, dtype=np.float32) * 0.375


def extraction_contract():
    return SpeakerExtractionReferenceContract(
        RESOURCE_REVISION, PREPROCESSING_REVISION, REFERENCE_METHOD, 16000, True,
    )


def triple_profile(generation="triple-owner", *, contract=None, identity=TSE_ENCODER_IDENTITY):
    original = dual_profile(generation)
    primary, activity = original.clone_reference(), original.clone_activity_reference()
    extraction = SpeakerExtractionReference(identity, RAW_EMBEDDING)
    try:
        return SpeakerProfile(
            generation, primary, activity_reference=activity,
            activity_reference_contract=original.activity_reference_contract,
            extraction_reference=extraction,
            extraction_reference_contract=contract or extraction_contract(),
        )
    finally:
        original.close()
        primary.close()
        activity.close()
        extraction.close()


def test_triple_references_round_trip_with_raw_scale_and_no_plaintext(tmp_path):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    profile = triple_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
        ciphertext = store.path.read_bytes()
        assert json.loads(ciphertext)["schema_version"] == 5
        assert b"triple-owner" not in ciphertext
        assert TSE_ENCODER_IDENTITY.model_id.encode() not in ciphertext
        assert base64.b64encode(RAW_EMBEDDING.tobytes()) not in ciphertext
        loaded = store.load()
        assert loaded is not None
        try:
            assert loaded.profile.has_activity_reference and loaded.profile.has_extraction_reference
            assert loaded.profile.extraction_reference_contract == extraction_contract()
            reference = loaded.profile.clone_extraction_reference()
            try:
                np.testing.assert_array_equal(reference.copy_embedding(), RAW_EMBEDDING)
                assert np.linalg.norm(reference.copy_embedding()) > 100
                assert reference.model_identity == TSE_ENCODER_IDENTITY
            finally:
                reference.close()
        finally:
            loaded.close()
        assert store.delete()
        assert store.load() is None
    finally:
        profile.close()


def test_raw_reference_clone_does_not_normalize_alias_or_disclose_and_close_wipes():
    source = RAW_EMBEDDING.copy()
    reference = SpeakerExtractionReference(TSE_ENCODER_IDENTITY, source)
    source.fill(0)
    clone = copy.deepcopy(reference)
    owned = reference._embedding
    reference.close()
    assert not np.any(owned)
    np.testing.assert_array_equal(clone.copy_embedding(), RAW_EMBEDDING)
    assert "0.375" not in repr(clone)
    with pytest.raises(RuntimeError):
        reference.copy_embedding()
    with pytest.raises(TypeError):
        clone.__reduce__()
    clone.close()


@pytest.mark.parametrize("version", [3, 4])
def test_legacy_envelope_load_keeps_original_bytes_and_cam_reference(tmp_path, version):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    profile = triple_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()

    def legacy(payload):
        payload.pop("extraction_reference")
        if version == 3:
            payload.pop("activity_reference")

    rewrite_authenticated_payload(store.path, legacy, schema_version=version)
    before = store.path.read_bytes()
    loaded = store.load()
    assert loaded is not None
    try:
        assert loaded.profile.generation == "triple-owner"
        assert not loaded.profile.has_extraction_reference
        assert loaded.profile.has_activity_reference is (version == 4)
        assert store.path.read_bytes() == before
    finally:
        loaded.close()


@pytest.mark.parametrize("field,value", [
    ("resource_revision", "older-resource"),
    ("preprocessing_revision", "older-frontend"),
    ("reference_method", "older-aggregation"),
    ("sample_rate_hz", 48000),
])
def test_authenticated_tse_contract_mismatch_keeps_cam_and_version_for_ui(tmp_path, field, value):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    profile = triple_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(store.path, lambda payload: payload["extraction_reference"]["contract"].update({field: value}))
    before = store.path.read_bytes()
    loaded = store.load()
    assert loaded is not None
    try:
        assert loaded.profile.generation == "triple-owner"
        assert loaded.profile.has_activity_reference
        assert getattr(loaded.profile.extraction_reference_contract, field) == value
        assert store.path.read_bytes() == before
    finally:
        loaded.close()


@pytest.mark.parametrize("mutate", [
    lambda p: p["extraction_reference"].update(embedding_dimension=191),
    lambda p: p["extraction_reference"].update(embedding="not base64!"),
    lambda p: p["extraction_reference"].update(embedding=base64.b64encode(np.full(192, np.nan, dtype="<f4").tobytes()).decode()),
    lambda p: p["extraction_reference"].update(embedding=base64.b64encode(np.zeros(192, dtype="<f4").tobytes()).decode()),
    lambda p: p["extraction_reference"]["contract"].update(noise_reduction_enabled=False),
    lambda p: p["extraction_reference"]["contract"].update(sample_rate_hz=True),
    lambda p: p["extraction_reference"].pop("contract"),
    lambda p: p.pop("extraction_reference"),
])
def test_structurally_bad_extraction_material_never_loads(tmp_path, mutate):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    profile = triple_profile()
    try:
        store.save(profile, audio_contract=AUDIO_CONTRACT)
    finally:
        profile.close()
    rewrite_authenticated_payload(store.path, mutate)
    with pytest.raises(VoiceIdentityProfileCorruptError):
        store.load()


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_three_reference_transaction_failure_preserves_previous_ciphertext(tmp_path, monkeypatch, stage):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    old, newer = triple_profile("old"), triple_profile("new")
    try:
        store.save(old, audio_contract=AUDIO_CONTRACT)
        before = store.path.read_bytes()

        def fail(*_):
            raise OSError("disk full")

        if stage == "fsync":
            monkeypatch.setattr(store_module.os, "fsync", fail)
        else:
            monkeypatch.setattr(store_module, "_replace", fail)
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(newer, audio_contract=AUDIO_CONTRACT)
        assert store.path.read_bytes() == before
        assert not list(tmp_path.glob(".*.tmp"))
        restored = store.load()
        try:
            assert restored.profile.generation == "old"
            assert restored.profile.has_extraction_reference
        finally:
            restored.close()
    finally:
        old.close()
        newer.close()


def test_mismatched_processing_domain_cannot_replace_previous_profile(tmp_path):
    store = VoiceIdentityProfileStore(tmp_path / "owner", key_protector=_TestKeyProtector())
    old = triple_profile("old")
    newer = triple_profile("new", contract=replace(extraction_contract(), noise_reduction_enabled=False))
    try:
        store.save(old, audio_contract=AUDIO_CONTRACT)
        before = store.path.read_bytes()
        with pytest.raises(VoiceIdentityProfileStoreError):
            store.save(newer, audio_contract=AUDIO_CONTRACT)
        assert store.path.read_bytes() == before
    finally:
        old.close()
        newer.close()
