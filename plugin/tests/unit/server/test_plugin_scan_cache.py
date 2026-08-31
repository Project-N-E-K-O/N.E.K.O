"""Scanning a plugin is a whole interpreter; scanning it twice is a waste.

One scan is a throwaway subprocess — measured ~0.84 s on this machine, of which
~0.76 s is interpreter start plus importing the scanner framework, i.e. a cost
that has nothing to do with the plugin. Computing the cache key instead walks
the plugin directory and stats it: ~1 ms per plugin, three orders of magnitude
cheaper.

The interesting part is not the hit, it is when the cache must NOT answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.plugins import metadata_scanner as module

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _clean_cache():
    module.clear_plugin_metadata_scan_cache()
    yield
    module.clear_plugin_metadata_scan_cache()


def _plugin_dir(tmp_path: Path, body: str = "x = 1\n") -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (root / "entry.py").write_text(body, encoding="utf-8")
    return root / "plugin.toml"


def _scan(monkeypatch, config_path: Path, calls: list, **kw):
    def _fake(**inner):
        calls.append(inner["plugin_id"])
        return module.IsolatedPluginMetadata(
            entries_preview=[], handlers={}, entry_methods={}
        )

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)
    return module.scan_plugin_metadata_isolated(
        plugin_id="demo",
        module_path="entry",
        class_name="C",
        config_path=config_path,
        conf={},
        pdata={},
        **kw,
    )


def test_a_second_scan_of_unchanged_files_is_served_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the cache lookup."""
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert calls == ["demo"], f"扫了 {len(calls)} 次，缓存没命中"


def test_touching_a_neighbouring_module_invalidates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key covers the whole plugin directory, not just the entry file.

    Plugins routinely split code across sibling modules. Keying only on
    ``plugin.toml`` and the entry would serve a stale scan after an edit to a
    neighbour — and that is a bug nobody would think to blame on a cache.

    Mutation: narrow the fingerprint to the config file only.
    """
    config_path = _plugin_dir(tmp_path)
    (config_path.parent / "helper.py").write_text("y = 1\n", encoding="utf-8")
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    (config_path.parent / "helper.py").write_text("y = 2\n", encoding="utf-8")
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, "改了同目录别的模块，却拿到了旧扫描结果"


def test_force_bypasses_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install/upgrade/uninstall and the refresh button cannot trust the key.

    The fingerprint sees the plugin directory only, so a change in a shared
    vendor directory or in site-packages is invisible to it.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls, force=True)
    _scan(monkeypatch, config_path, calls)  # force 之后应重新填好缓存

    assert len(calls) == 2, f"force 没有绕过，或绕过后没有回填：calls={len(calls)}"


def test_failures_are_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that timed out describes the moment, not the plugin.

    Caching it would stick the failure to the plugin until something on disk
    changed — a transient slow disk would look like a broken plugin forever.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    def _boom(**inner):
        calls.append(inner["plugin_id"])
        raise module.PluginMetadataScanError("TimeoutExpired", "slow")

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _boom)
    for _ in range(2):
        with pytest.raises(module.PluginMetadataScanError):
            module.scan_plugin_metadata_isolated(
                plugin_id="demo",
                module_path="entry",
                class_name="C",
                config_path=config_path,
                conf={},
                pdata={},
            )

    assert len(calls) == 2, "失败被缓存了——下次刷新不会重试"


def test_a_different_config_is_a_different_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same files, different conf: the scan sees conf, so the key must too."""
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    def _fake(**inner):
        calls.append(inner["conf"])
        return module.IsolatedPluginMetadata(
            entries_preview=[], handlers={}, entry_methods={}
        )

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)
    for conf in ({"a": 1}, {"a": 2}, {"a": 1}):
        module.scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf=conf,
            pdata={},
        )

    assert calls == [{"a": 1}, {"a": 2}], f"conf 没进键：{calls}"
