from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from plugin.utils.path_resolution import PathResolutionCache, canonical_read_path

pytestmark = pytest.mark.plugin_unit


def test_discovery_reuses_paths_without_affecting_other_reads(monkeypatch, tmp_path):
    calls = []
    original = Path.resolve

    def resolve(path, *, strict=False):
        calls.append((path, strict))
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    target = tmp_path / "plugin.toml"
    cache = PathResolutionCache()
    assert canonical_read_path(target, cache=cache) == canonical_read_path(
        target, cache=cache
    )
    assert len(calls) == 1
    canonical_read_path(target, cache=PathResolutionCache())
    canonical_read_path(target)
    canonical_read_path(target)
    assert len(calls) == 4


def test_cache_cannot_cross_threads(tmp_path):
    cache = PathResolutionCache()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="across threads"):
            pool.submit(cache.resolve, tmp_path).result()


def test_relative_paths_follow_current_directory(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    cache = PathResolutionCache()
    monkeypatch.chdir(a)
    assert cache.resolve(Path("file")) == a / "file"
    monkeypatch.chdir(b)
    assert cache.resolve(Path("file")) == b / "file"


def test_strict_resolution_is_not_satisfied_by_non_strict_result(tmp_path):
    cache = PathResolutionCache()
    target = tmp_path / "missing"
    cache.resolve(target)
    with pytest.raises(FileNotFoundError):
        cache.resolve(target, strict=True)
