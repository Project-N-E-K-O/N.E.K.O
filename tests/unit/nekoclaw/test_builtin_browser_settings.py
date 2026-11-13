# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``browser.json``: which browser the browser tools use, and in which of the two modes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nekoclaw.builtin.browser import settings as settings_module
from nekoclaw.builtin.browser.settings import (
    MODE_ATTACH,
    MODE_OWN_PROFILE,
    MODES,
    BrowserSettings,
    BrowserSettingsStore,
)


def _write(store: BrowserSettingsStore, content: Any) -> None:
    store.path().parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    store.path().write_text(text, encoding="utf-8")


def test_the_defaults_are_an_own_profile_a_searched_browser_and_a_window() -> None:
    assert BrowserSettings() == BrowserSettings(mode=MODE_OWN_PROFILE, executable=None, headless=False)
    assert BrowserSettings().to_json() == {"mode": "own_profile", "executable": None, "headless": False}
    assert MODES == (MODE_OWN_PROFILE, MODE_ATTACH) == ("own_profile", "attach")


def test_a_missing_file_means_the_defaults(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    assert not store.path().exists()
    assert store.load() == BrowserSettings()
    # Reading creates nothing.
    assert list(tmp_path.iterdir()) == []


def test_what_is_saved_is_read_back(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    wanted = BrowserSettings(mode=MODE_ATTACH, executable=r"C:\Browsers\chrome.exe", headless=True)
    store.save(wanted)
    assert store.load() == wanted
    assert json.loads(store.path().read_text(encoding="utf-8")) == {
        "version": 1,
        "mode": "attach",
        "executable": r"C:\Browsers\chrome.exe",
        "headless": True,
    }


def test_saving_creates_the_folder_and_replaces_what_was_there(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path / "not" / "yet" / "there")
    store.save(BrowserSettings(mode=MODE_ATTACH))
    store.save(BrowserSettings(headless=True))
    assert store.load() == BrowserSettings(headless=True)


def test_a_change_of_the_file_is_seen_by_the_next_read(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    store.save(BrowserSettings())
    assert store.load().mode == MODE_OWN_PROFILE
    _write(store, {"version": 1, "mode": "attach"})
    assert store.load().mode == MODE_ATTACH


@pytest.mark.parametrize("mode", ["incognito", "", None, 3, ["attach"], "ATTACH"])
def test_an_unknown_mode_in_the_file_falls_back_to_the_own_profile(tmp_path: Path, mode: Any) -> None:
    store = BrowserSettingsStore(tmp_path)
    _write(store, {"version": 1, "mode": mode, "executable": "/usr/bin/chromium", "headless": True})
    assert store.load() == BrowserSettings(mode=MODE_OWN_PROFILE, executable="/usr/bin/chromium", headless=True)


@pytest.mark.parametrize("content", ["{not json", "", "[1, 2]", '"attach"', "null", "\ufffe\x00"])
def test_a_file_that_is_not_a_settings_object_means_the_defaults(tmp_path: Path, content: str) -> None:
    store = BrowserSettingsStore(tmp_path)
    _write(store, content)
    assert store.load() == BrowserSettings()


def test_a_file_that_cannot_be_read_means_the_defaults(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    # A folder where the file should be: reading it fails with an OS error.
    store.path().mkdir(parents=True)
    assert store.load() == BrowserSettings()


@pytest.mark.parametrize(
    ("executable", "kept"),
    [("  /opt/chrome/chrome  ", "/opt/chrome/chrome"), ("", None), ("   ", None), (None, None), (17, None), (["chrome"], None)],
)
def test_the_executable_is_trimmed_and_anything_but_a_path_means_search(tmp_path: Path, executable: Any, kept: str | None) -> None:
    store = BrowserSettingsStore(tmp_path)
    _write(store, {"mode": "own_profile", "executable": executable})
    assert store.load().executable == kept


@pytest.mark.parametrize(("headless", "kept"), [(True, True), (False, False), ("true", False), (1, False), (None, False)])
def test_headless_is_on_only_for_a_real_true(tmp_path: Path, headless: Any, kept: bool) -> None:
    store = BrowserSettingsStore(tmp_path)
    _write(store, {"headless": headless})
    assert store.load().headless is kept


def test_saving_an_unknown_mode_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    with pytest.raises(ValueError, match="unknown browser mode 'kiosk'"):
        store.save(BrowserSettings(mode="kiosk"))
    assert not store.path().exists()
    store.save(BrowserSettings(mode=MODE_ATTACH))
    with pytest.raises(ValueError):
        store.save(BrowserSettings(mode=""))
    assert store.load().mode == MODE_ATTACH


def test_the_file_and_the_profile_directory_are_below_the_root(tmp_path: Path) -> None:
    store = BrowserSettingsStore(tmp_path)
    assert store.root() == tmp_path
    assert store.path() == tmp_path / "browser.json"
    assert store.profile_dir() == tmp_path / "browser_profile"


def test_the_root_may_be_a_callable_asked_every_time(tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two"]
    store = BrowserSettingsStore(lambda: roots[0])
    assert store.profile_dir() == tmp_path / "one" / "browser_profile"
    roots[0] = roots[1]
    assert store.path() == tmp_path / "two" / "browser.json"


def test_a_root_given_as_text_is_a_path(tmp_path: Path) -> None:
    store = BrowserSettingsStore(str(tmp_path))  # type: ignore[arg-type]
    assert store.path() == tmp_path / "browser.json"


def test_without_a_root_the_files_live_in_the_nekoclaw_folder_of_the_data_root(tmp_path: Path, monkeypatch: Any) -> None:
    import utils.config_manager as config_manager

    asked: list[dict[str, Any]] = []

    def get_config_manager(**kwargs: Any) -> Any:
        asked.append(kwargs)
        return SimpleNamespace(app_docs_dir=str(tmp_path / "data root"))

    monkeypatch.setattr(config_manager, "get_config_manager", get_config_manager)
    store = BrowserSettingsStore()
    assert store.root() == tmp_path / "data root" / "nekoclaw"
    assert store.path() == tmp_path / "data root" / "nekoclaw" / "browser.json"
    assert store.profile_dir() == tmp_path / "data root" / "nekoclaw" / "browser_profile"
    # Looking the data root up must not set off a migration of the user's files.
    assert asked and all(item == {"migrate": False} for item in asked)
    assert settings_module._root() == tmp_path / "data root" / "nekoclaw"
