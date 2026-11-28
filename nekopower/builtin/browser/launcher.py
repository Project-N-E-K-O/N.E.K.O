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

"""Finding a browser on this computer, starting it, and getting rid of it again.

No browser comes with the application (design section 11.2). The one that is
driven is a Chromium-based browser the user has installed: Chrome, Edge,
Brave, Chromium in that order, and on Windows the one set as the default
browser first when it is one of them. Two ways to use it:

* **its own profile** (the default): the browser is started with
  ``--remote-debugging-port=0`` and a profile directory below the data root.
  Since Chrome 136 remote debugging is not switched on for the default
  profile, so the user's everyday logins are not there; a site logged into
  once in this profile stays logged in. The browser writes the port it chose
  to ``DevToolsActivePort`` in that directory, which is where it is read.
* **the browser in use** ("attach"): Chrome 144 and later let the user switch
  remote debugging on for the running browser at
  ``chrome://inspect/#remote-debugging``. It then writes the same file into
  its own profile directory, and every connection has to be allowed in a
  prompt of the browser. Nothing is started and nothing is closed.

A browser that this process started is closed with the protocol's
``Browser.close`` first and, if it is still there after a moment, by killing
its process tree. A browser that was only attached to is left alone.

Everything here that touches the file system or processes blocks; the
``async`` functions hand it to worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACTIVE_PORT_FILE = "DevToolsActivePort"
LAUNCH_TIMEOUT_SECONDS = 25.0
PIPE_PROBE_TIMEOUT_SECONDS = 8.0
CLOSE_WAIT_SECONDS = 5.0
# Overrides the search; for a browser installed somewhere unusual.
EXECUTABLE_ENV = "NEKO_BROWSER_PATH"


class NoBrowserError(Exception):
    """No usable browser was found on this computer."""


class BrowserStartError(Exception):
    """A browser was found but could not be started or reached."""


@dataclass(frozen=True, slots=True)
class BrowserInstall:
    """One browser found on this computer."""

    # "chrome", "edge", "brave", "chromium" or "custom".
    kind: str
    name: str
    executable: str
    # The profile directory the browser uses for everyday browsing, where it
    # writes ``DevToolsActivePort`` once the user switched remote debugging on.
    user_data_dir: str | None = None


# ---------------------------------------------------------------------------
# Finding a browser
# ---------------------------------------------------------------------------


def _windows_candidates() -> list[BrowserInstall]:
    roots = [os.environ.get(name) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    local = os.environ.get("LOCALAPPDATA") or ""
    layouts = (
        ("chrome", "Google Chrome", ("Google", "Chrome", "Application", "chrome.exe"), ("Google", "Chrome", "User Data")),
        ("edge", "Microsoft Edge", ("Microsoft", "Edge", "Application", "msedge.exe"), ("Microsoft", "Edge", "User Data")),
        (
            "brave",
            "Brave",
            ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            ("BraveSoftware", "Brave-Browser", "User Data"),
        ),
        ("chromium", "Chromium", ("Chromium", "Application", "chrome.exe"), ("Chromium", "User Data")),
    )
    found = []
    for kind, name, relative, profile in layouts:
        for root in roots:
            if not root:
                continue
            path = os.path.join(root, *relative)
            if os.path.isfile(path):
                found.append(BrowserInstall(kind, name, path, os.path.join(local, *profile) if local else None))
                break
    return found


def _windows_default_kind() -> str | None:
    """Return the kind of the browser Windows opens links with, when it is one we can drive."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0])
    except Exception:  # noqa: BLE001 - no default known; the fixed order is used
        return None
    lowered = prog_id.lower()
    for marker, kind in (("chromehtml", "chrome"), ("msedgehtm", "edge"), ("bravehtml", "brave"), ("chromiumhtm", "chromium")):
        if lowered.startswith(marker):
            return kind
    return None


def _mac_candidates() -> list[BrowserInstall]:
    home = os.path.expanduser("~")
    support = os.path.join(home, "Library", "Application Support")
    layouts = (
        ("chrome", "Google Chrome", "Google Chrome.app/Contents/MacOS/Google Chrome", "Google/Chrome"),
        ("edge", "Microsoft Edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "Microsoft Edge"),
        ("brave", "Brave", "Brave Browser.app/Contents/MacOS/Brave Browser", "BraveSoftware/Brave-Browser"),
        ("chromium", "Chromium", "Chromium.app/Contents/MacOS/Chromium", "Chromium"),
    )
    found = []
    for kind, name, relative, profile in layouts:
        for root in ("/Applications", os.path.join(home, "Applications")):
            path = os.path.join(root, relative)
            if os.path.isfile(path):
                found.append(BrowserInstall(kind, name, path, os.path.join(support, profile)))
                break
    return found


def _linux_candidates() -> list[BrowserInstall]:
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    layouts = (
        ("chrome", "Google Chrome", ("google-chrome", "google-chrome-stable"), "google-chrome"),
        ("edge", "Microsoft Edge", ("microsoft-edge", "microsoft-edge-stable"), "microsoft-edge"),
        ("brave", "Brave", ("brave-browser", "brave"), "BraveSoftware/Brave-Browser"),
        ("chromium", "Chromium", ("chromium", "chromium-browser"), "chromium"),
    )
    found = []
    for kind, name, commands, profile in layouts:
        for command in commands:
            path = shutil.which(command)
            if path:
                found.append(BrowserInstall(kind, name, path, os.path.join(config, profile)))
                break
    return found


# The file names of the browsers that can be driven, on any platform.
# The file names of Chrome, Edge, Brave and Chromium, every release channel
# of them: on Windows all channels share one name, on macOS and Linux the
# channel is part of it.
_BROWSER_FILE_NAME = re.compile(
    "|".join(
        [
            r"(?:chrome|msedge|brave|chromium)\.exe",
            r"(?:google chrome|microsoft edge|brave browser)(?: (?:beta|dev|canary|nightly|for testing))?",
            r"(?:google-chrome|microsoft-edge|brave-browser)(?:-(?:stable|beta|dev|unstable|canary|nightly))?",
            r"chrome|msedge|brave|chromium|chromium-browser",
        ]
    ),
    re.IGNORECASE,
)


def is_browser_executable(path: str | os.PathLike[str]) -> bool:
    """Whether a file is, going by its name, one of the browsers the tools can drive.

    A path that comes from the settings is started with a debugging port and
    a profile directory: it must not be any program at all.
    """
    return _BROWSER_FILE_NAME.fullmatch(os.path.basename(os.fspath(path))) is not None


def find_browsers(platform: str | None = None) -> list[BrowserInstall]:
    """Return the browsers that can be driven, the preferred one first. Blocking (file checks only)."""
    platform = platform or sys.platform
    override = (os.environ.get(EXECUTABLE_ENV) or "").strip()
    found: list[BrowserInstall] = []
    if override and os.path.isfile(override):
        found.append(BrowserInstall("custom", os.path.basename(override), override))
    if platform == "win32":
        installs = _windows_candidates()
        default = _windows_default_kind()
        if default:
            installs.sort(key=lambda item: item.kind != default)
    elif platform == "darwin":
        installs = _mac_candidates()
    else:
        installs = _linux_candidates()
    found.extend(installs)
    return found


def pick_browser(executable: str | None = None, platform: str | None = None) -> BrowserInstall:
    """Return the browser to use; ``executable`` (from the settings) wins when it exists."""
    installs = find_browsers(platform)
    if executable:
        wanted = os.path.normcase(os.path.abspath(executable))
        for install in installs:
            if os.path.normcase(os.path.abspath(install.executable)) == wanted:
                return install
        if os.path.isfile(executable) and is_browser_executable(executable):
            return BrowserInstall("custom", os.path.basename(executable), executable)
    if not installs:
        raise NoBrowserError(
            "no Chromium-based browser (Chrome, Edge, Brave, Chromium) was found on this computer"
        )
    return installs[0]


# ---------------------------------------------------------------------------
# The debugging endpoint
# ---------------------------------------------------------------------------


def read_active_port(user_data_dir: str | os.PathLike[str]) -> tuple[int, str] | None:
    """Return ``(port, browser path)`` from a profile's ``DevToolsActivePort``, or None."""
    try:
        lines = Path(user_data_dir, ACTIVE_PORT_FILE).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    if len(lines) < 2 or not lines[0].strip().isdigit():
        return None
    path = lines[1].strip()
    if not path.startswith("/devtools/browser/"):
        return None
    return int(lines[0].strip()), path


def display_available(platform: str | None = None) -> bool:
    """Return whether a browser window can be shown here.

    False on Linux without a display server (a container, a server over
    SSH): a browser started with a window exits at once there, so it is
    started without one whatever the settings say.
    """
    platform = platform or sys.platform
    if platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def endpoint_url(port: int, path: str) -> str:
    return f"ws://127.0.0.1:{port}{path}"


def _port_open(port: int, timeout: float = 0.5) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _left_by_a_browser_that_is_gone(user_data_dir: str | os.PathLike[str]) -> bool:
    """Return whether the port file of a profile of ours names a port no browser of that profile listens on. Blocking.

    A browser that was killed leaves its port file behind, and the port may
    since have gone to another program: that the port answers does not show
    the browser to be there. Taken for running, such a file would fail every
    start for as long as the other program runs.

    False whenever it cannot be told: a browser that does run must never be
    taken for gone, or its port file is removed and another browser is
    started on its profile, which only opens a window in the first and exits.

    The price of that caution: on a computer where some browser keeps its
    command line from being read (another user's, or one run with higher
    rights), nothing is ever told, for any profile, and a port file whose
    port went to another program fails the start again until that program
    is gone. A file wrongly removed would be the worse of the two.
    """
    if browser_process_on(user_data_dir) is not None:
        return False
    # None was found on the profile. One that was passed over because its
    # command line cannot be read may still be the one.
    return not _a_browser_cannot_be_looked_at()


def _a_browser_cannot_be_looked_at() -> bool:
    """Return whether some process that is a browser by its name keeps its command line from being read. Blocking.

    ``browser_process_on`` tells the browser of a profile by its command
    line and passes such a process over (one started with other rights than
    this process has, say). True as well when the processes cannot be listed
    at all.
    """
    try:
        import psutil
    except ImportError:
        return True
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            info = process.info
            unreadable = info.get("cmdline") is None
            name = str(info.get("name") or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if unreadable and _BROWSER_FILE_NAME.fullmatch(name):
            return True
    return False


def running_endpoint(user_data_dir: str | os.PathLike[str]) -> str | None:
    """Return the debugging endpoint of a browser already running on this profile, or None. Blocking."""
    active = read_active_port(user_data_dir)
    if active is None or not _port_open(active[0]):
        return None
    return endpoint_url(*active)


# ---------------------------------------------------------------------------
# Profile inference and running browser discovery (for attach mode)
# ---------------------------------------------------------------------------


def _infer_profile_dirs(executable: str, platform: str | None = None) -> list[str]:
    """Infer everyday user-data directories for a browser executable at a custom path."""
    platform = platform or sys.platform
    raw = os.fspath(executable)
    lowered = raw.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    found: list[str] = []

    def add(path: str | None) -> None:
        if path and path not in found:
            found.append(path)

    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or ""
        if not local:
            return []
        if base == "chrome.exe":
            if "chrome sxs" in lowered or "canary" in lowered:
                add(os.path.join(local, "Google", "Chrome SxS", "User Data"))
            elif "chrome beta" in lowered or "/beta/" in lowered:
                add(os.path.join(local, "Google", "Chrome Beta", "User Data"))
            elif "chrome dev" in lowered or "/dev/" in lowered:
                add(os.path.join(local, "Google", "Chrome Dev", "User Data"))
            elif "chromium" in lowered:
                add(os.path.join(local, "Chromium", "User Data"))
            else:
                add(os.path.join(local, "Google", "Chrome", "User Data"))
                add(os.path.join(local, "Chromium", "User Data"))
        elif base == "msedge.exe":
            if "edge sxs" in lowered or "canary" in lowered:
                add(os.path.join(local, "Microsoft", "Edge SxS", "User Data"))
            elif "edge beta" in lowered:
                add(os.path.join(local, "Microsoft", "Edge Beta", "User Data"))
            elif "edge dev" in lowered:
                add(os.path.join(local, "Microsoft", "Edge Dev", "User Data"))
            else:
                add(os.path.join(local, "Microsoft", "Edge", "User Data"))
        elif base == "brave.exe":
            if "nightly" in lowered:
                add(os.path.join(local, "BraveSoftware", "Brave-Browser-Nightly", "User Data"))
            elif "beta" in lowered:
                add(os.path.join(local, "BraveSoftware", "Brave-Browser-Beta", "User Data"))
            else:
                add(os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"))
        elif base == "chromium.exe":
            add(os.path.join(local, "Chromium", "User Data"))
    elif platform == "darwin":
        support = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        if "google chrome canary" in base or "chrome canary" in lowered:
            add(os.path.join(support, "Google/Chrome Canary"))
        elif "google chrome beta" in base:
            add(os.path.join(support, "Google/Chrome Beta"))
        elif "google chrome dev" in base:
            add(os.path.join(support, "Google/Chrome Dev"))
        elif base == "google chrome" or base == "chrome":
            add(os.path.join(support, "Google/Chrome"))
        elif "microsoft edge canary" in base:
            add(os.path.join(support, "Microsoft Edge Canary"))
        elif "microsoft edge beta" in base:
            add(os.path.join(support, "Microsoft Edge Beta"))
        elif "microsoft edge dev" in base:
            add(os.path.join(support, "Microsoft Edge Dev"))
        elif base == "microsoft edge" or base == "msedge":
            add(os.path.join(support, "Microsoft Edge"))
        elif "brave browser nightly" in base:
            add(os.path.join(support, "BraveSoftware/Brave-Browser-Nightly"))
        elif "brave browser beta" in base:
            add(os.path.join(support, "BraveSoftware/Brave-Browser-Beta"))
        elif base in ("brave browser", "brave"):
            add(os.path.join(support, "BraveSoftware/Brave-Browser"))
        elif base in ("chromium", "chromium-browser"):
            add(os.path.join(support, "Chromium"))
    else:
        config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        if base in ("google-chrome", "google-chrome-stable", "chrome"):
            add(os.path.join(config, "google-chrome"))
        elif base == "google-chrome-beta":
            add(os.path.join(config, "google-chrome-beta"))
        elif base in ("google-chrome-unstable", "google-chrome-dev"):
            add(os.path.join(config, "google-chrome-unstable"))
        elif base == "google-chrome-canary":
            add(os.path.join(config, "google-chrome-canary"))
        elif base in ("microsoft-edge", "microsoft-edge-stable", "msedge"):
            add(os.path.join(config, "microsoft-edge"))
        elif base == "microsoft-edge-beta":
            add(os.path.join(config, "microsoft-edge-beta"))
        elif base == "microsoft-edge-dev":
            add(os.path.join(config, "microsoft-edge-dev"))
        elif base in ("brave-browser", "brave"):
            add(os.path.join(config, "BraveSoftware/Brave-Browser"))
        elif base == "brave-browser-beta":
            add(os.path.join(config, "BraveSoftware/Brave-Browser-Beta"))
        elif base == "brave-browser-nightly":
            add(os.path.join(config, "BraveSoftware/Brave-Browser-Nightly"))
        elif base in ("chromium", "chromium-browser"):
            add(os.path.join(config, "chromium"))
    return found


def _extract_user_data_dir(arguments: list[str]) -> str | None:
    """Return the ``--user-data-dir`` value from a browser command line, or None."""
    for index, argument in enumerate(arguments):
        if argument.startswith("--user-data-dir="):
            named = argument[len("--user-data-dir=") :].strip('"')
            if named:
                return named
        elif argument == "--user-data-dir" and index + 1 < len(arguments):
            named = arguments[index + 1].strip('"')
            if named:
                return named
    return None


def _running_browser_profiles(
    executable: str | None = None, *, exclude_user_data_dir: str | None = None
) -> list[str]:
    """Return ``--user-data-dir`` paths of live browser main processes matching ``executable``. Blocking."""
    try:
        import psutil
    except ImportError:
        return []
    wanted_path = _profile_key(executable) if executable else None
    wanted_base = os.path.normcase(os.path.basename(executable)) if executable else None
    excluded = _profile_key(exclude_user_data_dir) if exclude_user_data_dir else None
    profiles: list[str] = []
    seen: set[str] = set()
    for process in psutil.process_iter(["exe", "name", "cmdline"]):
        try:
            info = process.info
            arguments = list(info.get("cmdline") or [])
            exe = info.get("exe")
            name = str(info.get("name") or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not arguments or any(arg.startswith("--type=") for arg in arguments):
            continue
        if wanted_path is not None:
            matched = False
            if exe and _profile_key(str(exe)) == wanted_path:
                matched = True
            elif arguments and _profile_key(arguments[0]) == wanted_path:
                matched = True
            elif not exe and wanted_base and os.path.normcase(name) == wanted_base:
                matched = True
            if not matched:
                continue
        elif not _BROWSER_FILE_NAME.fullmatch(name):
            continue
        profile = _extract_user_data_dir(arguments)
        if not profile:
            continue
        key = _profile_key(profile)
        if excluded is not None and key == excluded:
            continue
        if key not in seen:
            seen.add(key)
            profiles.append(profile)
    return profiles


def attach_candidates(
    primary: BrowserInstall, *, executable: str | None = None, platform: str | None = None
) -> list[BrowserInstall]:
    """Return browsers to try in attach mode, preferred first.

    When the user explicitly configured a browser executable (via settings or
    ``NEKO_BROWSER_PATH``), only that browser is tried so attach does not
    silently connect to a different browser.
    """
    override = (os.environ.get(EXECUTABLE_ENV) or "").strip()
    if executable or (override and os.path.isfile(override)):
        return [primary]
    candidates = [primary]
    seen = {_profile_key(primary.executable)}
    for install in find_browsers(platform):
        key = _profile_key(install.executable)
        if key not in seen:
            seen.add(key)
            candidates.append(install)
    return candidates


# ---------------------------------------------------------------------------
# Starting and closing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunningBrowser:
    """A browser being driven."""

    install: BrowserInstall | None
    endpoint: str
    # The process this module started; None for a browser that was already running.
    process: Any = None
    # True when it is ours to close: started here, on the profile kept for it.
    owned: bool = False
    user_data_dir: str | None = None
    # When ``process`` was created, as the system tells it; None when that
    # could not be asked. A number alone does not name a process for good.
    started: float | None = None
    # Whether it runs without a window. Known for one this module started;
    # None for one that was already running (found on its profile, or the
    # user's own), until whoever drives it has asked it.
    headless: bool | None = None
    # Open ``PipeTransport`` when started with ``--remote-debugging-pipe``; None for WebSocket.
    pipe: Any = None

    @property
    def pid(self) -> int | None:
        return getattr(self.process, "pid", None)


def launch_arguments(
    executable: str,
    user_data_dir: str,
    *,
    headless: bool = False,
    extra: tuple[str, ...] = (),
    pipe: bool = False,
) -> list[str]:
    arguments = [
        executable,
        "--remote-debugging-pipe" if pipe else "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Not a sandbox: keeps a browser nobody is looking at from being
        # slowed down, which makes pages hang half loaded.
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    if headless:
        arguments.append("--headless=new")
    arguments.extend(extra)
    arguments.append("about:blank")
    return arguments


def _can_use_pipe() -> bool:
    """Whether ``--remote-debugging-pipe`` launch is enabled on this platform."""
    return True


class _Win32Process:
    """Minimal ``Popen``-like wrapper around a Win32 process handle from ``CreateProcessW``."""

    _STILL_ACTIVE = 259
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _INFINITE = 0xFFFFFFFF

    def __init__(self, handle: int, pid: int) -> None:
        self._handle: int | None = int(handle)
        self.pid = int(pid)
        self.returncode: int | None = None

    def _close_handle(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                import ctypes
                import ctypes.wintypes as wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                kernel32.CloseHandle(handle)
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        handle = self._handle
        if handle is None:
            return self.returncode
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        if code.value == self._STILL_ACTIVE:
            return None
        self.returncode = int(code.value)
        self._close_handle()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        handle = self._handle
        if handle is None:
            return self.returncode or 0
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        ms = self._INFINITE if timeout is None else max(0, int(timeout * 1000))
        res = kernel32.WaitForSingleObject(handle, ms)
        if res == self._WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("browser", timeout or 0)
        code = self.poll()
        return 0 if code is None else code

    def __del__(self) -> None:
        self._close_handle()


def _spawn_pipe_windows(arguments: list[str]) -> tuple[Any, Any]:
    """Start a Chromium process on Windows with inheritable pipe handles mapped to CRT FDs 3 and 4."""
    import ctypes
    import struct
    import ctypes.wintypes as wintypes

    from nekopower.builtin.browser.cdp import PipeTransport

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    c_read = wintypes.HANDLE()
    p_write = wintypes.HANDLE()
    p_read = wintypes.HANDLE()
    c_write = wintypes.HANDLE()
    nul_handle = wintypes.HANDLE()

    def close_h(handle: Any) -> None:
        val = handle.value if isinstance(handle, wintypes.HANDLE) else handle
        if val and val != ctypes.c_void_p(-1).value:
            kernel32.CloseHandle(val)

    if not kernel32.CreatePipe(ctypes.byref(c_read), ctypes.byref(p_write), ctypes.byref(sa), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel32.CreatePipe(ctypes.byref(p_read), ctypes.byref(c_write), ctypes.byref(sa), 0):
        err = ctypes.get_last_error()
        close_h(c_read)
        close_h(p_write)
        raise ctypes.WinError(err)
    try:
        # Parent ends must not be inherited by the child.
        kernel32.SetHandleInformation(p_write, 1, 0)
        kernel32.SetHandleInformation(p_read, 1, 0)
        # Redirect child stdin/stdout/stderr to NUL (matches DEVNULL on Popen).
        generic_rw = 0x80000000 | 0x40000000
        share_rw = 0x00000001 | 0x00000002
        open_existing = 3
        nul_raw = kernel32.CreateFileW("NUL", generic_rw, share_rw, ctypes.byref(sa), open_existing, 0, None)
        invalid_handle = ctypes.c_void_p(-1).value
        if nul_raw and nul_raw != invalid_handle:
            nul_handle = wintypes.HANDLE(nul_raw)
        # MSVCRT StartupInfo lpReserved2 layout: int32 count=5, byte[5] flags (FOPEN|FPIPE = 0x09),
        # pointer-sized[5] handles for CRT FDs 0..4 (Chromium reads FD 3, writes FD 4).
        ptr_fmt = "<5Q" if ctypes.sizeof(ctypes.c_void_p) == 8 else "<5I"
        fd_table = struct.pack("<I", 5) + bytes([0x00, 0x00, 0x00, 0x09, 0x09]) + struct.pack(
            ptr_fmt, 0, 0, 0, c_read.value, c_write.value
        )
        cbuf = ctypes.create_string_buffer(fd_table)
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.cbReserved2 = len(fd_table)
        si.lpReserved2 = ctypes.cast(cbuf, ctypes.c_void_p)
        if nul_handle.value:
            si.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            si.hStdInput = nul_handle
            si.hStdOutput = nul_handle
            si.hStdError = nul_handle
        pi = PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
        ok = kernel32.CreateProcessW(None, cmdline, None, None, True, 0, None, None, ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(pi.hThread)
        proc = _Win32Process(pi.hProcess, pi.dwProcessId)
        try:
            transport = PipeTransport(p_read.value, p_write.value, is_win32_handle=True)
        except BaseException:
            kill_tree(proc.pid)
            close_h(pi.hProcess)
            raise
        return proc, transport
    except BaseException:
        close_h(p_read)
        close_h(p_write)
        raise
    finally:
        close_h(c_read)
        close_h(c_write)
        close_h(nul_handle)


class _PosixSpawnProcess:
    """Minimal ``Popen``-like handle for a POSIX process launched with ``os.posix_spawnp``."""

    def __init__(self, pid: int, args: list[str]) -> None:
        self.pid = int(pid)
        self._args = list(args)
        self.returncode: int | None = None
        self._lock = threading.Lock()

    def poll(self) -> int | None:
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            try:
                waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self.returncode = 0
                return self.returncode
            if waited_pid == 0:
                return None
            self.returncode = os.waitstatus_to_exitcode(status)
            return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            with self._lock:
                if self.returncode is not None:
                    return self.returncode
                try:
                    _, status = os.waitpid(self.pid, 0)
                except ChildProcessError:
                    self.returncode = 0
                    return self.returncode
                self.returncode = os.waitstatus_to_exitcode(status)
                return self.returncode
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            code = self.poll()
            if code is not None:
                return code
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self._args, timeout)
            time.sleep(min(0.02, remaining))


def _dup_fd_above(fd: int, minimum: int = 5) -> int:
    """Duplicate ``fd`` onto a fresh CLOEXEC descriptor ``>= minimum``."""
    if fd >= minimum:
        return fd
    try:
        import fcntl

        dup_cmd = getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD)
        new_fd = int(fcntl.fcntl(fd, dup_cmd, minimum))
        os.set_inheritable(new_fd, False)
        return new_fd
    except ImportError:
        temps: list[int] = []
        try:
            cand = os.dup(fd)
            while cand < minimum:
                temps.append(cand)
                cand = os.dup(fd)
            os.set_inheritable(cand, False)
            return cand
        finally:
            for temp in temps:
                try:
                    os.close(temp)
                except OSError:
                    # Best-effort cleanup of temporary duplicated low FDs.
                    pass


def _build_posix_pipe_file_actions(
    devnull_fd: int, src_r: int, src_w: int, parent_fds: tuple[int, ...] = ()
) -> list[tuple[Any, ...]]:
    """Build ``posix_spawn`` file actions mapping FDs 0..2 to ``devnull_fd`` and FDs 3/4 to pipe ends."""
    dup2_op = getattr(os, "POSIX_SPAWN_DUP2", "POSIX_SPAWN_DUP2")
    close_op = getattr(os, "POSIX_SPAWN_CLOSE", "POSIX_SPAWN_CLOSE")
    actions: list[tuple[Any, ...]] = [
        (dup2_op, devnull_fd, 0),
        (dup2_op, devnull_fd, 1),
        (dup2_op, devnull_fd, 2),
        (dup2_op, src_r, 3),
        (dup2_op, src_w, 4),
    ]
    seen_close: set[int] = set()
    for fd in (devnull_fd, src_r, src_w, *parent_fds):
        if fd >= 5 and fd not in seen_close:
            seen_close.add(fd)
            actions.append((close_op, fd))
    return actions


def _spawn_pipe_posix(arguments: list[str]) -> tuple[Any, Any]:
    """Start a Chromium process on POSIX with pipes mapped to FDs 3 (read) and 4 (write).

    Uses ``os.posix_spawnp`` with explicit ``file_actions`` rather than
    ``subprocess.Popen(..., preexec_fn=...)`` so no Python code ever executes
    between ``fork`` and ``exec`` inside multithreaded worker threads.
    """
    from nekopower.builtin.browser.cdp import PipeTransport

    spawnp = getattr(os, "posix_spawnp", None)
    if spawnp is None:
        raise OSError("os.posix_spawnp is unavailable on this platform")

    c_read: int | None = None
    p_write: int | None = None
    p_read: int | None = None
    c_write: int | None = None
    devnull_fd: int | None = None
    extra_parent_close: list[int] = []
    process: _PosixSpawnProcess | None = None

    def close_fd(fd: int | None) -> None:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                # Best-effort cleanup of pipe/spawn file descriptors.
                pass

    try:
        c_read, p_write = os.pipe()
        p_read, c_write = os.pipe()
        devnull_fd = os.open(os.devnull, os.O_RDWR)
        for fd in (c_read, p_write, p_read, c_write, devnull_fd):
            os.set_inheritable(fd, False)
        src_r = _dup_fd_above(c_read, 5)
        if src_r != c_read:
            extra_parent_close.append(src_r)
        src_w = _dup_fd_above(c_write, 5)
        if src_w != c_write:
            extra_parent_close.append(src_w)
        src_null = _dup_fd_above(devnull_fd, 5)
        if src_null != devnull_fd:
            extra_parent_close.append(src_null)
        actions = _build_posix_pipe_file_actions(src_null, src_r, src_w, (p_read, p_write))
        try:
            pid = spawnp(arguments[0], arguments, os.environ, file_actions=actions, setsid=True)
        except (TypeError, NotImplementedError):
            pid = spawnp(arguments[0], arguments, os.environ, file_actions=actions)
        process = _PosixSpawnProcess(pid, arguments)
        transport = PipeTransport(p_read, p_write, is_win32_handle=False)
        return process, transport
    except BaseException:
        if process is not None:
            kill_tree(process.pid)
        close_fd(p_read)
        close_fd(p_write)
        raise
    finally:
        close_fd(c_read)
        close_fd(c_write)
        if devnull_fd is not None:
            try:
                os.close(devnull_fd)
            except OSError:
                # Best-effort cleanup of /dev/null FD after spawn.
                pass
        for fd in extra_parent_close:
            close_fd(fd)


def _spawn_pipe_process(arguments: list[str]) -> tuple[Any, Any]:
    if sys.platform == "win32":
        return _spawn_pipe_windows(arguments)
    return _spawn_pipe_posix(arguments)


def _pipe_read_available(transport: Any, timeout: float) -> bytes | None:
    """Read available bytes from an unstarted ``PipeTransport``; ``b""`` on wait, ``None`` on EOF/error."""
    handle = transport._read_handle
    if handle is None:
        return None
    if transport._is_win32:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        avail = wintypes.DWORD()
        if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
            return None
        if avail.value == 0:
            time.sleep(timeout)
            return b""
        to_read = min(int(avail.value), 64 * 1024)
        buf = ctypes.create_string_buffer(to_read)
        nread = wintypes.DWORD()
        if not kernel32.ReadFile(handle, buf, to_read, ctypes.byref(nread), None) or nread.value == 0:
            return None
        return buf.raw[: nread.value]
    import selectors

    try:
        with selectors.DefaultSelector() as sel:
            sel.register(handle, selectors.EVENT_READ)
            events = sel.select(timeout)
    except (OSError, ValueError):
        return None
    if not events:
        return b""
    try:
        data = os.read(handle, 64 * 1024)
    except OSError:
        return None
    return data if data else None


def _probe_pipe_ready(process: Any, transport: Any, stop: threading.Event | None) -> bool:
    """Verify the browser answered ``Browser.getVersion`` over its debugging pipe. Blocking."""
    import json

    try:
        transport.write_sync(b'{"id":0,"method":"Browser.getVersion"}\x00')
    except OSError:
        return False
    buf = bytearray()
    kept = bytearray()
    deadline = time.monotonic() + PIPE_PROBE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            return False
        chunk = _pipe_read_available(transport, 0.05)
        if chunk is None:
            return False
        if chunk:
            buf.extend(chunk)
            while True:
                nul = buf.find(0)
                if nul == -1:
                    break
                raw = bytes(buf[:nul])
                del buf[: nul + 1]
                if not raw:
                    continue
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == 0:
                    if kept or buf:
                        transport._initial_buffer = bytes(kept) + bytes(buf)
                    return "result" in msg
                kept.extend(raw)
                kept.append(0)
        elif process.poll() is not None:
            return False
    return False


def _start_pipe(
    install: BrowserInstall,
    user_data_dir: str,
    headless: bool,
    extra: tuple[str, ...],
    stop: threading.Event | None = None,
) -> RunningBrowser | None:
    """Try starting the browser with ``--remote-debugging-pipe``. Returns None to fall back to TCP."""
    if not _can_use_pipe():
        return None
    if stop is not None and stop.is_set():
        raise BrowserStartError(f"{install.name} was not started: the start was stopped")
    try:
        process, transport = _spawn_pipe_process(
            launch_arguments(install.executable, user_data_dir, headless=headless, extra=extra, pipe=True)
        )
    except OSError:
        logger.info("%s debugging pipe spawn failed; falling back to a local debugging port", install.name, exc_info=True)
        return None
    started = _created(process.pid)
    ready = False
    try:
        ready = _probe_pipe_ready(process, transport, stop)
    except Exception:  # noqa: BLE001 - fall back to TCP below
        logger.debug("probing the browser's debugging pipe failed", exc_info=True)
        ready = False
    if stop is not None and stop.is_set():
        transport.close_sync()
        _kill_started(process, user_data_dir, handed_over_too=True)
        raise BrowserStartError(f"{install.name} was not started: the start was stopped")
    if ready:
        return RunningBrowser(
            install=install,
            endpoint="",
            process=process,
            owned=True,
            user_data_dir=user_data_dir,
            started=started,
            headless=headless,
            pipe=transport,
        )
    transport.close_sync()
    _kill_started(process, user_data_dir, handed_over_too=True)
    logger.info("%s did not answer on its debugging pipe; falling back to a local debugging port", install.name)
    return None


def _start(
    install: BrowserInstall,
    user_data_dir: str,
    headless: bool,
    extra: tuple[str, ...],
    stop: threading.Event | None = None,
) -> RunningBrowser:
    """Start the browser on a profile and wait for its endpoint. Blocking.

    ``stop`` is set by whoever asked for the browser when they no longer
    wait for it (the call was stopped, the process is shutting down): the
    start gives up at once and takes what it started with it, so that a
    browser nobody will ever close is not left behind.
    """
    try:
        os.makedirs(user_data_dir, exist_ok=True)
    except OSError as exc:
        raise BrowserStartError(f"{install.name} could not be started: its profile directory cannot be made ({exc})") from exc
    # A browser left running on this profile (by an earlier run of the
    # application) is used as it is: starting another on the same profile
    # would only open a window in that one and exit.
    existing = running_endpoint(user_data_dir)
    if existing is not None and not _left_by_a_browser_that_is_gone(user_data_dir):
        return RunningBrowser(install=install, endpoint=existing, process=None, owned=True, user_data_dir=user_data_dir)
    stale = Path(user_data_dir, ACTIVE_PORT_FILE)
    try:
        stale.unlink()
    except OSError:
        pass  # Not there, which is the usual case.
    piped = _start_pipe(install, user_data_dir, headless, extra, stop)
    if piped is not None:
        return piped
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform != "win32":
        # A group of its own, so a signal meant for us does not reach it first.
        options["start_new_session"] = True
    if stop is not None and stop.is_set():
        raise BrowserStartError(f"{install.name} was not started: the start was stopped")
    try:
        process = subprocess.Popen(  # noqa: S603 - the executable is a browser found on this computer
            launch_arguments(install.executable, user_data_dir, headless=headless, extra=extra), **options
        )
    except OSError as exc:
        raise BrowserStartError(f"{install.name} could not be started: {exc}") from exc
    started = _created(process.pid)
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            _kill_started(process, user_data_dir, handed_over_too=True)
            raise BrowserStartError(f"{install.name} was not started: the start was stopped")
        active = read_active_port(user_data_dir)
        if active is not None and _port_open(active[0]):
            # Some browsers (Edge) hand over to another process and let the
            # one that was started exit at once. The browser is then not our
            # child: its process is looked up by its port when it is closed.
            child = process if process.poll() is None else None
            return RunningBrowser(
                install=install,
                endpoint=endpoint_url(*active),
                process=child,
                owned=True,
                user_data_dir=user_data_dir,
                started=started if child is not None else None,
                headless=headless,
            )
        time.sleep(0.1)
    exited = process.poll() is not None
    _kill_started(process, user_data_dir)
    if not exited:
        raise BrowserStartError(f"{install.name} did not open its debugging port within {LAUNCH_TIMEOUT_SECONDS:.0f}s")
    raise BrowserStartError(
        f"{install.name} exited (code {process.returncode}) without opening its debugging port. "
        "It may be set up by an administrator not to allow remote debugging."
    )


def _kill_started(process: Any, user_data_dir: str, *, handed_over_too: bool = False) -> None:
    """Take away what a start brought up that will not be used. Blocking.

    Whatever was started on the profile must not stay behind: the process
    that was started, or, when that one has exited (some browsers hand over
    to another process), whatever runs on the profile now. The profile is
    only searched then: finding it means looking at every process.

    ``handed_over_too`` is for a start that was told to stop, which may be at
    any moment of the coming up: the process that was started may have handed
    over already and not have exited yet, and the one it handed over to is
    not among what it started. Both are taken then. (A start that ran out of
    its time is long past that moment.)
    """
    started_runs = process.poll() is None
    if started_runs:
        kill_tree(process.pid)
    if handed_over_too or not started_runs:
        kill_tree(browser_process_on(user_data_dir))


async def launch(
    install: BrowserInstall,
    user_data_dir: str,
    *,
    headless: bool = False,
    extra: tuple[str, ...] = (),
    stop: threading.Event | None = None,
) -> RunningBrowser:
    """Start ``install`` on ``user_data_dir`` (or find it running there) and return how to reach it.

    The start runs in a worker thread and goes on when this call is
    cancelled; set ``stop`` to make it give up (see ``_start``).
    """
    return await asyncio.to_thread(_start, install, user_data_dir, headless, extra, stop)


def _attach_profile_dirs(install: BrowserInstall, exclude_user_data_dir: str | None = None) -> list[str]:
    """Return candidate profile directories to check when attaching to ``install``. Blocking."""
    excluded = _profile_key(exclude_user_data_dir) if exclude_user_data_dir else None
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path:
            return
        key = _profile_key(path)
        if excluded is not None and key == excluded:
            return
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    for profile in _running_browser_profiles(install.executable, exclude_user_data_dir=exclude_user_data_dir):
        add(profile)
    if install.user_data_dir:
        add(install.user_data_dir)
    else:
        for profile in _infer_profile_dirs(install.executable):
            add(profile)
    return candidates


async def attach_endpoints(install: BrowserInstall, *, exclude_user_data_dir: str | None = None) -> list[RunningBrowser]:
    """Return every running debugging endpoint found for ``install`` across its candidate profiles.

    Raises ``BrowserStartError`` when none of its profiles has remote debugging open.
    """
    profiles = await asyncio.to_thread(_attach_profile_dirs, install, exclude_user_data_dir)
    if not profiles:
        raise BrowserStartError(f"where {install.name} keeps its profile is not known, so it cannot be attached to")
    found: list[RunningBrowser] = []
    for profile in profiles:
        endpoint = await asyncio.to_thread(running_endpoint, profile)
        if endpoint is not None:
            found.append(RunningBrowser(install=install, endpoint=endpoint, process=None, owned=False, user_data_dir=profile))
    if found:
        return found
    raise BrowserStartError(
        f"{install.name} is not running with remote debugging on. Open chrome://inspect/#remote-debugging "
        "in it and allow remote debugging (Chrome 144 or later), then try again."
    )


async def attach(install: BrowserInstall, *, exclude_user_data_dir: str | None = None) -> RunningBrowser:
    """Find the browser the user is using, with remote debugging switched on by them.

    Raises ``BrowserStartError`` when it is not running that way.
    """
    return (await attach_endpoints(install, exclude_user_data_dir=exclude_user_data_dir))[0]


def kill_tree(pid: int | None) -> None:
    """Kill a process and everything it started. Blocking; best effort."""
    if pid is None:
        return
    try:
        import psutil

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        children = parent.children(recursive=True)
        for process in [*children, parent]:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        psutil.wait_procs([*children, parent], timeout=3.0)
        return
    except ImportError:
        logger.warning("psutil is not available; killing the browser with the system's own tool")
    except Exception as exc:  # noqa: BLE001 - the system's tool is tried next
        logger.warning("killing the browser's process tree failed (%s); trying the system's own tool", type(exc).__name__)
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, check=False)  # noqa: S603, S607
        else:
            import signal

            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("the browser process %s could not be killed: %s", pid, type(exc).__name__)


# How far apart two readings of when a process was created may be for it to
# be the same process (see ``_is_the_child``).
_SAME_PROCESS_SECONDS = 1.0


def _created(pid: int) -> float | None:
    """Return when the process of a number was created, or None when that cannot be told. Blocking."""
    try:
        import psutil

        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - no psutil, no such process, no right to ask: it cannot be told
        return None


def _is_the_child(pid: int, started: float | None, user_data_dir: str | None) -> bool:
    """Whether the process of that number is still the one that was started. Blocking.

    ``poll`` says a child runs for as long as nobody has collected its exit,
    and for a moment longer when another thread is waiting for it; a number
    that was given to another program meanwhile shows by when its process
    was created. True when that was not known at the start.

    The two times may be up to a second apart: on Linux the time is worked
    out from when the system was started, which is itself worked out from
    the clock of now, and a clock that was set meanwhile moves it. The time
    does not settle it alone, however close: another program may have got
    the number of a child that ran for less than a second. The process has
    to be the browser of the profile by its command line too.

    With no time on record (it could not be asked for when the child was
    started, and the command line could not be now), ``poll`` is all there
    is.
    """
    if started is None:
        return True
    now = _created(pid)
    if now is None or abs(now - started) >= _SAME_PROCESS_SECONDS:
        return False
    return user_data_dir is not None and _runs_on(pid, user_data_dir)


def _wait_gone(process: Any, seconds: float) -> bool:
    if process is None:
        return True
    try:
        process.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def browser_process_on(user_data_dir: str | os.PathLike[str]) -> int | None:
    """Return the browser process that runs on a profile directory, or None. Blocking.

    It is found by its command line: the main process of a browser names its
    profile with ``--user-data-dir`` and, unlike the processes it starts for
    its pages, has no ``--type``. The profile directory is one this
    application keeps for itself, so no browser of the user's matches.
    """
    try:
        import psutil
    except ImportError:
        return None
    wanted = _profile_key(user_data_dir)
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            arguments = process.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if _is_browser_of(arguments, wanted):
            return int(process.info["pid"])
    return None


def _profile_key(user_data_dir: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(user_data_dir)))


def _is_browser_of(arguments: list[str], wanted: str) -> bool:
    """Whether a command line is that of the main process of a browser on the profile ``wanted`` (a ``_profile_key``)."""
    if any(argument.startswith("--type=") for argument in arguments):
        return False
    for argument in arguments:
        if argument.startswith("--user-data-dir="):
            named = argument[len("--user-data-dir=") :].strip('"')
            if _profile_key(named) == wanted:
                return True
    return False


def _runs_on(pid: int, user_data_dir: str | os.PathLike[str]) -> bool:
    """Whether the process of a number is the browser of a profile, by its command line. Blocking.

    False when the command line cannot be read: what is not known to be the
    browser is not killed for it.
    """
    try:
        import psutil

        arguments = psutil.Process(pid).cmdline()
    except Exception:  # noqa: BLE001 - no psutil, no such process, no right to read it: it cannot be told
        return False
    return _is_browser_of(list(arguments or []), _profile_key(user_data_dir))


def _pid_of(browser: RunningBrowser) -> int | None:
    """Return the process of a browser this process owns, or None when it cannot be found. Blocking.

    The number of the child that was started is used only while that child
    runs. Once it has exited (the user closed the browser, or it handed over
    to another process and went), the system may give its number to any
    other program: killed by that number, it would be that program, with
    whatever it had not saved.
    """
    child = browser.process
    if child is not None and child.poll() is None and _is_the_child(child.pid, browser.started, browser.user_data_dir):
        return child.pid
    if browser.user_data_dir:
        # Not our child (found running on the profile), or no longer running:
        # the browser is looked up by the profile it runs on.
        return browser_process_on(browser.user_data_dir)
    return None


def kill(browser: RunningBrowser) -> None:
    """Kill a browser this process owns without asking it to close; one that was attached to is left running. Blocking."""
    if browser.owned:
        kill_tree(_pid_of(browser))
        if browser.pipe is not None:
            try:
                browser.pipe.close_sync()
            except Exception:  # noqa: BLE001 - best effort handle cleanup
                pass


async def close(browser: RunningBrowser, connection: Any = None) -> None:
    """Close a browser this process owns; a browser that was attached to is left running.

    ``connection`` is an open protocol connection to it, if there is one: the
    browser is asked to close through it first.
    """
    if not browser.owned:
        return
    # The process that was started, for as long as it runs. One that has
    # exited says nothing about the browser, which may run on as the process
    # it handed over to: that one is dealt with as a browser that was never
    # our child.
    child = browser.process if browser.process is not None and browser.process.poll() is None else None
    asked = False
    if connection is not None and not connection.closed:
        try:
            await connection.send("Browser.close", timeout=3.0)
            asked = True
        except Exception:  # noqa: BLE001 - killed below instead
            logger.debug("asking the browser to close failed", exc_info=True)
    try:
        if asked and child is not None and await asyncio.to_thread(_wait_gone, child, CLOSE_WAIT_SECONDS):
            # The child went. With the port of the profile closed, that was the
            # browser; with it still answering, the child had handed over to
            # another process after the port was open, and that one is the
            # browser. The port is asked, not the processes on the profile:
            # listing those reads the command line of every process there is,
            # and every close would pay for it.
            if (
                browser.pipe is not None
                or not browser.user_data_dir
                or await asyncio.to_thread(running_endpoint, browser.user_data_dir) is None
            ):
                return
            child = None
        if asked and child is None:
            # Not our child, so its exit cannot be waited for; give it a moment.
            await asyncio.sleep(1.0)
            if browser.user_data_dir and await asyncio.to_thread(running_endpoint, browser.user_data_dir) is None:
                return
        # Which process that is, is asked only now (``kill`` looks it up): what
        # the child was when the closing began may no longer hold (it may have
        # handed over and gone meanwhile), and its number would then be nobody's,
        # or somebody else's.
        await asyncio.to_thread(kill, browser)
    finally:
        if browser.pipe is not None:
            try:
                await browser.pipe.close()
            except Exception:  # noqa: BLE001 - best effort handle cleanup
                pass
