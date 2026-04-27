from __future__ import annotations

import pytest

from plugin.server.infrastructure import runtime_overrides as ro


@pytest.mark.plugin_unit
def test_runtime_overrides_set_load_clear_roundtrip(_isolate_runtime_overrides):
    assert ro.load_runtime_overrides() == {}

    ro.set_runtime_override("alpha", False)
    ro.set_runtime_override("beta", True)
    assert ro.load_runtime_overrides() == {"alpha": False, "beta": True}
    assert ro.get_runtime_override("alpha") is False
    assert ro.get_runtime_override("beta") is True
    assert ro.get_runtime_override("missing") is None

    ro.clear_runtime_override("alpha")
    assert ro.load_runtime_overrides() == {"beta": True}
    assert ro.get_runtime_override("alpha") is None


@pytest.mark.plugin_unit
def test_runtime_overrides_set_no_op_when_unchanged(_isolate_runtime_overrides, monkeypatch):
    write_calls: list[dict[str, bool]] = []

    original_save = ro._save_to_disk

    def _spy_save(overrides):
        write_calls.append(dict(overrides))
        original_save(overrides)

    monkeypatch.setattr(ro, "_save_to_disk", _spy_save)
    ro.reset_cache_for_testing()

    ro.set_runtime_override("alpha", False)
    ro.set_runtime_override("alpha", False)  # 同值，不应再写
    ro.set_runtime_override("alpha", True)   # 翻转，应写

    assert [list(call.items()) for call in write_calls] == [
        [("alpha", False)],
        [("alpha", True)],
    ]


@pytest.mark.plugin_unit
def test_runtime_overrides_ignore_blank_plugin_id(_isolate_runtime_overrides):
    ro.set_runtime_override("", False)
    ro.clear_runtime_override("")
    assert ro.get_runtime_override("") is None
    assert ro.load_runtime_overrides() == {}


@pytest.mark.plugin_unit
def test_coerce_overrides_drops_invalid_entries():
    """Non-bool / non-string entries from disk are silently dropped."""
    coerced = ro._coerce_overrides({"good": True, "bad": "yes", 42: True, "neg": False})
    assert coerced == {"good": True, "neg": False}


@pytest.mark.plugin_unit
def test_coerce_overrides_handles_non_mapping():
    assert ro._coerce_overrides([1, 2, 3]) == {}
    assert ro._coerce_overrides(None) == {}


@pytest.mark.plugin_unit
def test_set_runtime_override_holds_cache_lock_during_disk_write(
    _isolate_runtime_overrides, monkeypatch
):
    """Regression: set/clear must serialize the disk write under _cache_lock.

    Releasing the lock before writing lets two concurrent toggles capture
    independent snapshots, then race on `_save_to_disk` order — the second
    write wins and the first toggle's plugin_id silently disappears.
    """
    lock_held_during_save: list[bool] = []
    original_save = ro._save_to_disk

    def _spy(overrides):
        # On a non-reentrant Lock, a non-blocking acquire from the holder thread
        # still returns False — so this is True iff the lock is currently held.
        acquired = ro._cache_lock.acquire(blocking=False)
        lock_held_during_save.append(not acquired)
        if acquired:
            ro._cache_lock.release()
        original_save(overrides)

    monkeypatch.setattr(ro, "_save_to_disk", _spy)
    ro.reset_cache_for_testing()

    ro.set_runtime_override("alpha", False)
    ro.clear_runtime_override("alpha")

    assert lock_held_during_save == [True, True]
