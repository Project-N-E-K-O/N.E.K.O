from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from plugin.sdk.shared import i18n as i18n_module
from plugin.sdk.shared.i18n import (
    clear_plugin_i18n_cache,
    load_plugin_i18n_from_dir,
    load_plugin_i18n_from_meta,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_plugin_i18n_cache()
    yield
    clear_plugin_i18n_cache()


@pytest.fixture()
def read_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    original = i18n_module._load_json_file_checked

    def counting(path: Path) -> tuple[dict[str, object], bool]:
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(i18n_module, "_load_json_file_checked", counting)
    return reads


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def _plugin(tmp_path: Path) -> tuple[dict[str, object], Path]:
    plugin_dir = tmp_path / "plugin"
    locales = plugin_dir / "i18n"
    locales.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    _write(locales / "en.json", {"plugin.name": "Demo"})
    _write(locales / "zh-CN.json", {"plugin.name": "演示"})
    return {"config_path": str(config_path)}, locales


def test_repeated_loads_hit_cache(tmp_path: Path, read_counter: list[str]) -> None:
    meta, _ = _plugin(tmp_path)

    first = load_plugin_i18n_from_meta(meta)
    assert sorted(read_counter) == ["en.json", "zh-CN.json"]

    for _ in range(5):
        again = load_plugin_i18n_from_meta(meta)
        assert again.messages == first.messages
        assert again.t("plugin.name", locale="zh-CN") == "演示"
    assert len(read_counter) == 2


def test_meta_and_dir_loaders_share_cache(tmp_path: Path, read_counter: list[str]) -> None:
    meta, locales = _plugin(tmp_path)

    load_plugin_i18n_from_meta(meta)
    loaded = load_plugin_i18n_from_dir(locales, default_locale="zh-CN")

    assert len(read_counter) == 2
    assert loaded.default_locale == "zh-CN"
    assert loaded.t("plugin.name") == "演示"


def test_modified_file_is_reloaded(tmp_path: Path, read_counter: list[str]) -> None:
    meta, locales = _plugin(tmp_path)
    load_plugin_i18n_from_meta(meta)

    _write(locales / "en.json", {"plugin.name": "Renamed"})
    _bump_mtime(locales / "en.json")

    assert load_plugin_i18n_from_meta(meta).t("plugin.name", locale="en") == "Renamed"
    assert len(read_counter) == 4


def test_same_size_rewrite_detected_by_mtime(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    load_plugin_i18n_from_meta(meta)

    _write(locales / "en.json", {"plugin.name": "Dumb"})  # same byte length as "Demo"
    _bump_mtime(locales / "en.json")

    assert load_plugin_i18n_from_meta(meta).t("plugin.name", locale="en") == "Dumb"


def test_added_and_removed_locale_files_are_picked_up(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    assert set(load_plugin_i18n_from_meta(meta).messages) == {"en", "zh-CN"}

    _write(locales / "ja.json", {"plugin.name": "デモ"})
    assert set(load_plugin_i18n_from_meta(meta).messages) == {"en", "zh-CN", "ja"}

    (locales / "zh-CN.json").unlink()
    assert set(load_plugin_i18n_from_meta(meta).messages) == {"en", "ja"}


def test_missing_locales_dir_then_created(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    for path in locales.iterdir():
        path.unlink()
    locales.rmdir()
    assert load_plugin_i18n_from_meta(meta).messages == {}

    locales.mkdir()
    _write(locales / "en.json", {"plugin.name": "Demo"})
    assert load_plugin_i18n_from_meta(meta).t("plugin.name") == "Demo"


def test_mutating_returned_object_does_not_poison_cache(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    _write(locales / "ja.json", {"plugin.name": "デモ", "nested": {"items": ["a"]}})

    first = load_plugin_i18n_from_meta(meta)
    first.messages["en"]["plugin.name"] = "poisoned"
    first.messages["ja"]["nested"]["items"].append("poisoned")  # type: ignore[index,union-attr]
    first.messages.pop("zh-CN")
    first.messages["xx"] = {"plugin.name": "poisoned"}

    second = load_plugin_i18n_from_meta(meta)
    assert second.messages["en"]["plugin.name"] == "Demo"
    assert second.messages["ja"]["nested"] == {"items": ["a"]}
    assert set(second.messages) == {"en", "zh-CN", "ja"}


def test_first_load_result_is_independent_from_cache(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    _write(locales / "ja.json", {"nested": {"items": ["a"]}})

    first = load_plugin_i18n_from_meta(meta)  # cache miss path
    first.messages["ja"]["nested"]["items"].append("poisoned")  # type: ignore[index,union-attr]

    assert load_plugin_i18n_from_meta(meta).messages["ja"]["nested"] == {"items": ["a"]}


def test_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n_module, "_BUNDLE_CACHE_MAX_ENTRIES", 3)
    for index in range(6):
        locales = tmp_path / f"p{index}"
        locales.mkdir()
        _write(locales / "en.json", {"k": str(index)})
        assert load_plugin_i18n_from_dir(locales).t("k") == str(index)

    assert len(i18n_module._bundle_cache) == 3
    assert str((tmp_path / "p5").resolve()) in i18n_module._bundle_cache
    assert str((tmp_path / "p0").resolve()) not in i18n_module._bundle_cache


def test_concurrent_loads_return_consistent_results(tmp_path: Path) -> None:
    meta, _ = _plugin(tmp_path)
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(20):
                results.append(load_plugin_i18n_from_meta(meta).t("plugin.name", locale="zh-CN"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert results == ["演示"] * workers * 20


def test_unstatable_entry_does_not_drop_other_locales(tmp_path: Path) -> None:
    meta, locales = _plugin(tmp_path)
    try:
        os.symlink(tmp_path / "missing.json", locales / "ja.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")

    assert set(load_plugin_i18n_from_meta(meta).messages) == {"en", "zh-CN"}


def test_transient_read_error_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    locales = tmp_path / "locales"
    locales.mkdir()
    _write(locales / "en.json", {"title": "Hello"})
    _write(locales / "zh-CN.json", {"title": "你好"})

    original_read_text = Path.read_text
    failing = {"on": True}

    def flaky_read_text(self: Path, *args, **kwargs):
        if failing["on"] and self.name == "zh-CN.json":
            raise PermissionError("locked")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    first = load_plugin_i18n_from_dir(locales, default_locale="en")
    assert first.t("title", locale="zh-CN") == "Hello"

    # Same stat signature after the error clears; the bundle must be re-read.
    failing["on"] = False
    second = load_plugin_i18n_from_dir(locales, default_locale="en")
    assert second.t("title", locale="zh-CN") == "你好"


def test_invalid_utf8_locale_is_skipped(tmp_path: Path) -> None:
    locales = tmp_path / "locales"
    locales.mkdir()
    _write(locales / "en.json", {"title": "Hello"})
    (locales / "zh-CN.json").write_bytes(b'{"title": "\xff\xfe"}')

    i18n = load_plugin_i18n_from_dir(locales, default_locale="en")
    assert i18n.t("title", locale="en") == "Hello"
    assert "zh-CN" not in i18n.messages
    # A permanently undecodable file is not a transient error; the result is cached.
    assert str(locales.resolve()) in i18n_module._bundle_cache


def test_plugin_replacement_invalidates_timestamp_preserving_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plugin.core import host as host_module
    from plugin.server.application.plugins.installation_transactions import replace as replace_module

    monkeypatch.setattr(host_module, "evict_cached_plugin_modules", lambda plugin_id: None)
    meta, locales = _plugin(tmp_path)
    load_plugin_i18n_from_meta(meta)

    target = locales / "en.json"
    before = target.stat()
    _write(target, {"plugin.name": "Dumb"})  # same byte length as "Demo"
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    replace_module._evict_replaced_plugin_modules("demo")

    assert load_plugin_i18n_from_meta(meta).t("plugin.name", locale="en") == "Dumb"
