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

"""Finding a browser, starting it, attaching to one, closing it and killing its process tree.

Nothing here starts, finds or kills a real process: the launcher's view of
the file system, of the environment, of ``subprocess`` and of ``psutil`` is
replaced for every test, and a test that reaches a real one fails.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nekoclaw.builtin.browser import launcher
from nekoclaw.builtin.browser.launcher import (
    ACTIVE_PORT_FILE,
    BrowserInstall,
    BrowserStartError,
    NoBrowserError,
    RunningBrowser,
)
from tests.fake_clock import patch_module_clock

REACHED = "a unit test reached a real process"


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(REACHED)


class _ScopedModule:
    """Stands in for a module inside the launcher: the overrides, else the real thing."""

    def __init__(self, real: Any, **overrides: Any) -> None:
        self._real = real
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        try:
            return self._overrides[name]
        except KeyError:
            return getattr(self._real, name)


def _subprocess(**overrides: Any) -> _ScopedModule:
    return _ScopedModule(subprocess, **{"Popen": _refuse, "run": _refuse, **overrides})


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch: Any) -> None:
    """Make starting, finding and killing real processes impossible; tests put their own fakes on top."""
    refusing = types.ModuleType("psutil")
    refusing.Process = _refuse  # type: ignore[attr-defined]
    refusing.process_iter = _refuse  # type: ignore[attr-defined]
    refusing.wait_procs = _refuse  # type: ignore[attr-defined]
    refusing.NoSuchProcess = type("NoSuchProcess", (Exception,), {})  # type: ignore[attr-defined]
    refusing.AccessDenied = type("AccessDenied", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", refusing)
    monkeypatch.setattr(launcher, "subprocess", _subprocess())
    monkeypatch.setattr(launcher, "_port_open", lambda port, timeout=0.5: False)


# ---------------------------------------------------------------------------
# A made-up computer for the search
# ---------------------------------------------------------------------------


class Machine:
    """The files, the environment and the commands the launcher sees."""

    def __init__(self, monkeypatch: Any) -> None:
        self.files: set[str] = set()
        self.environ: dict[str, str] = {}
        self.commands: dict[str, str] = {}
        self.default_kind: str | None = None
        path = _ScopedModule(os.path, isfile=lambda candidate: os.fspath(candidate) in self.files)
        monkeypatch.setattr(launcher, "os", _ScopedModule(os, path=path, environ=self.environ))
        monkeypatch.setattr(launcher, "shutil", SimpleNamespace(which=lambda command: self.commands.get(command)))
        monkeypatch.setattr(launcher, "_windows_default_kind", lambda: self.default_kind)

    def windows(self) -> "Machine":
        self.environ.update(
            {
                "PROGRAMFILES": os.path.join("X:", os.sep, "Program Files"),
                "PROGRAMFILES(X86)": os.path.join("X:", os.sep, "Program Files (x86)"),
                "LOCALAPPDATA": os.path.join("X:", os.sep, "Users", "ada", "AppData", "Local"),
            }
        )
        return self

    def install(self, root_variable: str, *relative: str) -> str:
        path = os.path.join(self.environ[root_variable], *relative)
        self.files.add(path)
        return path


WINDOWS_CHROME = ("Google", "Chrome", "Application", "chrome.exe")
WINDOWS_EDGE = ("Microsoft", "Edge", "Application", "msedge.exe")
WINDOWS_BRAVE = ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")
WINDOWS_CHROMIUM = ("Chromium", "Application", "chrome.exe")


@pytest.fixture
def machine(monkeypatch: Any) -> Machine:
    return Machine(monkeypatch)


# ---------------------------------------------------------------------------
# Finding a browser
# ---------------------------------------------------------------------------


def test_windows_browsers_are_found_in_the_three_program_folders_in_the_fixed_order(machine: Machine) -> None:
    machine.windows()
    chromium = machine.install("LOCALAPPDATA", *WINDOWS_CHROMIUM)
    brave = machine.install("LOCALAPPDATA", *WINDOWS_BRAVE)
    edge = machine.install("PROGRAMFILES(X86)", *WINDOWS_EDGE)
    chrome = machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    found = launcher.find_browsers("win32")
    assert [(item.kind, item.name, item.executable) for item in found] == [
        ("chrome", "Google Chrome", chrome),
        ("edge", "Microsoft Edge", edge),
        ("brave", "Brave", brave),
        ("chromium", "Chromium", chromium),
    ]
    local = machine.environ["LOCALAPPDATA"]
    assert [item.user_data_dir for item in found] == [
        os.path.join(local, "Google", "Chrome", "User Data"),
        os.path.join(local, "Microsoft", "Edge", "User Data"),
        os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
        os.path.join(local, "Chromium", "User Data"),
    ]


def test_a_browser_installed_in_two_places_on_windows_is_listed_once(machine: Machine) -> None:
    machine.windows()
    system_wide = machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    machine.install("LOCALAPPDATA", *WINDOWS_CHROME)
    found = launcher.find_browsers("win32")
    assert [item.executable for item in found] == [system_wide]


def test_without_a_local_app_data_folder_the_everyday_profile_is_unknown(machine: Machine) -> None:
    machine.environ["PROGRAMFILES"] = os.path.join("X:", os.sep, "Program Files")
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    (found,) = launcher.find_browsers("win32")
    assert found.kind == "chrome"
    assert found.user_data_dir is None


@pytest.mark.parametrize(
    ("default", "order"),
    [
        ("edge", ["edge", "chrome", "brave"]),
        ("brave", ["brave", "chrome", "edge"]),
        ("chrome", ["chrome", "edge", "brave"]),
        # Not installed, or not a browser that can be driven: the fixed order.
        ("chromium", ["chrome", "edge", "brave"]),
        (None, ["chrome", "edge", "brave"]),
    ],
)
def test_the_default_browser_of_windows_comes_first(machine: Machine, default: str | None, order: list[str]) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    machine.install("PROGRAMFILES(X86)", *WINDOWS_EDGE)
    machine.install("LOCALAPPDATA", *WINDOWS_BRAVE)
    machine.default_kind = default
    assert [item.kind for item in launcher.find_browsers("win32")] == order
    assert launcher.pick_browser(platform="win32").kind == order[0]


def test_the_default_browser_is_only_asked_for_on_windows(machine: Machine, monkeypatch: Any) -> None:
    monkeypatch.setattr(launcher, "_windows_default_kind", _refuse)
    machine.commands["chromium"] = "/usr/bin/chromium"
    assert [item.kind for item in launcher.find_browsers("linux")] == ["chromium"]
    assert launcher.find_browsers("darwin") == []


@pytest.mark.parametrize(
    ("prog_id", "kind"),
    [
        ("ChromeHTML", "chrome"),
        ("ChromeHTML.ABCDEF", "chrome"),
        ("MSEdgeHTM", "edge"),
        ("BraveHTML", "brave"),
        ("ChromiumHTM", "chromium"),
        ("FirefoxURL-308046B0AF4A39CB", None),
        ("", None),
    ],
)
def test_the_default_browser_is_read_from_the_https_association(monkeypatch: Any, prog_id: str, kind: str | None) -> None:
    opened: list[tuple[Any, str]] = []

    class Key:
        def __enter__(self) -> "Key":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

    registry = types.ModuleType("winreg")
    registry.HKEY_CURRENT_USER = "HKCU"  # type: ignore[attr-defined]
    registry.OpenKey = lambda root, path: opened.append((root, path)) or Key()  # type: ignore[attr-defined]
    registry.QueryValueEx = lambda key, name: (prog_id, 1) if name == "ProgId" else _refuse()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert launcher._windows_default_kind() == kind
    assert opened == [("HKCU", r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice")]


def test_no_readable_association_means_no_default_browser(monkeypatch: Any) -> None:
    registry = types.ModuleType("winreg")
    registry.HKEY_CURRENT_USER = "HKCU"  # type: ignore[attr-defined]

    def missing(*_args: Any) -> Any:
        raise FileNotFoundError("no such key")

    registry.OpenKey = missing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert launcher._windows_default_kind() is None
    # Where there is no registry at all (not Windows) the answer is the same.
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert launcher._windows_default_kind() is None


def test_mac_browsers_are_found_in_both_application_folders(machine: Machine) -> None:
    home = os.path.expanduser("~")
    chrome = os.path.join("/Applications", "Google Chrome.app/Contents/MacOS/Google Chrome")
    brave = os.path.join(home, "Applications", "Brave Browser.app/Contents/MacOS/Brave Browser")
    machine.files.update({chrome, brave})
    found = launcher.find_browsers("darwin")
    assert [(item.kind, item.name, item.executable) for item in found] == [
        ("chrome", "Google Chrome", chrome),
        ("brave", "Brave", brave),
    ]
    support = os.path.join(home, "Library", "Application Support")
    assert [item.user_data_dir for item in found] == [
        os.path.join(support, "Google/Chrome"),
        os.path.join(support, "BraveSoftware/Brave-Browser"),
    ]


def test_linux_browsers_are_found_by_their_commands(machine: Machine) -> None:
    machine.environ["XDG_CONFIG_HOME"] = "/home/ada/.cfg"
    machine.commands.update(
        {
            "google-chrome-stable": "/usr/bin/google-chrome-stable",
            "microsoft-edge": "/usr/bin/microsoft-edge",
            "brave": "/snap/bin/brave",
            "chromium-browser": "/usr/bin/chromium-browser",
        }
    )
    found = launcher.find_browsers("linux")
    assert [(item.kind, item.executable) for item in found] == [
        ("chrome", "/usr/bin/google-chrome-stable"),
        ("edge", "/usr/bin/microsoft-edge"),
        ("brave", "/snap/bin/brave"),
        ("chromium", "/usr/bin/chromium-browser"),
    ]
    assert [item.user_data_dir for item in found] == [
        os.path.join("/home/ada/.cfg", "google-chrome"),
        os.path.join("/home/ada/.cfg", "microsoft-edge"),
        os.path.join("/home/ada/.cfg", "BraveSoftware/Brave-Browser"),
        os.path.join("/home/ada/.cfg", "chromium"),
    ]


def test_the_first_command_of_a_linux_browser_wins_and_the_profile_defaults_to_dot_config(machine: Machine) -> None:
    machine.commands.update({"google-chrome": "/opt/google/chrome", "google-chrome-stable": "/usr/bin/google-chrome-stable"})
    (found,) = launcher.find_browsers("linux")
    assert found.executable == "/opt/google/chrome"
    assert found.user_data_dir == os.path.join(os.path.expanduser("~"), ".config", "google-chrome")


def test_the_platform_of_this_process_is_used_when_none_is_named(machine: Machine, monkeypatch: Any) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    machine.commands["chromium"] = "/usr/bin/chromium"
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    assert [item.kind for item in launcher.find_browsers()] == ["chrome"]
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    assert [item.kind for item in launcher.find_browsers()] == ["chromium"]


def test_the_environment_override_comes_before_everything_found(machine: Machine) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    machine.default_kind = "chrome"
    portable = os.path.join("D:", os.sep, "Portable", "thorium.exe")
    machine.files.add(portable)
    machine.environ[launcher.EXECUTABLE_ENV] = f"  {portable}  "
    found = launcher.find_browsers("win32")
    assert [item.kind for item in found] == ["custom", "chrome"]
    assert found[0] == BrowserInstall("custom", "thorium.exe", portable, None)
    assert launcher.pick_browser(platform="win32") == found[0]


def test_an_environment_override_that_is_not_a_file_is_ignored(machine: Machine) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    machine.environ[launcher.EXECUTABLE_ENV] = os.path.join("D:", os.sep, "gone", "chrome.exe")
    assert [item.kind for item in launcher.find_browsers("win32")] == ["chrome"]


def test_the_name_of_the_environment_override() -> None:
    assert launcher.EXECUTABLE_ENV == "NEKO_BROWSER_PATH"


def test_no_browser_at_all_is_an_error_that_names_the_kinds_looked_for(machine: Machine) -> None:
    machine.windows()
    for platform in ("win32", "darwin", "linux"):
        assert launcher.find_browsers(platform) == []
        with pytest.raises(NoBrowserError, match="Chrome, Edge, Brave, Chromium"):
            launcher.pick_browser(platform=platform)


def test_the_executable_of_the_settings_picks_that_install_among_those_found(machine: Machine) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    edge = machine.install("PROGRAMFILES(X86)", *WINDOWS_EDGE)
    # Written another way than the search finds it: a detour through a folder, and on Windows another case.
    detour = os.path.join(os.path.dirname(edge), "..", "Application", os.path.basename(edge))
    if os.path.normcase("A") == "a":
        detour = detour.upper()
    picked = launcher.pick_browser(detour, platform="win32")
    assert picked.kind == "edge"
    assert picked.executable == edge
    assert picked.user_data_dir is not None


def test_an_executable_of_the_settings_that_was_not_found_by_the_search_is_used_as_it_is(machine: Machine) -> None:
    machine.windows()
    machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    portable = os.path.join("D:", os.sep, "Apps", "PortableChrome", "chrome.exe")
    machine.files.add(portable)
    assert launcher.pick_browser(portable, platform="win32") == BrowserInstall("custom", "chrome.exe", portable, None)


def test_an_executable_of_the_settings_that_is_no_browser_is_not_started(machine: Machine) -> None:
    machine.windows()
    chrome = machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    # It would be started with a debugging port and a profile directory, whatever it is.
    for name in ("powershell.exe", "cmd.exe", "notepad.exe", "chrome.exe.bat", "vivaldi.exe"):
        other = os.path.join("C:", os.sep, "Windows", "System32", name)
        machine.files.add(other)
        assert launcher.pick_browser(other, platform="win32").executable == chrome


@pytest.mark.parametrize(
    ("path", "is_one"),
    [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", True),
        (r"D:\Apps\Edge\MSEDGE.EXE", True),
        ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", True),
        ("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", True),
        ("/usr/bin/chromium-browser", True),
        ("/usr/bin/microsoft-edge-stable", True),
        # Every release channel of them: on macOS and Linux the channel is part of the name.
        ("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary", True),
        ("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", True),
        ("/Applications/Microsoft Edge Beta.app/Contents/MacOS/Microsoft Edge Beta", True),
        ("/Applications/Brave Browser Nightly.app/Contents/MacOS/Brave Browser Nightly", True),
        ("/usr/bin/google-chrome-unstable", True),
        ("/usr/bin/microsoft-edge-dev", True),
        ("/usr/bin/brave-browser-beta", True),
        (r"C:\Users\ada\AppData\Local\Google\Chrome SxS\Application\chrome.exe", True),
        # Not what merely begins or ends like one.
        ("/usr/bin/google-chrome-wrapper", False),
        ("/usr/bin/not-chromium", False),
        ("/Applications/Google Chrome Helper", False),
        ("/usr/bin/python3", False),
        (r"C:\Windows\System32\cmd.exe", False),
        (r"C:\Tools\chrome.exe.cmd", False),
        ("/tmp/chrome/run.sh", False),
        ("", False),
    ],
)
def test_a_browser_is_told_by_the_name_of_its_file(path: str, is_one: bool) -> None:
    import ntpath
    import posixpath

    # Asked about paths of every platform, on whichever this runs.
    name = ntpath.basename(path) if "\\" in path else posixpath.basename(path)
    assert launcher.is_browser_executable(name) is is_one


def test_an_executable_of_the_settings_that_does_not_exist_falls_back_to_the_search(machine: Machine) -> None:
    machine.windows()
    chrome = machine.install("PROGRAMFILES", *WINDOWS_CHROME)
    assert launcher.pick_browser(os.path.join("D:", os.sep, "gone.exe"), platform="win32").executable == chrome


def test_an_executable_of_the_settings_is_enough_when_the_search_finds_nothing(machine: Machine) -> None:
    only = os.path.join("D:", os.sep, "Apps", "chrome.exe")
    machine.files.add(only)
    assert launcher.pick_browser(only, platform="linux").executable == only
    with pytest.raises(NoBrowserError):
        launcher.pick_browser(os.path.join("D:", os.sep, "gone.exe"), platform="linux")


# ---------------------------------------------------------------------------
# Whether a window can be shown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macos_always_have_a_display(machine: Machine, platform: str) -> None:
    assert machine.environ == {}
    assert launcher.display_available(platform) is True


@pytest.mark.parametrize(
    ("environment", "available"),
    [
        ({}, False),
        ({"DISPLAY": ":0"}, True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"DISPLAY": ":1", "WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"DISPLAY": "", "WAYLAND_DISPLAY": ""}, False),
        ({"XDG_SESSION_TYPE": "x11", "TERM": "xterm"}, False),
    ],
)
def test_linux_has_a_display_only_when_a_display_server_is_named(
    machine: Machine, environment: dict[str, str], available: bool
) -> None:
    machine.environ.update(environment)
    assert launcher.display_available("linux") is available
    # Any other system is treated the same way.
    assert launcher.display_available("freebsd14") is available


def test_the_display_of_this_process_platform_is_meant_when_none_is_named(machine: Machine, monkeypatch: Any) -> None:
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    assert launcher.display_available() is False
    machine.environ["DISPLAY"] = ":0"
    assert launcher.display_available() is True
    machine.environ.clear()
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    assert launcher.display_available() is True


# ---------------------------------------------------------------------------
# The debugging endpoint
# ---------------------------------------------------------------------------


def _write_port_file(profile: Path, text: str) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    # Bytes, so the line ends are those of the test on every system.
    (profile / ACTIVE_PORT_FILE).write_bytes(text.encode("utf-8"))


def test_the_port_file_gives_the_port_and_the_path_of_the_browser_endpoint(tmp_path: Path) -> None:
    # The browser writes two lines without a line end after the second.
    _write_port_file(tmp_path, "9222\n/devtools/browser/0a1b-2c3d")
    assert launcher.read_active_port(tmp_path) == (9222, "/devtools/browser/0a1b-2c3d")
    assert launcher.read_active_port(str(tmp_path)) == (9222, "/devtools/browser/0a1b-2c3d")
    _write_port_file(tmp_path, "  51234  \r\n  /devtools/browser/x  \r\nmore\n")
    assert launcher.read_active_port(tmp_path) == (51234, "/devtools/browser/x")


def test_the_name_of_the_port_file() -> None:
    assert ACTIVE_PORT_FILE == "DevToolsActivePort"


def test_a_missing_port_file_or_profile_gives_nothing(tmp_path: Path) -> None:
    assert launcher.read_active_port(tmp_path) is None
    assert launcher.read_active_port(tmp_path / "no such profile") is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "9222",
        "9222\n",
        "\n/devtools/browser/x\n",
        "port\n/devtools/browser/x\n",
        "-1\n/devtools/browser/x\n",
        "92.5\n/devtools/browser/x\n",
        "9222\n/devtools/page/x\n",
        "9222\nws://evil.example/devtools/browser/x\n",
        "9222\n\n/devtools/browser/x\n",
    ],
)
def test_a_malformed_port_file_gives_nothing(tmp_path: Path, text: str) -> None:
    _write_port_file(tmp_path, text)
    assert launcher.read_active_port(tmp_path) is None


def test_the_endpoint_is_always_on_this_computer() -> None:
    assert launcher.endpoint_url(9222, "/devtools/browser/abc") == "ws://127.0.0.1:9222/devtools/browser/abc"


def test_a_browser_runs_on_a_profile_when_its_port_file_names_a_port_that_answers(tmp_path: Path, monkeypatch: Any) -> None:
    asked: list[int] = []
    answering: set[int] = set()

    def port_open(port: int, timeout: float = 0.5) -> bool:
        asked.append(port)
        return port in answering

    monkeypatch.setattr(launcher, "_port_open", port_open)
    assert launcher.running_endpoint(tmp_path) is None
    assert asked == []
    _write_port_file(tmp_path, "9333\n/devtools/browser/abc\n")
    assert launcher.running_endpoint(tmp_path) is None
    answering.add(9333)
    assert launcher.running_endpoint(tmp_path) == "ws://127.0.0.1:9333/devtools/browser/abc"
    assert asked == [9333, 9333]


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def test_the_browser_is_started_on_a_port_of_its_choice_and_the_profile_given() -> None:
    arguments = launcher.launch_arguments("/opt/chrome/chrome", "/data/nekoclaw/browser_profile")
    assert arguments[0] == "/opt/chrome/chrome"
    assert "--remote-debugging-port=0" in arguments
    assert "--user-data-dir=/data/nekoclaw/browser_profile" in arguments
    assert "--no-first-run" in arguments and "--no-default-browser-check" in arguments
    assert arguments[-1] == "about:blank"
    assert not any(argument.startswith("--headless") for argument in arguments)


def test_headless_is_passed_only_when_asked_and_the_blank_page_stays_last() -> None:
    arguments = launcher.launch_arguments("chrome", "profile", headless=True, extra=("--window-size=800,600", "--lang=en"))
    assert "--headless=new" in arguments
    assert arguments[-3:] == ["--window-size=800,600", "--lang=en", "about:blank"]
    plain = launcher.launch_arguments("chrome", "profile", extra=("--lang=en",))
    assert "--headless=new" not in plain
    assert plain[-2:] == ["--lang=en", "about:blank"]


class FakeClock:
    """A clock that only moves when the launcher sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.on_sleep: list[Any] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        # Rounded, so that ten sleeps of a tenth are exactly one second.
        self.now = round(self.now + seconds, 6)
        for hook in list(self.on_sleep):
            hook(len(self.sleeps))


class FakeChild:
    """What ``subprocess.Popen`` returns: a child that is alive until told otherwise."""

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.waits: list[float | None] = []
        self.exits_within_wait = True

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if not self.exits_within_wait:
            raise subprocess.TimeoutExpired("browser", timeout or 0)
        self.returncode = 0
        return 0


class Starter:
    """The pieces ``_start`` uses, faked: the clock, ``Popen``, the port check, the killing."""

    def __init__(self, monkeypatch: Any, profile: Path) -> None:
        self.profile = profile
        self.clock = FakeClock()
        self.child = FakeChild()
        self.started: list[tuple[list[str], dict[str, Any]]] = []
        self.open_ports: set[int] = set()
        self.port_checks: list[int] = []
        self.killed: list[Any] = []
        self.on_start: list[Any] = []
        self.process_on_profile: int | None = None
        # Whether some browser on this computer keeps its command line to itself.
        self.unreadable_browser = False
        patch_module_clock(monkeypatch, launcher, monotonic=self.clock.monotonic, sleep=self.clock.sleep)
        monkeypatch.setattr(launcher, "LAUNCH_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(launcher, "subprocess", _subprocess(Popen=self._popen))
        monkeypatch.setattr(launcher, "_port_open", self._port_open)
        monkeypatch.setattr(launcher, "kill_tree", self.killed.append)
        monkeypatch.setattr(launcher, "browser_process_on", self._browser_process_on)
        # Not the processes of the computer the tests run on.
        monkeypatch.setattr(launcher, "_a_browser_cannot_be_looked_at", lambda: self.unreadable_browser)
        monkeypatch.setattr(launcher, "_created", lambda pid: 1000.0 + pid)

    def _popen(self, arguments: list[str], **options: Any) -> FakeChild:
        self.started.append((list(arguments), options))
        for hook in self.on_start:
            hook()
        return self.child

    def _port_open(self, port: int, timeout: float = 0.5) -> bool:
        self.port_checks.append(port)
        return port in self.open_ports

    def _browser_process_on(self, profile: Any) -> int | None:
        assert Path(profile) == self.profile
        return self.process_on_profile

    def browser_listens(self, port: int, path: str = "/devtools/browser/new") -> None:
        _write_port_file(self.profile, f"{port}\n{path}\n")
        self.open_ports.add(port)


CHROME = BrowserInstall("chrome", "Google Chrome", "/opt/chrome/chrome", "/home/ada/.config/google-chrome")


@pytest.fixture
def starter(monkeypatch: Any, tmp_path: Path) -> Starter:
    return Starter(monkeypatch, tmp_path / "nekoclaw" / "browser_profile")


def test_a_browser_already_running_on_the_profile_is_used_and_nothing_is_started(starter: Starter) -> None:
    starter.browser_listens(9555, "/devtools/browser/left-running")
    starter.process_on_profile = 7001
    browser = launcher._start(CHROME, str(starter.profile), False, ())
    assert browser == RunningBrowser(
        install=CHROME,
        endpoint="ws://127.0.0.1:9555/devtools/browser/left-running",
        process=None,
        owned=True,
        user_data_dir=str(starter.profile),
    )
    assert starter.started == []
    assert starter.killed == []
    assert browser.pid is None


def test_starting_creates_the_profile_and_waits_for_the_port_the_browser_writes(starter: Starter) -> None:
    assert not starter.profile.exists()
    # The browser needs a moment: its port file appears while the launcher waits.
    starter.clock.on_sleep.append(lambda count: starter.browser_listens(9666) if count == 3 else None)
    browser = launcher._start(CHROME, str(starter.profile), True, ("--lang=en",))
    assert starter.profile.is_dir()
    assert browser.endpoint == "ws://127.0.0.1:9666/devtools/browser/new"
    assert browser.process is starter.child and browser.pid == 4321
    assert browser.owned is True
    assert browser.user_data_dir == str(starter.profile)
    assert browser.install is CHROME
    ((arguments, options),) = starter.started
    assert arguments == launcher.launch_arguments(CHROME.executable, str(starter.profile), headless=True, extra=("--lang=en",))
    assert options["stdin"] is options["stdout"] is options["stderr"] is subprocess.DEVNULL
    assert starter.clock.sleeps == [0.1, 0.1, 0.1]
    assert starter.killed == []


def test_the_child_gets_a_session_of_its_own_except_on_windows(starter: Starter, monkeypatch: Any) -> None:
    starter.on_start.append(lambda: starter.browser_listens(9666))
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    launcher._start(CHROME, str(starter.profile), False, ())
    assert starter.started[-1][1].get("start_new_session") is True
    (starter.profile / ACTIVE_PORT_FILE).unlink()
    starter.open_ports.clear()
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    launcher._start(CHROME, str(starter.profile), False, ())
    assert "start_new_session" not in starter.started[-1][1]


def test_a_stale_port_file_is_removed_before_the_browser_is_started(starter: Starter) -> None:
    _write_port_file(starter.profile, "9111\n/devtools/browser/stale\n")
    seen_at_start: list[bool] = []
    starter.on_start.append(lambda: seen_at_start.append((starter.profile / ACTIVE_PORT_FILE).exists()))
    # Something else starts to listen on the old port once the browser is being
    # started; the old file must not make that pass for the browser.
    starter.on_start.append(lambda: starter.open_ports.add(9111))
    starter.clock.on_sleep.append(lambda count: starter.browser_listens(9222) if count == 2 else None)
    browser = launcher._start(CHROME, str(starter.profile), False, ())
    assert seen_at_start == [False]
    assert browser.endpoint == "ws://127.0.0.1:9222/devtools/browser/new"
    assert starter.port_checks[0] == 9111 and 9111 not in starter.port_checks[1:]


def test_a_child_that_hands_over_to_another_process_and_exits_still_yields_a_browser(starter: Starter) -> None:
    def hand_over() -> None:
        starter.browser_listens(9777)
        starter.child.returncode = 0

    starter.on_start.append(hand_over)
    browser = launcher._start(CHROME, str(starter.profile), False, ())
    assert browser.endpoint == "ws://127.0.0.1:9777/devtools/browser/new"
    assert browser.process is None and browser.pid is None
    assert browser.owned is True
    assert browser.user_data_dir == str(starter.profile)
    assert starter.killed == []


def test_a_child_that_exits_without_a_port_file_is_an_error_after_the_wait_and_the_profile_is_cleared_of_processes(
    starter: Starter,
) -> None:
    starter.on_start.append(lambda: setattr(starter.child, "returncode", 21))
    starter.process_on_profile = 8765
    with pytest.raises(BrowserStartError) as caught:
        launcher._start(CHROME, str(starter.profile), False, ())
    assert "Google Chrome exited (code 21) without opening its debugging port" in str(caught.value)
    assert "administrator" in str(caught.value)
    # The whole wait was used: the process it handed over to may have needed it.
    assert sum(starter.clock.sleeps) == pytest.approx(1.0)
    # Not the child (it is gone) but whatever runs on the profile is killed.
    assert starter.killed == [8765]


def test_a_live_child_that_never_opens_the_port_is_killed_and_reported(starter: Starter) -> None:
    starter.process_on_profile = 8765
    with pytest.raises(BrowserStartError, match="Google Chrome did not open its debugging port within 1s"):
        launcher._start(CHROME, str(starter.profile), False, ())
    assert starter.killed == [4321]
    assert sum(starter.clock.sleeps) == pytest.approx(1.0)


def test_a_port_file_whose_port_does_not_answer_is_not_enough(starter: Starter) -> None:
    starter.on_start.append(lambda: _write_port_file(starter.profile, "9888\n/devtools/browser/new\n"))
    with pytest.raises(BrowserStartError, match="did not open its debugging port"):
        launcher._start(CHROME, str(starter.profile), False, ())
    assert starter.killed == [4321]


def test_a_browser_that_cannot_be_started_at_all_is_reported_with_its_name(starter: Starter, monkeypatch: Any) -> None:
    def popen(arguments: list[str], **options: Any) -> Any:
        raise PermissionError("access denied")

    monkeypatch.setattr(launcher, "subprocess", _subprocess(Popen=popen))
    with pytest.raises(BrowserStartError, match="Google Chrome could not be started: access denied"):
        launcher._start(CHROME, str(starter.profile), False, ())
    assert starter.killed == []


async def test_launch_starts_in_a_worker_thread_with_what_it_was_given(monkeypatch: Any) -> None:
    import threading

    calls: list[tuple[Any, ...]] = []
    made = RunningBrowser(install=CHROME, endpoint="ws://127.0.0.1:1/devtools/browser/x")

    def start(*arguments: Any) -> RunningBrowser:
        calls.append((*arguments, threading.current_thread() is threading.main_thread()))
        return made

    monkeypatch.setattr(launcher, "_start", start)
    stop = threading.Event()
    assert await launcher.launch(CHROME, "/profile", headless=True, extra=("--lang=en",), stop=stop) is made
    assert await launcher.launch(CHROME, "/profile") is made
    # What it is told to stop with goes along to the thread.
    assert calls == [(CHROME, "/profile", True, ("--lang=en",), stop, False), (CHROME, "/profile", False, (), None, False)]


# ---------------------------------------------------------------------------
# Attaching to the browser the user is using
# ---------------------------------------------------------------------------


async def test_a_browser_whose_everyday_profile_is_not_known_cannot_be_attached_to(monkeypatch: Any) -> None:
    monkeypatch.setattr(launcher, "running_endpoint", _refuse)
    custom = BrowserInstall("custom", "thorium.exe", "/opt/thorium/thorium", None)
    with pytest.raises(BrowserStartError, match="where thorium.exe keeps its profile is not known"):
        await launcher.attach(custom)


async def test_a_browser_not_running_with_remote_debugging_says_what_to_switch_on(monkeypatch: Any) -> None:
    asked: list[Any] = []
    monkeypatch.setattr(launcher, "running_endpoint", lambda profile: asked.append(profile))
    with pytest.raises(BrowserStartError) as caught:
        await launcher.attach(CHROME)
    assert asked == [CHROME.user_data_dir]
    message = str(caught.value)
    assert "Google Chrome is not running with remote debugging on" in message
    assert "chrome://inspect/#remote-debugging" in message
    assert "Chrome 144 or later" in message


async def test_a_browser_running_with_remote_debugging_is_attached_to_and_is_not_ours(monkeypatch: Any) -> None:
    endpoint = "ws://127.0.0.1:9222/devtools/browser/theirs"
    monkeypatch.setattr(launcher, "running_endpoint", lambda profile: endpoint)
    browser = await launcher.attach(CHROME)
    assert browser == RunningBrowser(
        install=CHROME, endpoint=endpoint, process=None, owned=False, user_data_dir=CHROME.user_data_dir
    )


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, *, closed: bool = False, fails: bool = False) -> None:
        self.closed = closed
        self.fails = fails
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, method: str, params: Any = None, **options: Any) -> dict[str, Any]:
        self.sent.append((method, options))
        if self.fails:
            raise RuntimeError("the socket broke")
        return {}


class Closer:
    """The pieces ``close`` uses, faked."""

    def __init__(self, monkeypatch: Any) -> None:
        self.killed: list[Any] = []
        self.looked_up: list[Any] = []
        self.process_on_profile: int | None = 7007
        self.endpoint_after_asking: str | None = None
        self.endpoint_checks: list[Any] = []
        self.slept: list[float] = []
        monkeypatch.setattr(launcher, "kill_tree", self.killed.append)
        monkeypatch.setattr(launcher, "browser_process_on", self._browser_process_on)
        monkeypatch.setattr(launcher, "running_endpoint", self._running_endpoint)
        monkeypatch.setattr(launcher, "asyncio", _ScopedModule(asyncio, sleep=self._sleep))

    def _browser_process_on(self, profile: Any) -> int | None:
        self.looked_up.append(profile)
        return self.process_on_profile

    def _running_endpoint(self, profile: Any) -> str | None:
        self.endpoint_checks.append(profile)
        return self.endpoint_after_asking

    async def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture
def closer(monkeypatch: Any) -> Closer:
    return Closer(monkeypatch)


def _ours(child: FakeChild | None, profile: str | None = "/data/profile") -> RunningBrowser:
    return RunningBrowser(install=CHROME, endpoint="ws://127.0.0.1:1/x", process=child, owned=True, user_data_dir=profile)


async def test_a_browser_that_was_attached_to_is_left_running(closer: Closer) -> None:
    connection = FakeConnection()
    theirs = RunningBrowser(install=CHROME, endpoint="ws://127.0.0.1:1/x", process=None, owned=False, user_data_dir="/their/profile")
    await launcher.close(theirs, connection)
    await launcher.close(theirs)
    assert connection.sent == []
    assert closer.killed == [] and closer.looked_up == []


async def test_our_child_asked_to_close_that_exits_is_not_killed(closer: Closer) -> None:
    child = FakeChild()
    connection = FakeConnection()
    closer.process_on_profile = None
    await launcher.close(_ours(child), connection)
    assert connection.sent == [("Browser.close", {"timeout": 3.0})]
    assert child.waits == [launcher.CLOSE_WAIT_SECONDS]
    assert closer.killed == []
    # Our own child is known by its pid. The profile is looked at once it went, and only then:
    # nothing is left on it, so the child was the browser.
    assert closer.looked_up == ["/data/profile"] and closer.slept == []


async def test_a_child_that_went_while_the_browser_runs_on_as_another_process_is_not_taken_for_the_browser(
    closer: Closer,
) -> None:
    # The child handed over after its port was open; what it handed over to is asked to close and stays.
    child = FakeChild()
    connection = FakeConnection()
    closer.endpoint_after_asking = "ws://127.0.0.1:9555/devtools/browser/still-here"
    await launcher.close(_ours(child), connection)
    assert child.waits == [launcher.CLOSE_WAIT_SECONDS]
    # Its exit cannot be waited for: a moment, another look at its port, then by the number found on the profile.
    assert closer.slept == [1.0] and closer.endpoint_checks == ["/data/profile"]
    assert closer.killed == [7007]


async def test_what_a_child_handed_over_to_is_left_alone_once_it_closed(closer: Closer) -> None:
    child = FakeChild()
    connection = FakeConnection()
    closer.endpoint_after_asking = None
    await launcher.close(_ours(child), connection)
    assert closer.slept == [1.0] and closer.killed == []


async def test_a_child_that_had_exited_before_the_closing_is_not_waited_for_or_killed_by_its_number(closer: Closer) -> None:
    child = FakeChild(pid=5150)
    child.returncode = 0
    connection = FakeConnection()
    closer.endpoint_after_asking = "ws://127.0.0.1:9555/devtools/browser/still-here"
    await launcher.close(_ours(child), connection)
    # Gone long ago: waiting for it would come back at once and say nothing about the browser.
    assert child.waits == []
    assert closer.slept == [1.0]
    # Number 5150 may be anybody's by now; the browser is the one on the profile.
    assert closer.killed == [7007]


async def test_our_child_asked_to_close_that_stays_is_killed_with_its_tree(closer: Closer) -> None:
    child = FakeChild(pid=5150)
    child.exits_within_wait = False
    connection = FakeConnection()
    await launcher.close(_ours(child), connection)
    assert connection.sent == [("Browser.close", {"timeout": 3.0})]
    assert closer.killed == [5150]


@pytest.mark.parametrize("connection", [None, FakeConnection(closed=True), FakeConnection(fails=True)])
async def test_our_child_that_cannot_be_asked_is_killed_without_waiting(closer: Closer, connection: Any) -> None:
    child = FakeChild(pid=5150)
    await launcher.close(_ours(child), connection)
    assert child.waits == []
    assert closer.killed == [5150]
    if connection is not None and connection.closed:
        assert connection.sent == []


async def test_a_browser_that_is_not_our_child_is_looked_up_by_its_profile_and_left_alone_once_it_closed(closer: Closer) -> None:
    connection = FakeConnection()
    closer.endpoint_after_asking = None
    await launcher.close(_ours(None), connection)
    # Its process is looked for only when it has to be killed, and it has not.
    assert closer.looked_up == []
    assert connection.sent == [("Browser.close", {"timeout": 3.0})]
    # It cannot be waited for, so it is given a moment and its endpoint is looked at again.
    assert closer.slept == [1.0]
    assert closer.endpoint_checks == ["/data/profile"]
    assert closer.killed == []


async def test_a_browser_that_is_not_our_child_and_does_not_close_is_killed_by_the_pid_found_on_its_profile(closer: Closer) -> None:
    connection = FakeConnection()
    closer.endpoint_after_asking = "ws://127.0.0.1:9555/devtools/browser/still-here"
    await launcher.close(_ours(None), connection)
    assert closer.killed == [7007]


async def test_a_browser_that_is_not_our_child_and_cannot_be_asked_is_killed_at_once(closer: Closer) -> None:
    await launcher.close(_ours(None), None)
    assert closer.looked_up == ["/data/profile"]
    assert closer.slept == []
    assert closer.killed == [7007]


async def test_nothing_is_killed_when_no_process_is_found_on_the_profile(closer: Closer) -> None:
    closer.process_on_profile = None
    await launcher.close(_ours(None), None)
    # ``kill_tree(None)`` does nothing.
    assert closer.killed == [None]


def test_waiting_for_a_child_to_exit() -> None:
    assert launcher._wait_gone(None, 1.0) is True
    child = FakeChild()
    assert launcher._wait_gone(child, 2.5) is True
    assert child.waits == [2.5]
    child.exits_within_wait = False
    assert launcher._wait_gone(child, 0.5) is False


# ---------------------------------------------------------------------------
# The browser process of a profile
# ---------------------------------------------------------------------------


def _fake_psutil(monkeypatch: Any, processes: list[Any] | None = None) -> Any:
    module = types.ModuleType("psutil")
    module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})  # type: ignore[attr-defined]
    module.AccessDenied = type("AccessDenied", (Exception,), {})  # type: ignore[attr-defined]
    module.asked_for = []  # type: ignore[attr-defined]

    def process_iter(attributes: list[str]) -> list[Any]:
        module.asked_for.append(attributes)  # type: ignore[attr-defined]
        return list(processes or [])

    module.process_iter = process_iter  # type: ignore[attr-defined]
    module.Process = _refuse  # type: ignore[attr-defined]
    module.wait_procs = _refuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", module)
    return module


def _listed(pid: int, *cmdline: str) -> Any:
    return SimpleNamespace(info={"pid": pid, "cmdline": list(cmdline)})


def test_the_browser_process_of_a_profile_is_the_one_without_a_type(tmp_path: Path, monkeypatch: Any) -> None:
    profile = str(tmp_path / "browser_profile")
    module = _fake_psutil(
        monkeypatch,
        [
            _listed(11, "chrome", "--type=renderer", f"--user-data-dir={profile}"),
            _listed(12, "chrome", "--type=gpu-process", f"--user-data-dir={profile}"),
            _listed(13, "python", "serve.py"),
            _listed(14, "chrome", f"--user-data-dir={tmp_path / 'another_profile'}", "about:blank"),
            _listed(15, "chrome", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"),
            _listed(16, "chrome", "--type=utility", f"--user-data-dir={profile}"),
        ],
    )
    assert launcher.browser_process_on(profile) == 15
    assert launcher.browser_process_on(Path(profile)) == 15
    assert module.asked_for[0] == ["pid", "cmdline"]


def test_no_browser_on_the_profile_gives_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    profile = str(tmp_path / "browser_profile")
    _fake_psutil(
        monkeypatch,
        [
            _listed(11, "chrome", "--type=renderer", f"--user-data-dir={profile}"),
            _listed(12, "chrome", f"--user-data-dir={profile}-other"),
            SimpleNamespace(info={"pid": 13, "cmdline": None}),
            SimpleNamespace(info={"pid": 14}),
        ],
    )
    assert launcher.browser_process_on(profile) is None
    _fake_psutil(monkeypatch, [])
    assert launcher.browser_process_on(profile) is None


def test_the_profile_on_a_command_line_may_be_quoted_and_written_another_way(tmp_path: Path, monkeypatch: Any) -> None:
    profile = tmp_path / "browser profile"
    quoted = f'--user-data-dir="{profile}"'
    _fake_psutil(monkeypatch, [_listed(21, "msedge", quoted)])
    assert launcher.browser_process_on(profile) == 21
    detour = os.path.join(str(tmp_path), "elsewhere", "..", "browser profile") + os.sep
    _fake_psutil(monkeypatch, [_listed(22, "msedge", f"--user-data-dir={detour}")])
    assert launcher.browser_process_on(profile) == 22
    if os.path.normcase("A") == "a":
        _fake_psutil(monkeypatch, [_listed(23, "msedge", f"--user-data-dir={str(profile).upper()}")])
        assert launcher.browser_process_on(profile) == 23


def test_without_psutil_no_browser_process_can_be_found(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert launcher.browser_process_on(tmp_path) is None


# ---------------------------------------------------------------------------
# Killing a process tree
# ---------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, pid: int, log: list[Any], children: list["FakeProcess"] | None = None, kill_error: Exception | None = None) -> None:
        self.pid = pid
        self._log = log
        self._children = children or []
        self._kill_error = kill_error

    def children(self, recursive: bool = False) -> list["FakeProcess"]:
        self._log.append(("children", self.pid, recursive))
        return list(self._children)

    def kill(self) -> None:
        self._log.append(("kill", self.pid))
        if self._kill_error is not None:
            raise self._kill_error


def _process_table(monkeypatch: Any, processes: dict[int, FakeProcess], log: list[Any]) -> Any:
    module = _fake_psutil(monkeypatch)

    def process(pid: int) -> FakeProcess:
        if pid not in processes:
            raise module.NoSuchProcess(pid)
        return processes[pid]

    def wait_procs(waited: list[FakeProcess], timeout: float | None = None) -> tuple[list[Any], list[Any]]:
        log.append(("wait", [item.pid for item in waited], timeout))
        return waited, []

    module.Process = process
    module.wait_procs = wait_procs
    return module


def test_no_pid_means_nothing_to_kill() -> None:
    # The refusing ``psutil`` and ``subprocess`` of this file are in place: neither is reached.
    launcher.kill_tree(None)


def test_the_children_are_killed_and_then_the_parent_and_all_are_waited_for(monkeypatch: Any) -> None:
    log: list[Any] = []
    renderer, gpu = FakeProcess(31, log), FakeProcess(32, log)
    parent = FakeProcess(30, log, [renderer, gpu])
    _process_table(monkeypatch, {30: parent}, log)
    launcher.kill_tree(30)
    assert log == [("children", 30, True), ("kill", 31), ("kill", 32), ("kill", 30), ("wait", [31, 32, 30], 3.0)]


def test_a_process_that_is_already_gone_is_not_an_error_and_the_system_tool_is_not_called(monkeypatch: Any) -> None:
    log: list[Any] = []
    _process_table(monkeypatch, {}, log)
    launcher.kill_tree(30)
    assert log == []


def test_a_part_of_the_tree_that_is_gone_or_protected_does_not_stop_the_rest(monkeypatch: Any) -> None:
    log: list[Any] = []
    module = _process_table(monkeypatch, {}, log)
    gone = FakeProcess(31, log, kill_error=module.NoSuchProcess(31))
    protected = FakeProcess(32, log, kill_error=module.AccessDenied(32))
    last = FakeProcess(33, log)
    parent = FakeProcess(30, log, [gone, protected, last])
    module.Process = lambda pid: parent
    launcher.kill_tree(30)
    assert [entry for entry in log if entry[0] == "kill"] == [("kill", 31), ("kill", 32), ("kill", 33), ("kill", 30)]


def test_without_psutil_windows_kills_the_tree_with_taskkill(monkeypatch: Any) -> None:
    runs: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(launcher, "subprocess", _subprocess(run=lambda command, **options: runs.append((command, options))))
    launcher.kill_tree(4321)
    assert runs == [(["taskkill", "/PID", "4321", "/T", "/F"], {"capture_output": True, "timeout": 10, "check": False})]


def test_when_psutil_fails_the_system_tool_is_tried(monkeypatch: Any) -> None:
    runs: list[list[str]] = []
    module = _fake_psutil(monkeypatch)

    def broken(pid: int) -> Any:
        raise RuntimeError("psutil lost its mind")

    module.Process = broken
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(launcher, "subprocess", _subprocess(run=lambda command, **options: runs.append(command)))
    launcher.kill_tree(4321)
    assert runs == [["taskkill", "/PID", "4321", "/T", "/F"]]


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("taskkill", 10), FileNotFoundError("taskkill")])
def test_a_system_tool_that_fails_is_not_an_error(monkeypatch: Any, error: Exception) -> None:
    def run(command: list[str], **options: Any) -> None:
        raise error

    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(launcher, "subprocess", _subprocess(run=run))
    launcher.kill_tree(4321)


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs the kill signal of a POSIX system")
def test_without_psutil_other_systems_kill_the_process_group_or_else_the_process(monkeypatch: Any) -> None:
    log: list[Any] = []
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    group_error: list[Exception] = []

    def killpg(group: int, sent: int) -> None:
        log.append(("killpg", group, sent))
        if group_error:
            raise group_error[0]

    fake_os = _ScopedModule(
        os,
        getpgid=lambda pid: pid + 1000,
        killpg=killpg,
        kill=lambda pid, sent: log.append(("kill", pid, sent)),
    )
    monkeypatch.setattr(launcher, "os", fake_os)
    launcher.kill_tree(4321)
    assert log == [("killpg", 5321, signal.SIGKILL)]
    log.clear()
    group_error.append(ProcessLookupError())
    launcher.kill_tree(4321)
    assert log == [("killpg", 5321, signal.SIGKILL), ("kill", 4321, signal.SIGKILL)]


# ---------------------------------------------------------------------------
# A start that is told to stop
# ---------------------------------------------------------------------------


def test_a_start_that_is_told_to_stop_gives_up_at_once_and_takes_what_it_started_with_it(starter: Starter) -> None:
    import threading

    stop = threading.Event()
    # Whoever asked for the browser goes away just as it has been started.
    starter.on_start.append(stop.set)
    starter.process_on_profile = 8765
    with pytest.raises(BrowserStartError, match="Google Chrome was not started: the start was stopped"):
        launcher._start(CHROME, str(starter.profile), False, (), stop)
    assert len(starter.started) == 1
    # The child is killed, and whatever runs on the profile as well: stopped at any moment of
    # the coming up, the child may just have handed over. None of the wait a browser is given.
    assert starter.killed == [4321, 8765]
    assert starter.clock.sleeps == []


def test_a_start_told_to_stop_while_it_waits_gives_up_then(starter: Starter) -> None:
    import threading

    stop = threading.Event()
    # The child has handed over and gone; the stop comes after a few looks for the port.
    starter.on_start.append(lambda: setattr(starter.child, "returncode", 0))
    starter.process_on_profile = 8765
    starter.clock.on_sleep.append(lambda slept: stop.set() if slept == 3 else None)
    with pytest.raises(BrowserStartError, match="the start was stopped"):
        launcher._start(CHROME, str(starter.profile), False, (), stop)
    assert len(starter.clock.sleeps) == 3
    # The child is gone already: only what runs on the profile is killed.
    assert starter.killed == [8765]


def test_a_start_that_is_stopped_before_it_began_starts_nothing(starter: Starter) -> None:
    import threading

    stop = threading.Event()
    stop.set()
    with pytest.raises(BrowserStartError, match="the start was stopped"):
        launcher._start(CHROME, str(starter.profile), False, (), stop)
    assert starter.started == [] and starter.killed == []


def test_a_start_that_is_never_told_to_stop_goes_on_as_before(starter: Starter) -> None:
    import threading

    starter.on_start.append(lambda: starter.browser_listens(9555))
    browser = launcher._start(CHROME, str(starter.profile), False, (), threading.Event())
    assert browser.endpoint == "ws://127.0.0.1:9555/devtools/browser/new" and starter.killed == []


# ---------------------------------------------------------------------------
# Killing a browser outright
# ---------------------------------------------------------------------------


def test_killing_takes_our_child_by_its_number_and_a_browser_that_is_not_our_child_by_its_profile(
    monkeypatch: Any,
) -> None:
    from types import SimpleNamespace

    killed: list[Any] = []
    looked_up: list[Any] = []

    def on_profile(profile: Any) -> int:
        looked_up.append(profile)
        return 8765

    monkeypatch.setattr(launcher, "kill_tree", killed.append)
    monkeypatch.setattr(launcher, "browser_process_on", on_profile)
    child = RunningBrowser(install=CHROME, endpoint="ws://x", process=SimpleNamespace(pid=4321, poll=lambda: None), owned=True, user_data_dir="/profile")
    launcher.kill(child)
    assert killed == [4321] and looked_up == []

    handed_over = RunningBrowser(install=CHROME, endpoint="ws://x", process=None, owned=True, user_data_dir="/profile")
    launcher.kill(handed_over)
    assert killed == [4321, 8765] and looked_up == ["/profile"]

    # Neither a child nor a profile to find it by: nothing is found, nothing is killed.
    launcher.kill(RunningBrowser(install=None, endpoint="ws://x", process=None, owned=True))
    assert killed == [4321, 8765, None] and looked_up == ["/profile"]


def test_a_browser_that_was_attached_to_is_never_killed(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    killed: list[Any] = []
    monkeypatch.setattr(launcher, "kill_tree", killed.append)
    monkeypatch.setattr(launcher, "browser_process_on", lambda profile: 8765)
    theirs = RunningBrowser(install=CHROME, endpoint="ws://x", process=SimpleNamespace(pid=4321, poll=lambda: None), owned=False, user_data_dir="/theirs")
    launcher.kill(theirs)
    assert killed == []


def test_a_port_file_whose_port_went_to_another_program_is_not_taken_for_a_running_browser(starter: Starter) -> None:
    # The browser was killed and left its port file; the port answers, but it is another program's now.
    starter.browser_listens(9555, "/devtools/browser/killed")
    assert starter.process_on_profile is None
    seen_at_start: list[bool] = []
    starter.on_start.append(lambda: seen_at_start.append((starter.profile / ACTIVE_PORT_FILE).exists()))
    starter.clock.on_sleep.append(lambda count: starter.browser_listens(9666) if count == 2 else None)

    browser = launcher._start(CHROME, str(starter.profile), False, ())

    # Taken for the browser, it would fail this start and every one after it.
    assert len(starter.started) == 1 and seen_at_start == [False]
    assert browser.endpoint == "ws://127.0.0.1:9666/devtools/browser/new" and browser.process is starter.child


def test_a_browser_is_not_taken_for_gone_when_one_that_cannot_be_looked_at_may_be_it(starter: Starter) -> None:
    starter.browser_listens(9555, "/devtools/browser/left-running")
    # No process shows this profile on its command line, but one browser does not show its command line at all.
    assert starter.process_on_profile is None
    starter.unreadable_browser = True

    browser = launcher._start(CHROME, str(starter.profile), False, ())

    # Starting another on its profile would only open a window in it, and its port file would be gone.
    assert starter.started == [] and browser.endpoint == "ws://127.0.0.1:9555/devtools/browser/left-running"
    assert (starter.profile / ACTIVE_PORT_FILE).exists()


def test_a_profile_directory_that_cannot_be_made_is_a_start_that_failed(starter: Starter, monkeypatch: Any) -> None:
    def refuse(path: Any, exist_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(launcher.os, "makedirs", refuse)

    # Any other error would leave the tool call without an answer the model can read.
    with pytest.raises(BrowserStartError, match="Google Chrome could not be started: its profile directory cannot be made"):
        launcher._start(CHROME, str(starter.profile), False, ())
    assert starter.started == []


def test_a_start_told_to_stop_takes_along_whoever_the_started_process_handed_over_to(starter: Starter) -> None:
    import threading

    stop = threading.Event()
    # The process that was started has handed over to another one (as Edge does) and has not exited yet.
    starter.process_on_profile = 7002
    starter.clock.on_sleep.append(lambda count: stop.set() if count == 2 else None)

    with pytest.raises(BrowserStartError, match="was not started: the start was stopped"):
        launcher._start(CHROME, str(starter.profile), False, (), stop)

    # The one it handed over to is not among what it started: left alone, it would run on with nobody to close it.
    assert starter.killed == [4321, 7002]


def test_a_start_that_ran_out_of_time_does_not_look_at_every_process_while_the_started_one_runs(starter: Starter) -> None:
    starter.process_on_profile = 7002

    with pytest.raises(BrowserStartError):
        launcher._start(CHROME, str(starter.profile), False, ())

    # Long past the moment of handing over: what was started is the browser.
    assert starter.killed == [4321]


def _named(name: str, *cmdline: str, unreadable: bool = False) -> Any:
    return SimpleNamespace(info={"name": name, "cmdline": None if unreadable else list(cmdline)})


def test_only_a_browser_that_keeps_its_command_line_to_itself_leaves_it_open_whether_one_runs(monkeypatch: Any) -> None:
    module = _fake_psutil(
        monkeypatch,
        [
            _named("chrome.exe", "--user-data-dir=C:/somewhere"),
            # Programs of the system never show theirs; they are no browsers.
            _named("System", unreadable=True),
            _named("csrss.exe", unreadable=True),
            _named("chrome_proxy.exe", unreadable=True),
            SimpleNamespace(info={}),
        ],
    )
    assert launcher._a_browser_cannot_be_looked_at() is False
    assert module.asked_for == [["name", "cmdline"]]

    for name in ("chrome.exe", "msedge.exe", "Google Chrome", "chromium", "MSEDGE.EXE"):
        _fake_psutil(monkeypatch, [_named("python.exe", "serve.py"), _named(name, unreadable=True)])
        assert launcher._a_browser_cannot_be_looked_at() is True, name


def test_without_psutil_it_cannot_be_told_whether_a_browser_runs(monkeypatch: Any) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert launcher._a_browser_cannot_be_looked_at() is True


def test_a_port_file_counts_as_left_behind_only_when_no_browser_can_be_on_the_profile(tmp_path: Path, monkeypatch: Any) -> None:
    profile = str(tmp_path / "browser_profile")
    # A browser runs on the profile.
    _fake_psutil(monkeypatch, [SimpleNamespace(info={"pid": 15, "name": "chrome.exe", "cmdline": [f"--user-data-dir={profile}"]})])
    assert launcher._left_by_a_browser_that_is_gone(profile) is False
    # None does, and every browser that runs shows where it runs.
    _fake_psutil(
        monkeypatch,
        [SimpleNamespace(info={"pid": 16, "name": "chrome.exe", "cmdline": [f"--user-data-dir={profile}-of-another"]})],
    )
    assert launcher._left_by_a_browser_that_is_gone(profile) is True
    # None is seen to, but one browser does not show where it runs: it may be this profile's.
    _fake_psutil(monkeypatch, [SimpleNamespace(info={"pid": 17, "name": "chrome.exe", "cmdline": None})])
    assert launcher._left_by_a_browser_that_is_gone(profile) is False
    # The processes cannot be listed at all.
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert launcher._left_by_a_browser_that_is_gone(profile) is False


def test_the_number_of_a_child_that_has_exited_is_not_used_to_find_the_browser(monkeypatch: Any, tmp_path: Path) -> None:
    profile = str(tmp_path / "browser_profile")
    looked_up: list[Any] = []

    def on_profile(directory: Any) -> int | None:
        looked_up.append(directory)
        return 7003

    monkeypatch.setattr(launcher, "browser_process_on", on_profile)
    child = FakeChild()
    browser = RunningBrowser(install=CHROME, endpoint="ws://127.0.0.1:9222/devtools/browser/x", process=child, owned=True, user_data_dir=profile)

    # While the child runs, it is the browser.
    assert launcher._pid_of(browser) == 4321 and looked_up == []

    # It has exited (the user closed the browser, or it handed over): its number may be another program's by now.
    child.returncode = 0
    assert launcher._pid_of(browser) == 7003 and looked_up == [profile]

    # With no profile to look on, nothing is found rather than the old number.
    nowhere = RunningBrowser(install=CHROME, endpoint="ws://127.0.0.1:9222/devtools/browser/x", process=child, owned=True, user_data_dir=None)
    assert launcher._pid_of(nowhere) is None


def test_killing_a_browser_whose_child_has_exited_does_not_kill_by_the_old_number(monkeypatch: Any, tmp_path: Path) -> None:
    killed: list[Any] = []
    monkeypatch.setattr(launcher, "kill_tree", killed.append)
    monkeypatch.setattr(launcher, "browser_process_on", lambda directory: None)
    child = FakeChild()
    child.returncode = 0
    browser = RunningBrowser(
        install=CHROME, endpoint="ws://127.0.0.1:9222/devtools/browser/x", process=child, owned=True, user_data_dir=str(tmp_path)
    )

    launcher.kill(browser)

    # Nothing runs on the profile any more: nothing is killed, least of all whoever has number 4321 now.
    assert killed == [None]


def test_a_number_that_went_to_another_process_is_not_taken_for_the_child(monkeypatch: Any, tmp_path: Path) -> None:
    profile = str(tmp_path / "browser_profile")
    monkeypatch.setattr(launcher, "browser_process_on", lambda directory: 7004)
    created = {4321: 500.0}
    monkeypatch.setattr(launcher, "_created", created.get)
    # ``poll`` still says it runs (nobody has collected its exit, or another thread is waiting for it).
    child = FakeChild()
    browser = RunningBrowser(
        install=CHROME, endpoint="ws://127.0.0.1:9222/devtools/browser/x", process=child, owned=True, user_data_dir=profile, started=500.0
    )
    assert launcher._pid_of(browser) == 4321

    # The number is another program's now: it was created at another time.
    created[4321] = 912.5
    assert launcher._pid_of(browser) == 7004
    # Or nobody's.
    del created[4321]
    assert launcher._pid_of(browser) == 7004

    # When it was not known at the start when the child was created, the number is taken at its word.
    unknown = RunningBrowser(
        install=CHROME, endpoint="ws://127.0.0.1:9222/devtools/browser/x", process=child, owned=True, user_data_dir=profile
    )
    assert launcher._pid_of(unknown) == 4321


def test_a_browser_that_is_started_remembers_when_its_process_was_created(starter: Starter) -> None:
    starter.on_start.append(lambda: starter.browser_listens(9666))
    browser = launcher._start(CHROME, str(starter.profile), False, ())
    assert browser.process is starter.child and browser.started == 1000.0 + 4321


def test_a_browser_whose_child_handed_over_at_once_has_no_time_of_creation_to_go_by(starter: Starter) -> None:
    def hand_over() -> None:
        starter.child.returncode = 0
        starter.browser_listens(9666)

    starter.on_start.append(hand_over)
    browser = launcher._start(CHROME, str(starter.profile), False, ())
    # Not our child: looked up by its profile whenever its process is wanted.
    assert browser.process is None and browser.started is None


def test_when_a_process_was_created_cannot_always_be_told(monkeypatch: Any) -> None:
    class Gone(Exception):
        pass

    module = types.ModuleType("psutil")

    def process(pid: int) -> Any:
        if pid == 1:
            raise Gone("no such process")
        return SimpleNamespace(create_time=lambda: 1234.5)

    module.Process = process  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", module)
    assert launcher._created(77) == 1234.5
    assert launcher._created(1) is None
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert launcher._created(77) is None


async def test_a_child_that_goes_while_the_browser_cannot_be_asked_is_not_killed_by_its_number(closer: Closer) -> None:
    child = FakeChild(pid=5150)

    class Refusing(FakeConnection):
        async def send(self, method: str, params: Any = None, **options: Any) -> Any:
            # While the browser is being asked, the child hands over and goes.
            child.returncode = 0
            raise RuntimeError("the connection broke")

    await launcher.close(_ours(child), Refusing())

    # Which process is the browser is asked when it is to be killed: by then it is the one on the profile.
    assert closer.looked_up == ["/data/profile"]
    assert closer.killed == [7007]
