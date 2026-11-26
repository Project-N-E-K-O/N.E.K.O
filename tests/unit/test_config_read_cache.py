# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Hot-path config read cache: writes invalidate at once, other changes after a lazy recheck."""

import json
import os

import pytest

from tests.fake_clock import patch_module_clock
from utils.config_manager import read_cache


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def cache(monkeypatch):
    monkeypatch.setenv("NEKO_CONFIG_READ_CACHE", "1")
    clock = _Clock()
    patch_module_clock(monkeypatch, read_cache, monotonic=clock)
    read_cache.invalidate()
    counts = {"stat": 0, "read": 0}
    real_stat, real_read = read_cache._signature, read_cache._read

    def counting_stat(path):
        counts["stat"] += 1
        return real_stat(path)

    def counting_read(path):
        counts["read"] += 1
        return real_read(path)

    monkeypatch.setattr(read_cache, "_signature", counting_stat)
    monkeypatch.setattr(read_cache, "_read", counting_read)
    yield clock, counts
    read_cache.invalidate()


def _rewrite(path, text):
    # Same size as before on purpose: only the file id / mtime may tell it apart.
    path.write_text(text, encoding="utf-8")


def test_repeated_reads_inside_the_window_touch_the_disk_once(tmp_path, cache):
    clock, counts = cache
    path = tmp_path / "core_config.json"
    path.write_text('{"a": 1}', encoding="utf-8")

    for _ in range(30):
        assert read_cache.read_text(path) == '{"a": 1}'
        clock.now += 0.01

    assert counts == {"stat": 1, "read": 1}


def test_lazy_recheck_rereads_only_when_the_file_changed(tmp_path, cache):
    clock, counts = cache
    path = tmp_path / "core_config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert read_cache.read_text(path) == '{"a": 1}'

    clock.now += read_cache.RECHECK_SECONDS + 0.1
    assert read_cache.read_text(path) == '{"a": 1}'
    assert counts == {"stat": 2, "read": 1}

    os.replace(_write_tmp(tmp_path, '{"a": 22}'), path)
    assert read_cache.read_text(path) == '{"a": 1}'  # still inside the new window
    clock.now += read_cache.RECHECK_SECONDS + 0.1
    assert read_cache.read_text(path) == '{"a": 22}'
    assert counts == {"stat": 3, "read": 2}


def _write_tmp(directory, text):
    tmp = directory / "staged.json"
    tmp.write_text(text, encoding="utf-8")
    return tmp


def test_no_background_checks_when_nobody_reads(tmp_path, cache):
    clock, counts = cache
    path = tmp_path / "core_config.json"
    path.write_text("{}", encoding="utf-8")
    read_cache.read_text(path)
    clock.now += 3600
    assert counts == {"stat": 1, "read": 1}


def test_invalidate_makes_the_next_read_fresh(tmp_path, cache):
    _, counts = cache
    path = tmp_path / "core_config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert read_cache.read_text(path) == '{"a": 1}'
    _rewrite(path, '{"a": 2}')
    read_cache.invalidate(path)
    assert read_cache.read_text(path) == '{"a": 2}'
    assert counts["read"] == 2


def test_missing_file_is_cached_and_its_creation_noticed(tmp_path, cache):
    clock, counts = cache
    path = tmp_path / "subscriptions.json"
    assert read_cache.read_text(path) is None
    assert read_cache.read_text(path) is None
    assert counts == {"stat": 1, "read": 0}
    path.write_text("{}", encoding="utf-8")
    clock.now += read_cache.RECHECK_SECONDS + 0.1
    assert read_cache.read_text(path) == "{}"


def test_switched_off_reads_every_time(tmp_path, cache, monkeypatch):
    _, counts = cache
    monkeypatch.setenv("NEKO_CONFIG_READ_CACHE", "0")
    path = tmp_path / "core_config.json"
    path.write_text("{}", encoding="utf-8")
    for _ in range(3):
        read_cache.read_text(path)
    assert counts == {"stat": 0, "read": 3}


def test_race_with_invalidation_does_not_keep_stale_text(tmp_path, cache, monkeypatch):
    path = tmp_path / "core_config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    real_read = read_cache._read

    def read_then_writer_saves(p):
        text = real_read(p)
        _rewrite(path, '{"a": 2}')
        read_cache.invalidate(path)
        return text

    monkeypatch.setattr(read_cache, "_read", read_then_writer_saves)
    assert read_cache.read_text(path) == '{"a": 1}'
    monkeypatch.setattr(read_cache, "_read", real_read)
    assert read_cache.read_text(path) == '{"a": 2}'


@pytest.fixture()
def config_manager(clean_user_data_dir):
    """The session's temp-root ConfigManager, with core_config.json put back afterwards."""
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    path = cm.config_dir / "core_config.json"
    original = path.read_bytes() if path.exists() else None
    yield cm
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)
    read_cache.invalidate()


def test_get_core_config_reads_the_file_once_per_window_and_sees_saves(config_manager, cache):
    _, counts = cache
    cm = config_manager
    cm.save_json_config("core_config.json", {"coreApi": "qwen", "assistApi": "qwen", "textGuardMaxLength": 123})
    try:
        first = cm.get_core_config()
        reads = counts["read"]
        for _ in range(10):
            assert cm.get_core_config()["TEXT_GUARD_MAX_LENGTH"] == 123
        assert counts["read"] == reads
        assert first["TEXT_GUARD_MAX_LENGTH"] == 123

        # A save through the config manager is visible on the very next read.
        cm.save_json_config("core_config.json", {"coreApi": "qwen", "assistApi": "qwen", "textGuardMaxLength": 456})
        assert cm.get_core_config()["TEXT_GUARD_MAX_LENGTH"] == 456

        # Each snapshot is a fresh object: mutating one never leaks into the next.
        snapshot = cm.get_core_config()
        snapshot["TEXT_GUARD_MAX_LENGTH"] = 1
        assert cm.get_core_config()["TEXT_GUARD_MAX_LENGTH"] == 456
    finally:
        read_cache.invalidate()


def test_update_json_config_invalidates(config_manager, cache):
    cm = config_manager
    cm.save_json_config("core_config.json", {"textGuardMaxLength": 111})
    assert cm.get_core_config()["TEXT_GUARD_MAX_LENGTH"] == 111

    def bump(data):
        data["textGuardMaxLength"] = 222

    cm.update_json_config("core_config.json", bump)
    assert cm.get_core_config()["TEXT_GUARD_MAX_LENGTH"] == 222
    assert json.loads((cm.config_dir / "core_config.json").read_text(encoding="utf-8"))["textGuardMaxLength"] == 222
