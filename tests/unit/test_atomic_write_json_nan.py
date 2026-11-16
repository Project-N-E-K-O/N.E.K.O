"""M-001 P0-1b: the shared JSON writer refuses non-finite numbers by default.

``atomic_write_json`` used to inherit ``json.dumps``' default ``allow_nan=True``,
so a ``NaN`` produced anywhere in the codebase could land on disk as the bare
literal ``NaN``. ``json.load`` reads it back without complaint, which means the
corruption stayed invisible until something rendered the value with
``allow_nan=False`` -- see M-001.

The writer is the choke point: whoever produces the bad value, it fails here, at
the write that produced it, and the previous file content survives because
``json.dumps`` runs before ``atomic_write_text``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from utils.file_utils import atomic_write_json, atomic_write_json_async


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_is_rejected_and_the_file_is_untouched(tmp_path, bad):
    target = tmp_path / "state.json"
    target.write_text('{"kept": 1}', encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(ValueError):
        atomic_write_json(target, {"value": bad})

    assert target.read_bytes() == before


@pytest.mark.unit
def test_nested_non_finite_is_rejected(tmp_path):
    target = tmp_path / "state.json"

    with pytest.raises(ValueError):
        atomic_write_json(target, {"outer": {"inner": [1, float("nan")]}})

    assert not target.exists()


@pytest.mark.unit
def test_async_variant_rejects_too(tmp_path):
    target = tmp_path / "state.json"

    with pytest.raises(ValueError):
        asyncio.run(atomic_write_json_async(target, {"value": float("inf")}))

    assert not target.exists()


@pytest.mark.unit
def test_explicit_opt_in_still_writes(tmp_path):
    """The escape hatch stays available, and has to be asked for by name."""
    target = tmp_path / "state.json"

    atomic_write_json(target, {"value": float("nan")}, allow_nan=True)

    # The literal is on disk, exactly the shape that has to be opted into.
    assert "NaN" in target.read_text(encoding="utf-8")


@pytest.mark.unit
def test_finite_payload_round_trips(tmp_path):
    target = tmp_path / "state.json"
    payload = {"a": 1, "b": 2.5, "c": [1, 2], "d": {"e": None}, "f": True}

    atomic_write_json(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload


@pytest.mark.unit
def test_huge_integer_is_still_writable(tmp_path):
    """Magnitude is an input-validation concern (P0-2), not a serialisation one."""
    target = tmp_path / "state.json"

    atomic_write_json(target, {"value": 10 ** 500})

    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 10 ** 500}
