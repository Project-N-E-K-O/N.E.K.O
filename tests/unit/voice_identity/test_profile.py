from __future__ import annotations

import copy
import gc

import numpy as np
import pytest

from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference


def _reference() -> SpeakerReference:
    return SpeakerReference(
        SpeakerModelIdentity(
            model_id="speaker-model",
            model_revision="revision-1",
            embedding_dimension=2,
        ),
        [3.0, 4.0],
    )


@pytest.mark.unit
@pytest.mark.parametrize("generation", ["", "   ", 1, None])
def test_profile_rejects_invalid_generation(generation: object) -> None:
    reference = _reference()
    try:
        with pytest.raises(ValueError):
            SpeakerProfile(generation, reference)  # type: ignore[arg-type]
    finally:
        reference.close()


@pytest.mark.unit
def test_profile_rejects_generation_string_subclasses() -> None:
    class Generation(str):
        pass

    reference = _reference()
    try:
        with pytest.raises(ValueError, match="generation"):
            SpeakerProfile(Generation("generation-a"), reference)
    finally:
        reference.close()


@pytest.mark.unit
def test_profile_requires_concrete_reference() -> None:
    class ReferenceSubclass(SpeakerReference):
        pass

    reference = ReferenceSubclass(
        SpeakerModelIdentity("speaker-model", "revision-1", 2),
        [3.0, 4.0],
    )
    try:
        with pytest.raises(TypeError, match="reference"):
            SpeakerProfile("generation-a", reference)
    finally:
        reference.close()


@pytest.mark.unit
def test_profile_clones_input_reference_and_exposes_only_clones() -> None:
    reference = _reference()
    profile = SpeakerProfile("generation-a", reference)
    reference.close()

    clone = profile.clone_reference()
    observed = clone.copy_embedding()
    try:
        assert profile.generation == "generation-a"
        assert profile.model_identity.model_id == "speaker-model"
        np.testing.assert_allclose(
            observed,
            np.array([0.6, 0.8], dtype=np.float32),
        )

        profile.close()
        after_close = clone.copy_embedding()
        try:
            np.testing.assert_allclose(
                after_close,
                np.array([0.6, 0.8], dtype=np.float32),
            )
        finally:
            after_close.fill(0.0)
    finally:
        observed.fill(0.0)
        clone.close()
        profile.close()


@pytest.mark.unit
@pytest.mark.parametrize("copy_profile", [copy.copy, copy.deepcopy])
def test_profile_copy_has_independent_ownership(copy_profile) -> None:
    reference = _reference()
    profile = SpeakerProfile("generation-a", reference)
    reference.close()
    copied = copy_profile(profile)
    profile.close()

    clone = copied.clone_reference()
    observed = clone.copy_embedding()
    try:
        np.testing.assert_allclose(
            observed,
            np.array([0.6, 0.8], dtype=np.float32),
        )
    finally:
        observed.fill(0.0)
        clone.close()
        copied.close()


@pytest.mark.unit
def test_profile_close_cascades_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = _reference()
    profile = SpeakerProfile("generation-a", reference)
    reference.close()
    close_calls: list[SpeakerReference] = []
    original_close = SpeakerReference.close

    def tracked_close(instance: SpeakerReference) -> None:
        close_calls.append(instance)
        original_close(instance)

    monkeypatch.setattr(SpeakerReference, "close", tracked_close)

    profile.close()
    profile.close()
    assert profile.closed
    del profile
    gc.collect()

    assert len(close_calls) == 1


@pytest.mark.unit
def test_closed_profile_blocks_use() -> None:
    reference = _reference()
    profile = SpeakerProfile("generation-a", reference)
    reference.close()
    profile.close()

    with pytest.raises(RuntimeError, match="closed"):
        _ = profile.generation
    with pytest.raises(RuntimeError, match="closed"):
        _ = profile.model_identity
    with pytest.raises(RuntimeError, match="closed"):
        profile.clone_reference()
    with pytest.raises(RuntimeError, match="closed"):
        copy.copy(profile)


@pytest.mark.unit
def test_profile_repr_never_contains_embedding() -> None:
    reference = SpeakerReference(
        SpeakerModelIdentity("speaker-model", "revision-1", 2),
        [0.1234567, 0.7654321],
    )
    profile = SpeakerProfile("generation-a", reference)
    try:
        assert "0.1234567" not in repr(profile)
        assert "0.7654321" not in repr(profile)
    finally:
        profile.close()
        reference.close()
