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
    except OSError:
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
    start for as long as the other program runs. False when the processes
    cannot be listed, so that a browser that does run is never taken for gone.
    """
    try:
        import psutil  # noqa: F401 - only whether the processes can be listed at all
    except ImportError:
        return False
    return browser_process_on(user_data_dir) is None


def running_endpoint(user_data_dir: str | os.PathLike[str]) -> str | None:
    """Return the debugging endpoint of a browser already running on this profile, or None. Blocking."""
    active = read_active_port(user_data_dir)
    if active is None or not _port_open(active[0]):
        return None
    return endpoint_url(*active)


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

    @property
    def pid(self) -> int | None:
        return getattr(self.process, "pid", None)


def launch_arguments(executable: str, user_data_dir: str, *, headless: bool = False, extra: tuple[str, ...] = ()) -> list[str]:
    arguments = [
        executable,
        "--remote-debugging-port=0",
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
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            _kill_started(process, user_data_dir)
            raise BrowserStartError(f"{install.name} was not started: the start was stopped")
        active = read_active_port(user_data_dir)
        if active is not None and _port_open(active[0]):
            # Some browsers (Edge) hand over to another process and let the
            # one that was started exit at once. The browser is then not our
            # child: its process is looked up by its port when it is closed.
            child = process if process.poll() is None else None
            return RunningBrowser(
                install=install, endpoint=endpoint_url(*active), process=child, owned=True, user_data_dir=user_data_dir
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


def _kill_started(process: Any, user_data_dir: str) -> None:
    """Take away what a start brought up that will not be used. Blocking.

    Whatever was started on the profile must not stay behind: the process
    that was started, or, when that one has exited (some browsers hand over
    to another process), whatever runs on the profile now. The profile is
    only searched then: finding it means looking at every process.
    """
    if process.poll() is None:
        kill_tree(process.pid)
    else:
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


async def attach(install: BrowserInstall) -> RunningBrowser:
    """Find the browser the user is using, with remote debugging switched on by them.

    Raises ``BrowserStartError`` when it is not running that way.
    """
    if not install.user_data_dir:
        raise BrowserStartError(f"where {install.name} keeps its profile is not known, so it cannot be attached to")
    endpoint = await asyncio.to_thread(running_endpoint, install.user_data_dir)
    if endpoint is None:
        raise BrowserStartError(
            f"{install.name} is not running with remote debugging on. Open chrome://inspect/#remote-debugging "
            "in it and allow remote debugging (Chrome 144 or later), then try again."
        )
    return RunningBrowser(install=install, endpoint=endpoint, process=None, owned=False, user_data_dir=install.user_data_dir)


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
    wanted = os.path.normcase(os.path.abspath(os.fspath(user_data_dir)))
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            arguments = process.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(argument.startswith("--type=") for argument in arguments):
            continue
        for argument in arguments:
            if argument.startswith("--user-data-dir="):
                named = argument[len("--user-data-dir=") :].strip('"')
                if os.path.normcase(os.path.abspath(named)) == wanted:
                    return int(process.info["pid"])
    return None


def _pid_of(browser: RunningBrowser) -> int | None:
    """Return the process of a browser this process owns, or None when it cannot be found. Blocking."""
    pid = browser.pid
    if pid is None and browser.user_data_dir:
        # Not our child (found running on the profile, or handed over to by
        # the process we started): it is looked up by the profile it runs on.
        pid = browser_process_on(browser.user_data_dir)
    return pid


def kill(browser: RunningBrowser) -> None:
    """Kill a browser this process owns without asking it to close; one that was attached to is left running. Blocking."""
    if browser.owned:
        kill_tree(_pid_of(browser))


async def close(browser: RunningBrowser, connection: Any = None) -> None:
    """Close a browser this process owns; a browser that was attached to is left running.

    ``connection`` is an open protocol connection to it, if there is one: the
    browser is asked to close through it first.
    """
    if not browser.owned:
        return
    pid = await asyncio.to_thread(_pid_of, browser)
    asked = False
    if connection is not None and not connection.closed:
        try:
            await connection.send("Browser.close", timeout=3.0)
            asked = True
        except Exception:  # noqa: BLE001 - killed below instead
            logger.debug("asking the browser to close failed", exc_info=True)
    if asked and browser.process is not None and await asyncio.to_thread(_wait_gone, browser.process, CLOSE_WAIT_SECONDS):
        return
    if asked and browser.process is None:
        # Not our child, so its exit cannot be waited for; give it a moment.
        await asyncio.sleep(1.0)
        if browser.user_data_dir and await asyncio.to_thread(running_endpoint, browser.user_data_dir) is None:
            return
    await asyncio.to_thread(kill_tree, pid)
