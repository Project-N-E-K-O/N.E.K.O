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

"""Where dsh is kept, and getting it there: download, check, remove.

dsh is not part of the application; a user who wants it downloads it. All of
it lives below one directory in the data root::

    <data root>/nekoclaw/dsh/
    ├─ runtime/<version>/     the program: the executable and what ships with
    │   └─ installed.json     it; written last, so its presence says "complete"
    ├─ home/                  dsh's own home (``DSH_HOME``): its profile, the
    │                         logs of its sessions, the plugins a user added
    ├─ cache/, resources/     what the executable unpacks of itself, and what
    │                         dsh downloads for its plugins
    ├─ download/              a download under way
    └─ settings.json          which runtime task agents use (``PREFERENCES``)

One exact version is used (``DSH_VERSION``): dsh is a developer preview whose
releases break compatibility. The program is one file of a PyPI release, a
zip. It is not signed, so the file is accepted only when its SHA-256 is the
one recorded here for that version and platform; anything else is deleted.

The directory is not among those a change of the storage location moves:
the program is large and a profile with plugins is full of links. After such
a change dsh is simply not installed at the new place.

Nothing here runs at import or at startup, and nothing is downloaded unless
``install`` is called.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import stat
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nekoclaw.dsh.profile import PROFILE, SESSION_FILE, DshLaunch, DshUnavailable
from utils.asyncio_executor import to_thread_through_cancellation

logger = logging.getLogger(__name__)

# What ``settings.json`` may say under "use": dsh whenever it is installed, or
# the built-in runtime although it is.
PREFERENCES: tuple[str, ...] = ("auto", "builtin")
INSTALLED_FILE = "installed.json"
# Where the program lies inside the release file.
_ARCHIVE_PREFIX = "deepseek_harness_runtime/runtime/"
_EXECUTABLE_STEM = "deepseek-harness-sdk-runtime"
# The bundles dsh's profile is made of; everything else in it the user added.
_OWN_BUNDLES = frozenset({"@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"})
# What dsh names a session: letters, digits and hyphens (a UUID).
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}")
_PLATFORMS = {"win32": "win", "darwin": "macos", "linux": "linux"}
_MACHINES = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}
_CHUNK_BYTES = 1 << 20

# Called with the bytes fetched so far and the size of the file.
Progress = Callable[[int, int], None]


def _numbers(version: str) -> tuple[int, ...]:
    """Return the leading numbers of a version; nothing for one that does not start with a number."""
    numbers = []
    for part in version.split("."):
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers)


def _too_old(system: str | None) -> bool:
    """Whether this system is known to be older than what dsh's release runs on.

    Only what is known counts: a system that does not tell its version is
    taken to be new enough.
    """
    from config.nekoclaw_settings import DSH_MIN_GLIBC, DSH_MIN_MACOS

    if system == "macos":
        version = _numbers(platform.mac_ver()[0])
        return bool(version) and version[0] < DSH_MIN_MACOS
    if system == "linux":
        library, version = platform.libc_ver()
        numbers = _numbers(version)
        return library == "glibc" and len(numbers) >= 2 and numbers[:2] < tuple(DSH_MIN_GLIBC)
    return False


def platform_tag() -> str | None:
    """Return dsh's name for this platform, or None when dsh is not released for it."""
    from config.nekoclaw_settings import DSH_WHEELS

    system = _PLATFORMS.get(sys.platform)
    machine = _MACHINES.get(platform.machine().lower())
    tag = f"{system}-{machine}" if system and machine else None
    if tag not in DSH_WHEELS or _too_old(system):
        return None
    return tag


def executable_name(tag: str) -> str:
    return f"{_EXECUTABLE_STEM}-{tag}{'.exe' if tag.startswith('win-') else ''}"


class DshInstallation:
    """The dsh of one data root: whether it is there, getting it, removing it."""

    def __init__(self, root: Path | Callable[[], Path], *, transport: Any = None) -> None:
        self._root = root
        # An ``httpx`` transport, for tests; None uses the network.
        self._transport = transport
        self._lock = asyncio.Lock()
        # Progress of the download under way: (bytes so far, bytes in all).
        self._progress: tuple[int, int] | None = None
        self._last_error: str | None = None

    # -- paths (resolving the root may read configuration: blocking) -------

    def root(self) -> Path:
        source = self._root
        return Path(source() if callable(source) else source)

    def runtime_dir(self) -> Path:
        from config.nekoclaw_settings import DSH_VERSION

        return self.root() / "runtime" / DSH_VERSION

    def home(self) -> Path:
        return self.root() / "home"

    def settings_file(self) -> Path:
        return self.root() / "settings.json"

    # -- state ------------------------------------------------------------

    def _executable(self) -> Path | None:
        """Blocking: return the installed executable of the version in use, or None."""
        tag = platform_tag()
        if tag is None:
            return None
        directory = self.runtime_dir()
        executable = directory / executable_name(tag)
        if (directory / INSTALLED_FILE).is_file() and executable.is_file():
            return executable
        return None

    def installed(self) -> bool:
        """Blocking: return whether the version in use is installed."""
        return self._executable() is not None

    def launch(self) -> DshLaunch:
        """Blocking: return what a dsh process is started with; raises ``DshUnavailable``."""
        executable = self._executable()
        if executable is None:
            raise DshUnavailable("dsh is not installed")
        home = self.home()
        home.mkdir(parents=True, exist_ok=True)
        return DshLaunch(executable=executable, home=home)

    def preference(self) -> str:
        """Blocking: return which runtime the user wants task agents to use."""
        try:
            data = json.loads(self.settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return PREFERENCES[0]
        value = data.get("use") if isinstance(data, dict) else None
        return value if value in PREFERENCES else PREFERENCES[0]

    def set_preference(self, value: str) -> None:
        """Blocking: store which runtime task agents use; raises ValueError for an unknown one."""
        if value not in PREFERENCES:
            raise ValueError(f"unknown runtime preference {value!r}")
        path = self.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"use": value}), encoding="utf-8")
        os.replace(temporary, path)

    def wanted(self) -> bool:
        """Blocking: return whether new commissions are to run on dsh: installed, and not switched off."""
        return self.installed() and self.preference() == "auto"

    def status(self) -> dict[str, Any]:
        """Blocking: return what a page about the runtime shows."""
        from config.nekoclaw_settings import DSH_VERSION, DSH_WHEELS

        tag = platform_tag()
        wheel = DSH_WHEELS.get(tag) if tag is not None else None
        directory = self.runtime_dir()
        installed = self._executable() is not None
        installed_at = None
        if installed:
            try:
                installed_at = json.loads((directory / INSTALLED_FILE).read_text(encoding="utf-8")).get("installed_at")
            except (OSError, ValueError, AttributeError):
                installed_at = None
        progress = self._progress
        return {
            "version": DSH_VERSION,
            # False: there is no dsh for this platform (Windows on ARM, say).
            "supported": wheel is not None,
            "platform": tag,
            "installed": installed,
            "installed_at": installed_at,
            "path": str(directory) if installed else None,
            "download_bytes": int(wheel["size"]) if wheel is not None else None,
            "downloading": progress is not None,
            "downloaded_bytes": progress[0] if progress is not None else None,
            "last_error": self._last_error,
            "preference": self.preference(),
            "in_use": installed and self.preference() == "auto",
        }

    def plugins(self) -> list[dict[str, Any]]:
        """Blocking: return the plugins the user added to dsh, and whether each is switched on.

        Read from the files of dsh's profile; no dsh process is needed. A
        plugin runs inside the dsh process with everything that process may
        do, and without a sandbox: whoever shows this list, or adds to it,
        has to say so.
        """
        try:
            package = json.loads((self.home() / "profiles" / PROFILE / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(package, dict):
            return []
        dependencies = package.get("dependencies")
        dependencies = dependencies if isinstance(dependencies, dict) else {}
        profile = (package.get("dsh") or {}).get("profile") if isinstance(package.get("dsh"), dict) else None
        bundles = profile.get("bundles") if isinstance(profile, dict) else None
        enabled = [str(name) for name in bundles] if isinstance(bundles, list) else []
        names = [*dependencies, *(name for name in enabled if name not in dependencies)]
        return [
            {"name": name, "version": str(dependencies.get(name) or ""), "enabled": name in enabled}
            for name in names
            if name not in _OWN_BUNDLES
        ]

    def forget_session(self, session_dir: Path) -> bool:
        """Blocking: remove what dsh kept of the session a session directory belongs to.

        dsh never removes the log of a session. The host removes what was
        said in a commission's session some time after it ended; for a
        commission that ran in dsh this is the counterpart: the session's
        log in dsh's home, what dsh cached of it, and the note that tied the
        two together. Returns whether there was such a session.

        Raises ``OSError`` when something of it could not be removed (a log
        held open, say). The note then stays, so that the next attempt still
        knows which sessions to remove.
        """
        note = Path(session_dir) / SESSION_FILE
        try:
            known = json.loads(note.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        named = [known.get("session"), *(known.get("previous") or ())] if isinstance(known, dict) else []
        home = self.home()
        failures: list[OSError] = []
        try:
            # One directory per working directory; whatever else is there (a
            # file the system put there, say) is none.
            projects = [item for item in (home / "sessions").iterdir() if item.is_dir() and not item.is_symlink()]
        except FileNotFoundError:
            projects = []
        except OSError as exc:
            # Not read: the logs may be there all the same, and the note is
            # kept for the next attempt.
            projects = []
            failures.append(exc)
        # The session it has now, and those it had before that dsh could not
        # resume: whatever dsh still holds of any of them.
        for session in named:
            if not isinstance(session, str) or not _SESSION_ID.fullmatch(session):
                continue
            # One directory per working directory, a session's own below it.
            paths = [project / session for project in projects]
            paths.append(home / "storages" / "session_projcache" / "sessions" / f"{session}.json")
            for path in paths:
                try:
                    _remove(path, strict=True)
                except OSError as exc:
                    failures.append(exc)
        if failures:
            raise failures[0]
        _remove(note, strict=True)
        return True

    # -- install ----------------------------------------------------------

    async def install(self, on_progress: Progress | None = None) -> dict[str, Any]:
        """Download the version in use, check it and unpack it. Returns ``status()``.

        Raises ``DshUnavailable`` when there is no dsh for this platform, when
        no host delivered the file, or when what arrived is not the file
        recorded for this version. Nothing is left installed in those cases.
        A second call while one is at work waits for it.
        """
        from config.nekoclaw_settings import DSH_VERSION, DSH_WHEELS

        async with self._lock:
            if await asyncio.to_thread(self.installed):
                return await asyncio.to_thread(self.status)
            # Both may read files (the system's version, the storage settings).
            tag = await asyncio.to_thread(platform_tag)
            if tag is None:
                raise DshUnavailable("dsh is not released for this platform")
            root = await asyncio.to_thread(self.root)
            wheel = DSH_WHEELS[tag]
            # From here on it counts as downloading, till the ``finally`` below.
            self._last_error = None
            self._progress = (0, int(wheel["size"]))
            # Removed whatever comes of it: also half a file, when the disk
            # filled up or the install was cancelled in the middle.
            archive = root / "download" / str(wheel["file"])
            try:
                await self._download(wheel, archive, on_progress)
                # A thread is not stopped by cancelling who waits for it: this
                # one is waited for all the same, so that what holds the lock
                # (another install, an uninstall) does not run beside it.
                await to_thread_through_cancellation(
                    self._unpack, archive, tag, str(wheel["sha256"]), DSH_VERSION
                )
            except DshUnavailable as exc:
                self._last_error = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001 - told to whoever asked, with the reason
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise DshUnavailable(f"dsh could not be installed: {self._last_error}") from exc
            finally:
                self._progress = None
                await to_thread_through_cancellation(_remove, archive)
            return await asyncio.to_thread(self.status)

    async def _download(self, wheel: dict[str, str | int], target: Path, on_progress: Progress | None) -> None:
        """Fetch the release file to ``target`` from the first host that delivers it intact."""
        import httpx

        from config.nekoclaw_settings import DSH_DOWNLOAD_CONNECT_SECONDS, DSH_DOWNLOAD_HOSTS, DSH_DOWNLOAD_READ_SECONDS

        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        size = int(wheel["size"])
        timeout = httpx.Timeout(
            connect=DSH_DOWNLOAD_CONNECT_SECONDS,
            read=DSH_DOWNLOAD_READ_SECONDS,
            write=DSH_DOWNLOAD_CONNECT_SECONDS,
            pool=DSH_DOWNLOAD_CONNECT_SECONDS,
        )
        options: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
        if self._transport is not None:
            options["transport"] = self._transport
        failures: list[str] = []
        async with httpx.AsyncClient(**options) as client:
            for host in DSH_DOWNLOAD_HOSTS:
                url = f"{host.rstrip('/')}/{wheel['path']}/{wheel['file']}"
                digest = hashlib.sha256()
                fetched = 0
                try:
                    async with client.stream("GET", url) as answer:
                        if answer.status_code != 200:
                            failures.append(f"{host}: HTTP {answer.status_code}")
                            continue
                        handle = await to_thread_through_cancellation(open, target, "wb")
                        try:
                            async for chunk in answer.aiter_bytes(_CHUNK_BYTES):
                                fetched += len(chunk)
                                if fetched > size:
                                    break
                                digest.update(chunk)
                                await to_thread_through_cancellation(handle.write, chunk)
                                self._progress = (fetched, size)
                                if on_progress is not None:
                                    on_progress(fetched, size)
                        finally:
                            await to_thread_through_cancellation(handle.close)
                except httpx.HTTPError as exc:
                    failures.append(f"{host}: {type(exc).__name__}")
                    continue
                if fetched != size or digest.hexdigest() != wheel["sha256"]:
                    # Not the file that was verified: never unpacked, never run.
                    failures.append(f"{host}: the file is not the one expected")
                    await asyncio.to_thread(_remove, target)
                    continue
                return
        await asyncio.to_thread(_remove, target)
        raise DshUnavailable("dsh could not be downloaded (" + "; ".join(failures) + ")")

    def _unpack(self, archive: Path, tag: str, sha256: str, version: str) -> None:
        """Blocking: unpack the program from a checked release file into its directory."""
        directory = self.runtime_dir()
        staging = directory.with_name(f"{directory.name}.unpacking")
        _remove(staging)
        staging.mkdir(parents=True)
        root = os.path.realpath(staging)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or not member.filename.startswith(_ARCHIVE_PREFIX):
                    continue
                relative = member.filename[len(_ARCHIVE_PREFIX) :]
                target = os.path.realpath(os.path.join(root, relative))
                if os.path.commonpath([root, target]) != root:
                    raise DshUnavailable("the release file names a path outside its own directory")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with bundle.open(member) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink, _CHUNK_BYTES)
        executable = staging / executable_name(tag)
        if not executable.is_file():
            raise DshUnavailable("the release file does not hold the program for this platform")
        if not tag.startswith("win-"):
            # A zip keeps no permissions: the program and its helpers must be runnable.
            for item in staging.iterdir():
                if item.is_file() and item.name.startswith(_EXECUTABLE_STEM):
                    item.chmod(item.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        (staging / INSTALLED_FILE).write_text(
            json.dumps({"version": version, "platform": tag, "sha256": sha256, "installed_at": time.time()}),
            encoding="utf-8",
        )
        _remove(directory)
        os.replace(staging, directory)

    # -- uninstall --------------------------------------------------------

    async def uninstall(self, *, purge: bool = False) -> dict[str, Any]:
        """Remove the program and what it unpacked or downloaded. Returns ``status()``.

        dsh's home stays unless ``purge`` is set: the logs of its sessions
        (a commission taken up again after a new install resumes from them)
        and the plugins the user added. Whoever calls this makes sure no dsh
        process is running; a file still in use raises ``DshUnavailable``.
        """
        async with self._lock:
            root = await asyncio.to_thread(self.root)
            names = ["runtime", "cache", "resources", "download"] + (["home"] if purge else [])
            try:
                for name in names:
                    # Waited for also when this is cancelled: the lock (and the
                    # host's, around it) is not let go of while files are being
                    # removed, nor dsh started from a half removed directory.
                    await to_thread_through_cancellation(_remove, root / name, strict=True)
            except OSError as exc:
                raise DshUnavailable(f"dsh could not be removed: {exc}") from exc
            return await asyncio.to_thread(self.status)


def _remove(path: Path, *, strict: bool = False) -> None:
    """Blocking: delete a file or a directory tree; missing is fine."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        # Gone already: that is what was asked for.
        pass
    except OSError:
        if strict:
            raise
        logger.warning("could not remove %s", path.name, exc_info=True)
