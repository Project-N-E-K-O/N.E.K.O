from __future__ import annotations

import copy

import numpy as np
import pytest

from main_logic.voice_identity.profile import SpeakerActivityReferenceContract, SpeakerProfile
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.reference import SpeakerReference


def _reference() -> SpeakerReference:
    return SpeakerReference(SpeakerModelIdentity("model", "revision", 2), [3.0, 4.0])


def contract() -> SpeakerActivityReferenceContract:
    return SpeakerActivityReferenceContract("bundle-v1", "frontend-v1", "holdout-v1", 16000, True)


@pytest.mark.unit
@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy])
def test_dual_profile_copy_preserves_contract_and_independent_reference_lifetimes(copier) -> None:
    primary, activity = _reference(), _reference()
    profile = SpeakerProfile(
        "one", primary, activity_reference=activity, activity_reference_contract=contract(),
    )
    primary.close()
    activity.close()
    copied = copier(profile)
    observed = copied.clone_activity_reference()
    assert observed is not None
    try:
        assert copied.activity_reference_contract == contract()
        profile.close()
        assert profile.closed
        assert copied.has_activity_reference
        copied.close()
        np.testing.assert_allclose(observed.copy_embedding(), [0.6, 0.8])
        with pytest.raises(RuntimeError, match="closed"):
            copied.clone_activity_reference()
        with pytest.raises(RuntimeError, match="closed"):
            _ = copied.activity_reference_contract
    finally:
        observed.close()
        profile.close()
        copied.close()


@pytest.mark.unit
def test_reference_and_contract_must_be_provided_together() -> None:
    primary, activity = _reference(), _reference()
    try:
        with pytest.raises(ValueError, match="together"):
            SpeakerProfile("one", primary, activity_reference=activity)
        with pytest.raises(ValueError, match="together"):
            SpeakerProfile("one", primary, activity_reference_contract=contract())
        with pytest.raises(TypeError):
            SpeakerProfile("one", primary, activity_reference=activity, activity_reference_contract={})
    finally:
        primary.close()
        activity.close()


@pytest.mark.unit
@pytest.mark.parametrize("field,value", [
    ("resource_revision", ""), ("preprocessing_revision", 1),
    ("reference_method", " "), ("sample_rate_hz", 0),
    ("sample_rate_hz", True), ("noise_reduction_enabled", 1),
])
def test_activity_contract_rejects_invalid_metadata(field, value) -> None:
    values = dict(
        resource_revision="bundle", preprocessing_revision="frontend",
        reference_method="holdout", sample_rate_hz=16000, noise_reduction_enabled=True,
    )
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        SpeakerActivityReferenceContract(**values)


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["construct", "clone"])
def test_partial_dual_reference_construction_releases_primary_clone(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    primary, activity = _reference(), _reference()
    source = SpeakerProfile(
        "one", primary, activity_reference=activity, activity_reference_contract=contract(),
    )
    clone_calls = []
    original = SpeakerReference.clone

    def fail_second(instance):
        if clone_calls:
            raise RuntimeError("activity clone failed")
        cloned = original(instance)
        clone_calls.append(cloned)
        return cloned

    monkeypatch.setattr(SpeakerReference, "clone", fail_second)
    try:
        with pytest.raises(RuntimeError, match="activity clone"):
            if operation == "construct":
                SpeakerProfile(
                    "two", primary, activity_reference=activity,
                    activity_reference_contract=contract(),
                )
            else:
                copy.copy(source)
        assert len(clone_calls) == 1
        assert clone_calls[0].closed
        assert not primary.closed and not activity.closed
    finally:
        primary.close()
        activity.close()
        source.close()


@pytest.mark.unit
def test_activity_close_failure_does_not_skip_primary_cleanup_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, activity = _reference(), _reference()
    profile = SpeakerProfile(
        "one", primary, activity_reference=activity, activity_reference_contract=contract(),
    )
    owned_primary = profile._reference
    owned_activity = profile._activity_reference
    original = SpeakerReference.close
    failed = False

    def fail_once(instance):
        nonlocal failed
        if instance is owned_activity and not failed:
            failed = True
            raise RuntimeError("close failed")
        original(instance)

    monkeypatch.setattr(SpeakerReference, "close", fail_once)
    try:
        with pytest.raises(RuntimeError, match="close failed"):
            profile.close()
        assert owned_primary.closed
        assert not owned_activity.closed
        with pytest.raises(RuntimeError, match="closed"):
            profile.clone_activity_reference()
        profile.close()
        assert profile.closed
        assert owned_activity.closed
    finally:
        profile.close()
        primary.close()
        activity.close()
