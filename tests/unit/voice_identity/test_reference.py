from __future__ import annotations

import copy
import json
import pickle
import threading

import numpy as np
import pytest

import main_logic.voice_identity.reference as reference_module
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.reference import SpeakerReference


def _identity(dimension: int = 2) -> SpeakerModelIdentity:
    return SpeakerModelIdentity(
        model_id="speaker-model",
        model_revision="revision-1",
        embedding_dimension=dimension,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_id": "", "model_revision": "revision", "embedding_dimension": 2},
        {"model_id": "   ", "model_revision": "revision", "embedding_dimension": 2},
        {"model_id": "model", "model_revision": "", "embedding_dimension": 2},
        {"model_id": "model", "model_revision": "   ", "embedding_dimension": 2},
        {"model_id": "model", "model_revision": "revision", "embedding_dimension": 0},
        {"model_id": "model", "model_revision": "revision", "embedding_dimension": -1},
        {"model_id": "model", "model_revision": "revision", "embedding_dimension": True},
        {"model_id": "model", "model_revision": "revision", "embedding_dimension": 2.0},
    ],
)
def test_model_identity_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SpeakerModelIdentity(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_reference_requires_concrete_model_identity() -> None:
    class IdentitySubclass(SpeakerModelIdentity):
        pass

    identity = IdentitySubclass("speaker-model", "revision-1", 2)

    with pytest.raises(TypeError, match="model_identity"):
        SpeakerReference(identity, [3.0, 4.0])


@pytest.mark.unit
@pytest.mark.parametrize(
    "embedding",
    [
        np.array(1.0, dtype=np.float32),
        np.array([[1.0, 0.0]], dtype=np.float32),
        np.array([1.0], dtype=np.float32),
        np.array([0.0, 0.0], dtype=np.float32),
        np.array([np.nan, 1.0], dtype=np.float32),
        np.array([np.inf, 1.0], dtype=np.float32),
        np.array([1.0 + 1.0j, 2.0], dtype=np.complex64),
    ],
)
def test_reference_rejects_invalid_embeddings(embedding: np.ndarray) -> None:
    with pytest.raises(ValueError):
        SpeakerReference(_identity(), embedding)


@pytest.mark.unit
def test_reference_error_does_not_echo_embedding() -> None:
    with pytest.raises(ValueError) as exc_info:
        SpeakerReference(_identity(), ["sensitive-vector", "value"])

    assert "sensitive-vector" not in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.parametrize("failing_call", [1, 2])
def test_reference_preserves_memory_error_during_conversion(
    monkeypatch: pytest.MonkeyPatch,
    failing_call: int,
) -> None:
    original_array = reference_module.np.array
    call_count = 0

    def array_with_allocation_failure(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == failing_call:
            raise MemoryError("allocation failed")
        return original_array(*args, **kwargs)

    monkeypatch.setattr(reference_module.np, "array", array_with_allocation_failure)

    with pytest.raises(MemoryError, match="allocation failed"):
        SpeakerReference(_identity(), [3.0, 4.0])


@pytest.mark.unit
def test_reference_normalizes_and_defensively_copies_input() -> None:
    source = np.array([3.0, 4.0], dtype=np.float64)
    reference = SpeakerReference(_identity(), source)
    source[:] = 99.0

    observed = reference.copy_embedding()
    try:
        assert observed.dtype == np.dtype(np.float32)
        assert observed.flags.c_contiguous
        np.testing.assert_allclose(
            observed,
            np.array([0.6, 0.8], dtype=np.float32),
        )
    finally:
        observed.fill(0.0)
        reference.close()


@pytest.mark.unit
def test_reference_normalizes_float32_subnormals_in_float64() -> None:
    smallest = np.nextafter(np.float32(0.0), np.float32(1.0))
    reference = SpeakerReference(
        _identity(),
        np.array([smallest, smallest], dtype=np.float32),
    )

    observed = reference.copy_embedding()
    try:
        expected = np.float32(1.0 / np.sqrt(2.0))
        np.testing.assert_array_equal(
            observed,
            np.array([expected, expected], dtype=np.float32),
        )
        assert np.linalg.norm(observed.astype(np.float64)) == pytest.approx(1.0)
    finally:
        observed.fill(0.0)
        reference.close()


@pytest.mark.unit
def test_embedding_copies_have_independent_caller_ownership() -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])
    first = reference.copy_embedding()
    second = reference.copy_embedding()
    try:
        assert first.flags.owndata
        assert second.flags.owndata
        assert not np.shares_memory(first, second)

        first.fill(0.0)
        np.testing.assert_allclose(
            second,
            np.array([0.6, 0.8], dtype=np.float32),
        )

        third = reference.copy_embedding()
        try:
            np.testing.assert_allclose(
                third,
                np.array([0.6, 0.8], dtype=np.float32),
            )
        finally:
            third.fill(0.0)
    finally:
        first.fill(0.0)
        second.fill(0.0)
        reference.close()


@pytest.mark.unit
def test_exported_copy_survives_reference_close() -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])
    exported = reference.copy_embedding()

    reference.close()

    try:
        assert type(exported) is np.ndarray
        assert exported.flags.owndata
        np.testing.assert_allclose(
            exported,
            np.array([0.6, 0.8], dtype=np.float32),
        )
    finally:
        exported.fill(0.0)


@pytest.mark.unit
def test_clone_and_copy_protocols_have_independent_ownership() -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])
    clones = (reference.clone(), copy.copy(reference), copy.deepcopy(reference))
    reference.close()

    try:
        for clone in clones:
            observed = clone.copy_embedding()
            try:
                np.testing.assert_allclose(
                    observed,
                    np.array([0.6, 0.8], dtype=np.float32),
                )
            finally:
                observed.fill(0.0)
    finally:
        for clone in clones:
            clone.close()


@pytest.mark.unit
def test_model_identity_is_returned_as_an_independent_value() -> None:
    identity = _identity()
    reference = SpeakerReference(identity, [3.0, 4.0])
    try:
        observed = reference.model_identity

        assert observed == identity
        assert observed is not identity
    finally:
        reference.close()


@pytest.mark.unit
def test_close_is_idempotent_and_blocks_use() -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])

    reference.close()
    reference.close()

    assert reference.closed
    with pytest.raises(RuntimeError, match="closed"):
        _ = reference.model_identity
    with pytest.raises(RuntimeError, match="closed"):
        reference.clone()
    with pytest.raises(RuntimeError, match="closed"):
        reference.copy_embedding()


@pytest.mark.unit
def test_close_commits_state_before_best_effort_wipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])
    original_wipe = reference_module._wipe

    def interrupted_wipe(array: np.ndarray) -> None:
        original_wipe(array)
        raise KeyboardInterrupt

    monkeypatch.setattr(reference_module, "_wipe", interrupted_wipe)

    with pytest.raises(KeyboardInterrupt):
        reference.close()

    assert reference.closed
    with pytest.raises(RuntimeError, match="closed"):
        reference.copy_embedding()


@pytest.mark.unit
def test_copy_and_close_are_serialized() -> None:
    reference = SpeakerReference(_identity(), [3.0, 4.0])
    barrier = threading.Barrier(2)
    copies: list[np.ndarray] = []
    failures: list[BaseException] = []

    def copy_once() -> None:
        barrier.wait()
        try:
            copies.append(reference.copy_embedding())
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=copy_once)
    worker.start()
    barrier.wait()
    reference.close()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert len(copies) + len(failures) == 1
    if copies:
        try:
            np.testing.assert_allclose(
                copies[0],
                np.array([0.6, 0.8], dtype=np.float32),
            )
        finally:
            copies[0].fill(0.0)
    else:
        assert isinstance(failures[0], RuntimeError)


@pytest.mark.unit
def test_reference_has_no_internal_embedding_representation() -> None:
    reference = SpeakerReference(_identity(), [0.1234567, 0.7654321])
    try:
        assert not hasattr(reference, "reference_embedding")
        assert not hasattr(reference, "__dict__")
        assert "0.1234567" not in repr(reference)
        assert "0.7654321" not in repr(reference)
        with pytest.raises(TypeError):
            json.dumps(reference)
        with pytest.raises(TypeError, match="must not be pickled"):
            pickle.dumps(reference)
        with pytest.raises(TypeError, match="must not be pickled"):
            reference.__reduce__()
    finally:
        reference.close()
