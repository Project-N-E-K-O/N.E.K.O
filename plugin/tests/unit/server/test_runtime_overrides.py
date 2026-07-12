from __future__ import annotations

import pytest
import utils.config_manager as config_manager_module

from plugin.server.infrastructure import runtime_overrides as ro

_ORIGINAL_LOAD_FROM_DISK = ro._load_from_disk


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
def test_runtime_overrides_persist_auto_start_alongside_enabled(_isolate_runtime_overrides):
    ro.set_runtime_override("alpha", True, auto_start=True)

    assert ro.load_runtime_overrides() == {
        "alpha": {"enabled": True, "auto_start": True},
    }
    assert ro.get_runtime_override("alpha") is True
    assert ro.get_runtime_auto_start_override("alpha") is True

    ro.set_runtime_override("alpha", False, auto_start=False)

    assert ro.load_runtime_overrides() == {
        "alpha": {"enabled": False, "auto_start": False},
    }
    assert ro.get_runtime_override("alpha") is False
    assert ro.get_runtime_auto_start_override("alpha") is False


@pytest.mark.plugin_unit
def test_runtime_overrides_keep_legacy_boolean_entries_compatible(monkeypatch):
    monkeypatch.setattr(ro, "_load_from_disk", lambda: {"legacy": False})
    ro.reset_cache_for_testing()

    assert ro.get_runtime_override("legacy") is False
    assert ro.get_runtime_auto_start_override("legacy") is None


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
def test_coerce_overrides_rejects_invalid_entries():
    with pytest.raises(ro.RuntimeOverrideReadError, match="invalid entry"):
        ro._coerce_overrides({"good": True, "bad": "yes"})

    with pytest.raises(ro.RuntimeOverrideReadError, match="invalid fields"):
        ro._coerce_overrides({"bad": {"enabled": "yes"}})


@pytest.mark.plugin_unit
def test_coerce_overrides_handles_non_mapping():
    with pytest.raises(ro.RuntimeOverrideReadError, match="JSON object"):
        ro._coerce_overrides([1, 2, 3])
    with pytest.raises(ro.RuntimeOverrideReadError, match="JSON object"):
        ro._coerce_overrides(None)


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


@pytest.mark.plugin_unit
def test_runtime_override_read_error_is_not_treated_as_empty_or_cached(monkeypatch):
    class _ConfigManager:
        def load_json_config(self, _filename):
            raise ValueError("invalid json")

    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(ro, "_load_from_disk", _ORIGINAL_LOAD_FROM_DISK)
    ro.reset_cache_for_testing()

    with pytest.raises(ro.RuntimeOverrideReadError, match="Failed to load"):
        ro.load_runtime_overrides()

    monkeypatch.setattr(ro, "_load_from_disk", lambda: {"recovered": True})
    assert ro.load_runtime_overrides() == {"recovered": True}


@pytest.mark.plugin_unit
def test_runtime_override_write_error_does_not_commit_cache(
    _isolate_runtime_overrides,
    monkeypatch,
):
    ro.set_runtime_override("alpha", True, auto_start=True)

    def _fail_save(_overrides):
        raise ro.RuntimeOverrideWriteError("disk full")

    monkeypatch.setattr(ro, "_save_to_disk", _fail_save)

    with pytest.raises(ro.RuntimeOverrideWriteError, match="disk full"):
        ro.set_runtime_override("alpha", False, auto_start=False)

    assert ro.load_runtime_overrides() == {
        "alpha": {"enabled": True, "auto_start": True},
    }
