"""uv and Python for plugin tooling, shared by every packaged-build feature.

Source checkouts already have both: tooling runs on ``sys.executable`` with
whatever uv is on PATH. A packaged (Nuitka) server has neither a usable
interpreter nor uv, so this module prepares them on demand under one app-owned
root::

    <app_docs_dir>/plugin-runtime/tools/
        uv/<version>/<os>-<arch>/uv[.exe]   pinned uv, from its GitHub release
        python/                             uv-managed CPython (UV_PYTHON_INSTALL_DIR)
        python.json                         record of the prepared interpreter
        .python-download/                   a CPython build while uv installs it
        envs/                               reserved for tool virtualenvs (not created here)

Preparation downloads the pinned uv archive for this host (checked against a
SHA-256 pinned here, size-capped, placed atomically), then installs the
CPython release matching the host's version and architecture exactly and
records it. A compatible uv on PATH is only an optional fallback.

Both downloads are GitHub release assets and go through the plugin market's
download routes (see package_fetch): GitHub directly or one of its mirrors,
chosen by the same user preference (auto / direct / specified) and ranked by
the same route history as market packages. A mirror only changes where the
bytes come from: uv is checked against the digest pinned here, and the
CPython build against the digest built into that uv, which installs it from
the downloaded file.

Reading the status never starts a child process, touches the network or
creates a directory. Preparing runs in a worker thread, honours a cancel event
and a deadline, and is single-flight within the process: a packaged build
runs one backend per user (the desktop shell and the launcher each hold a
single-instance lock), and only its plugin server prepares, so nothing
serializes preparation across processes.

Nothing calls this module yet; dependency sync and the plugin CLI tool
environment are meant to build on it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import threading
import time
from typing import Literal, Protocol
from urllib.parse import unquote
import zipfile

from packaging.tags import sys_tags
from packaging.version import InvalidVersion, Version

from plugin.core.packaged_metadata import build_environment
from plugin.neko_plugin_cli.git_env import git_env
from plugin.utils.http_imports import load_httpx

PINNED_UV_VERSION = "0.13.6"
_UV_RELEASE_URL = f"https://github.com/astral-sh/uv/releases/download/{PINNED_UV_VERSION}"
# SHA-256 of each uv archive of the pinned release, keyed by target triple:
# the release's sha256.sum, cross-checked against the digests GitHub reports
# for its assets. Every download is checked against these, whichever route
# (GitHub or a mirror) delivered it. Update them, and the version they
# belong to, with PINNED_UV_VERSION (a unit test checks the two agree).
_UV_ARCHIVE_SHA256_VERSION = "0.13.6"
_UV_ARCHIVE_SHA256 = {
    "aarch64-apple-darwin": "60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472",
    "x86_64-apple-darwin": "63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02",
    "aarch64-pc-windows-msvc": "c2426c0c8e28b53d8c440e47a18d96ad4db51e159866c0e81435b196d7f0081c",
    "i686-pc-windows-msvc": "32ceb64db231cbedfd499f7654c72e4c0b564f2846f9e76c0a4a7b2d03d7e67c",
    "x86_64-pc-windows-msvc": "a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f",
    "aarch64-unknown-linux-gnu": "229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d",
    "aarch64-unknown-linux-musl": "819692e404d23e5aae6bc44c0e260f1f5492fd1910ab4693b2c650f926dbe800",
    "armv7-unknown-linux-gnueabihf": "d00cd4949c4524c934afcfee1aedfa9d337cf4e43be4d6b692ad8d74d0a65562",
    "armv7-unknown-linux-musleabihf": "b215398c886e8cc2cfa282fe346c02025957f8633b75a3d4fd848e0adda4eddd",
    "i686-unknown-linux-gnu": "665ea831c38d342900950e606beb2ccc37b7ba0fcefc37b814eb4b076305f2b6",
    "i686-unknown-linux-musl": "de3a015c4b0fce63c178ae4fa95c94c873adfd92fa1e258c6a2a94e529812629",
    "powerpc64le-unknown-linux-gnu": "3b2847185ec2946fcc928500efef7e3e74c255d3be6f2cb435081a84885d8c6c",
    "riscv64gc-unknown-linux-gnu": "517052603ed46483b59d8875c9c61a577946b8f493c73e4339e45e7b119171d0",
    "riscv64gc-unknown-linux-musl": "659a95dc6aee3ecf86742a08d8ca6c6261422121cbd41decc0042f5a71af6305",
    "s390x-unknown-linux-gnu": "fb56d731308aa5c7718059d71afcb14d9a933a974997cee3d4eb5d06fc462a4b",
    "x86_64-unknown-linux-gnu": "f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645",
    "x86_64-unknown-linux-musl": "5d61f26b6c7213779eda90edc6971ea6b0b796bfb8d49a574c8ad69b59838548",
}
# python-build-standalone releases as the pinned uv lists them (Astral's
# mirror) and on GitHub, where the market's mirrors can serve them.
_PYTHON_RELEASE_PREFIXES = (
    "https://releases.astral.sh/github/python-build-standalone/releases/download/",
    "https://github.com/astral-sh/python-build-standalone/releases/download/",
)
_PYTHON_GITHUB_PREFIX = _PYTHON_RELEASE_PREFIXES[1]
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_PYTHON_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_SUPPORTED_PLATFORMS = frozenset({"win32", "darwin", "linux"})
# How often waits re-check the cancel event and the deadline.
_POLL_SECONDS = 0.1
# Inherited settings that would make the managed Python or uv use another
# standard library, packages or environment than the prepared one.
_INHERITED_PYTHON_VARS = frozenset({
    "PYTHONHOME", "PYTHONPATH", "PYTHONPLATLIBDIR", "PYTHONSTARTUP", "PYTHONINSPECT",
    "PYTHONUSERBASE", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
    "__PYVENV_LAUNCHER__",
})
_DEFAULT_BACKGROUND_TIMEOUT = 30 * 60.0

RuntimeState = Literal["source", "ready", "missing", "preparing", "failed", "unsupported"]
# Stable codes a frontend maps to localized text; ``detail`` stays English.
ErrorCode = Literal[
    "unsupported_platform",
    "download_failed",
    "checksum_mismatch",
    "python_install_failed",
    "python_mismatch",
    "canceled",
    "timeout",
    "internal_error",
]
ProgressPhase = Literal["downloading_uv", "checking_uv", "installing_python", "checking_python"]


class ToolRuntimeError(RuntimeError):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail


@dataclass(frozen=True)
class ToolRuntime:
    python: str
    """``sys.executable`` from source; the managed CPython when packaged."""
    uv: str | None
    """May be None from source (callers fall back to pip); always set when packaged."""
    env: dict[str, str]
    """Child-process environment; see :func:`tool_runtime_env`."""
    root: Path | None
    """The tools root; None from source."""
    packaged: bool


@dataclass(frozen=True)
class ProgressEvent:
    phase: ProgressPhase
    downloaded_bytes: int | None = None
    total_bytes: int | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    packaged: bool
    uv_version: str
    python: str | None = None
    uv: str | None = None
    progress: ProgressEvent | None = None
    error_code: ErrorCode | None = None
    error_detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CommandRunner(Protocol):
    def __call__(
        self, cmd: list[str], *, label: str, env: dict[str, str],
        cancel: threading.Event, deadline: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``cmd`` to completion, raising ToolRuntimeError on cancel or deadline."""


# ---------------------------------------------------------------------------
# Paths and host facts


def _is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _root_under(app_docs_dir: str | os.PathLike[str]) -> Path:
    # plugin-runtime/ is registered as regenerable in utils/storage/migration.py:
    # a storage migration leaves it behind and the tools are prepared again.
    # That is required, not just cheaper: the uv-managed CPython under python/
    # contains symlinks, which a migration copying files would fail on.
    return Path(app_docs_dir) / "plugin-runtime" / "tools"


def tools_root() -> Path:
    """The tools root; creates the config manager if this process has none yet."""
    from utils.config_manager import get_config_manager

    # migrate=False: resolving a path must not run the config data migrations.
    return _root_under(get_config_manager(migrate=False).app_docs_dir)


def _known_tools_root() -> Path | None:
    """The tools root without creating the config manager, which writes to disk.

    Resolved from an existing manager or the storage root the launcher exports
    (``peek_app_docs_dir``), so tools prepared by an earlier run read as ready
    before anything in this process has needed the config manager. None only
    in a process started outside the launcher that has not set up its config
    manager yet; status reads then say missing.
    """
    from utils.config_manager import peek_app_docs_dir

    app_docs_dir = peek_app_docs_dir()
    return None if app_docs_dir is None else _root_under(app_docs_dir)


def python_install_dir() -> Path:
    return tools_root() / "python"


def tool_envs_dir() -> Path:
    """Where tool virtualenvs belong. Reserved: this module never creates it."""
    return tools_root() / "envs"


def _python_record_path(root: Path | None = None) -> Path:
    return (tools_root() if root is None else root) / "python.json"


def _python_architecture(platform_tag: str, machine: str | None = None) -> str:
    architecture = platform_tag.rsplit("-", 1)[-1].lower()
    if architecture in {"universal2", "universal", "intel", "fat", "fat3", "fat64"} and machine:
        architecture = machine.lower()
    return {
        "amd64": "x86_64", "arm64": "aarch64", "win32": "x86",
        "i386": "x86", "i686": "x86", "armv7l": "armv7",
        "ppc64le": "powerpc64le", "riscv64": "riscv64gc",
    }.get(architecture, architecture)


def _host_architecture() -> str:
    # sysconfig describes the running interpreter, including emulated Windows
    # or macOS processes; platform.machine() can describe the native machine.
    return _python_architecture(
        sysconfig.get_platform(), platform.machine() if sys.platform == "darwin" else None,
    )


def cached_uv_path() -> Path:
    name = "uv.exe" if sys.platform == "win32" else "uv"
    return tools_root() / "uv" / PINNED_UV_VERSION / f"{sys.platform}-{_host_architecture()}" / name


def pinned_uv_version() -> str:
    """The uv release packaged servers download."""
    return PINNED_UV_VERSION


def packaged_python_request() -> str:
    """The exact CPython a packaged server asks uv for.

    python_full_version markers must resolve as they do in the host, not
    against a newer patch release with the same extension-module ABI. uv ships
    its list of downloadable Pythons inside each release, so the pinned uv must
    know this patch release; scripts/check_nuitka_dist.py verifies that at
    build time.
    """
    platform_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return f"cpython-{version}-{platform_name}-{_host_architecture()}"


# ---------------------------------------------------------------------------
# Environment


def _with_utf8(env: dict[str, str]) -> dict[str, str]:
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# Set below; any spelling of them in the base is dropped so that one value remains.
_FORCED_VARS = frozenset({"PYTHONNOUSERSITE", "PYTHONUTF8", "PYTHONIOENCODING"})


def tool_runtime_env(base: Mapping[str, str] | None = None, *, root: Path | None = None) -> dict[str, str]:
    """Environment for packaged tool child processes.

    Based on ``git_env()`` (``os.environ`` without git's repository-locating
    variables) unless ``base`` is given. Inherited ``UV_*`` variables are
    dropped: they could point uv at another Python directory, cache or index
    than the one prepared here. So are the variables that redirect a Python
    interpreter or uv's target environment (``PYTHONHOME``, ``PYTHONPATH``,
    ``VIRTUAL_ENV``...): the identity probe runs isolated (``-I``) and would
    not notice them, but tools started with this environment would. The user
    site directory is disabled for the same reason. Names are compared
    case-insensitively, as Windows treats them. UTF-8 is forced for Python
    children.
    """
    source = git_env() if base is None else base
    env: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if not upper.startswith("UV_") and upper not in _INHERITED_PYTHON_VARS and upper not in _FORCED_VARS:
            env[name] = value
    env["UV_PYTHON_INSTALL_DIR"] = str(python_install_dir() if root is None else root / "python")
    # Nor packages from the user's own site-packages (~/.local and the like).
    env["PYTHONNOUSERSITE"] = "1"
    return _with_utf8(env)


def _source_runtime() -> ToolRuntime:
    # A source checkout keeps the developer's uv configuration (UV_* included).
    return ToolRuntime(sys.executable, _source_uv(), _with_utf8(git_env()), None, False)


def _source_uv() -> str | None:
    # Same rules as the CLI's deps command: `uv run` exports its own path as UV.
    for candidate in (os.environ.get("UV"), "uv"):
        found = shutil.which(candidate) if candidate else None
        if found:
            return os.path.abspath(found)
    return None


# ---------------------------------------------------------------------------
# Read-only state


def _usable_executable(path: str) -> bool:
    """Exists, is not empty and, on POSIX, may be executed. No child process."""
    try:
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            return False
    except OSError:
        return False
    return sys.platform == "win32" or os.access(path, os.X_OK)


def _prepared_tools(root: Path) -> tuple[str, str] | None:
    """(python, uv) as recorded, when they still fit this host. Reads only."""
    try:
        record = json.loads(_python_record_path(root).read_text(encoding="utf-8"))
        python = record["executable"]
        uv = record["uv"]
        if (
            record["environment"] == build_environment()
            and record.get("python_version") == list(sys.version_info[:3])
            and record.get("python_architecture") == _host_architecture()
            and Version(record["uv_version"]) >= Version(PINNED_UV_VERSION)
            and isinstance(python, str)
            and isinstance(uv, str)
            and _usable_executable(python)
            and _usable_executable(uv)
        ):
            return python, uv
    except (OSError, ValueError, KeyError, TypeError, InvalidVersion):
        # Missing, unreadable or outdated record: the tools need preparing.
        return None
    return None


def _prepared_runtime() -> ToolRuntime | None:
    root = tools_root()
    tools = _prepared_tools(root)
    if tools is None:
        return None
    return ToolRuntime(tools[0], tools[1], tool_runtime_env(root=root), root, True)


def _prepared_status() -> RuntimeStatus | None:
    root = _known_tools_root()
    tools = None if root is None else _prepared_tools(root)
    if tools is None:
        return None
    return RuntimeStatus("ready", True, PINNED_UV_VERSION, python=tools[0], uv=tools[1])


_flight = threading.Lock()
_state_lock = threading.Lock()
_progress: ProgressEvent | None = None
_last_error: ToolRuntimeError | None = None
_background: tuple[threading.Thread, threading.Event] | None = None


def _background_alive() -> bool:
    return _background is not None and _background[0].is_alive()


def _on_background_thread() -> bool:
    background = _background
    return background is not None and background[0] is threading.current_thread()


def read_tool_runtime_status() -> RuntimeStatus:
    """Current state; no child process, network access or directory creation."""
    if not _is_packaged():
        return RuntimeStatus("source", False, PINNED_UV_VERSION, python=sys.executable, uv=_source_uv())
    if sys.platform not in _SUPPORTED_PLATFORMS:
        return RuntimeStatus(
            "unsupported", True, PINNED_UV_VERSION, error_code="unsupported_platform",
            error_detail=f"Plugin tools are not available on {sys.platform}",
        )
    ready = _prepared_status()
    if ready is not None:
        return ready
    with _state_lock:
        progress = _progress
        error = _last_error
        busy = _flight.locked() or _background_alive()
    if busy:
        return RuntimeStatus("preparing", True, PINNED_UV_VERSION, progress=progress)
    # A preparation may have finished between the first check and the snapshot.
    ready = _prepared_status()
    if ready is not None:
        return ready
    if error is not None and error.code == "unsupported_platform":
        # No pinned uv for this platform and no compatible one installed: as
        # final as an unsupported OS, so it is not started again by itself
        # (a foreground preparation still tries, e.g. after a uv was installed).
        return RuntimeStatus(
            "unsupported", True, PINNED_UV_VERSION, error_code=error.code, error_detail=error.detail,
        )
    if error is not None:
        return RuntimeStatus(
            "failed", True, PINNED_UV_VERSION, error_code=error.code, error_detail=error.detail,
        )
    return RuntimeStatus("missing", True, PINNED_UV_VERSION)


# ---------------------------------------------------------------------------
# Preparation helpers


def _check(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise ToolRuntimeError("canceled", "Plugin tool preparation was canceled")
    if time.monotonic() >= deadline:
        raise ToolRuntimeError("timeout", "Plugin tool preparation exceeded its time budget")


class _Reporter:
    def __init__(self, callback: Callable[[ProgressEvent], None] | None) -> None:
        self._callback = callback

    def __call__(self, event: ProgressEvent) -> None:
        global _progress
        with _state_lock:
            _progress = event
        if self._callback is not None:
            self._callback(event)


def _read_output(stream) -> str:
    # Only the end matters to callers (paths, versions, error tails).
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - _MAX_OUTPUT_BYTES))
    return stream.read().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _run_command(
    cmd: list[str], *, label: str, env: dict[str, str], cancel: threading.Event, deadline: float,
) -> subprocess.CompletedProcess[str]:
    """Run one tool command, killing it on cancel or at the deadline.

    Output goes to temporary files, not pipes: a grandchild that inherits the
    handles (uv can start helpers) would otherwise keep a pipe open after the
    child is killed, and reading or even closing it would wait for that
    grandchild. Waiting on the child alone stays bounded on every platform.
    """
    del label  # Only fake runners in tests use it.
    _check(cancel, deadline)
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env, **kwargs,
        )
        try:
            while True:
                try:
                    process.wait(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    _check(cancel, deadline)
        except BaseException:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # Killed but not yet reaped (stuck in the kernel); do not block the cancel on it.
            raise
        result = subprocess.CompletedProcess(cmd, process.returncode, _read_output(stdout), _read_output(stderr))
    return result


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    lines = ((result.stderr or "") + "\n" + (result.stdout or "")).strip().splitlines()
    return " ".join(lines[-3:])[-500:]


def _linux_libc(compatible_tags: list | None = None) -> str | None:
    """"gnu" or "musl", as this interpreter's wheel tags tell; None when neither."""
    platforms = {tag.platform for tag in (sys_tags() if compatible_tags is None else compatible_tags)}
    if any(name.startswith("manylinux") for name in platforms):
        return "gnu"
    if any(name.startswith("musllinux") for name in platforms):
        return "musl"
    return None


def _uv_target(
    platform_name: str | None = None, architecture: str | None = None, libc: str | None = None,
) -> str | None:
    """The Rust target triple of the pinned uv archive for this host, if one is published."""
    platform_name = sys.platform if platform_name is None else platform_name
    architecture = _host_architecture() if architecture is None else architecture
    if platform_name == "win32":
        name = {"x86": "i686"}.get(architecture, architecture)
        target = f"{name}-pc-windows-msvc"
    elif platform_name == "darwin":
        target = f"{architecture}-apple-darwin"
    elif platform_name == "linux":
        libc = _linux_libc() if libc is None else libc
        if libc is None:
            return None
        name = {"x86": "i686"}.get(architecture, architecture)
        abi = f"{libc}eabihf" if name == "armv7" else libc
        target = f"{name}-unknown-linux-{abi}"
    else:
        return None
    return target if target in _UV_ARCHIVE_SHA256 else None


def _uv_archive_name(target: str) -> str:
    return f"uv-{target}.zip" if target.endswith("-windows-msvc") else f"uv-{target}.tar.gz"


def _uv_archive_member(target: str, binary_name: str) -> str:
    # The zip holds the executables at its root; each tarball, in a directory named after it.
    return binary_name if target.endswith("-windows-msvc") else f"uv-{target}/{binary_name}"


# The market's transfer is imported on first use: reading the status, and the
# build check importing this module, need none of it.


def _fetch_release_asset(url: str, directory: Path, **kwargs):
    from plugin.server.infrastructure.package_fetch import fetch_release_asset

    return fetch_release_asset(url, directory, **kwargs)


def _record_route_failure(source_id: str, generation: int, cancel: threading.Event, deadline: float) -> None:
    from plugin.server.infrastructure.package_fetch import record_route_failure

    record_route_failure(source_id, generation, check_stopped=lambda: _check(cancel, deadline))


def _download_failures() -> tuple[type[BaseException], ...]:
    """What a failed download raises, as opposed to a bug in this module."""
    from plugin.server.infrastructure.package_download import PackageDownloadWriteError
    from plugin.server.infrastructure.package_fetch import NoRoutesLeft, PackageLoopUnavailable, RouteFailed

    return (
        load_httpx().HTTPError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
        tarfile.TarError, PackageDownloadWriteError, PackageLoopUnavailable, NoRoutesLeft, RouteFailed,
    )


def _file_sha256(path: Path, cancel: threading.Event, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            _check(cancel, deadline)
            digest.update(chunk)
    return digest.hexdigest()


def _discard_staged(path: Path) -> None:
    """Remove a downloaded file that was used or rejected; best effort.

    Its directory is removed as a whole afterwards, and a next download
    replaces it under the same name, so a failure here (a file still held
    open on Windows, say) neither fails preparation nor counts as a failed
    download that would start uv's own one.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # See above: the directory cleanup removes it.


def _fetch_accepted(
    url: str, directory: Path, *, maximum_bytes: int, phase: ProgressPhase,
    accept: Callable[[Path], bool], report: Callable[[ProgressEvent], None],
    cancel: threading.Event, deadline: float,
) -> None:
    """Download ``url`` over the market's routes until ``accept`` takes a file.

    ``accept(path)`` checks the file against a trusted digest and returns
    False when its content is wrong; it owns the file once called. A file
    that one route delivered whole and that is rejected is blamed on that
    route: it is penalized in the shared route history and skipped. A
    rejected file spliced from several routes (one resumed after another
    failed) blames none of them. After any rejection the remaining routes
    are tried one at a time, so each later file has a single source and
    every attempt rules a route out.

    The first download's failure propagates for the caller to map. Once a
    file has been rejected, running out of routes is ``checksum_mismatch``;
    ``download_failed`` when every rejected file came only from mirrors,
    which commonly answer with an error page rather than tampered bytes.
    """
    from plugin.server.infrastructure.package_fetch import NoRoutesLeft, RouteFailed
    from plugin.server.infrastructure.package_sources import GITHUB_DIRECT_SOURCE_ID

    excluded: list[str] = []
    rejected: list[str] = []
    rejected_sources: list[tuple[str, ...]] = []
    while True:
        try:
            fetched = _fetch_release_asset(
                url, directory, maximum_bytes=maximum_bytes, deadline=deadline,
                check_stopped=lambda: _check(cancel, deadline),
                progress=lambda received, total: report(ProgressEvent(phase, received, total)),
                exclude=excluded, single_route=bool(rejected),
            )
        except RouteFailed as exc:
            # Only raised one route at a time. The transfer has penalized the
            # route, except for an oversized file: skipped here either way.
            excluded.append(exc.source_id)
            continue
        except _download_failures() as exc:
            from plugin.server.infrastructure.package_download import PackageDownloadWriteError

            # A local write failure is reported as such, rejections or not.
            if not rejected or isinstance(exc, PackageDownloadWriteError):
                raise
            _check(cancel, deadline)
            last = exc if isinstance(exc, NoRoutesLeft) else f"{type(exc).__name__}: {exc}"
            code: ErrorCode = (
                # Only a file GitHub delivered whole counts as GitHub serving wrong bytes.
                "download_failed" if all(sources != (GITHUB_DIRECT_SOURCE_ID,) for sources in rejected_sources)
                else "checksum_mismatch"
            )
            raise ToolRuntimeError(
                code,
                f"{url.rsplit('/', 1)[-1]} failed its integrity check ({', '.join(rejected)}) "
                f"and no other route delivered it ({last})",
            ) from exc
        try:
            if accept(fetched.path):
                return
        finally:
            _discard_staged(fetched.path)
        source = fetched.single_source
        rejected.append(source or "+".join(fetched.sources))
        rejected_sources.append(fetched.sources)
        if source is not None:
            _record_route_failure(source, fetched.generation, cancel, deadline)
            excluded.append(source)
        _check(cancel, deadline)


def _fetch_verified(
    url: str, directory: Path, *, expected_sha256: str, maximum_bytes: int, phase: ProgressPhase,
    report: Callable[[ProgressEvent], None], cancel: threading.Event, deadline: float,
) -> Path:
    """Download ``url`` over the market's routes and check it against a pinned digest.

    Only the pinned digest counts, never one offered by a mirror. Returns a
    file in ``directory`` that the caller owns.
    """
    verified = directory / "verified"

    def accept(path: Path) -> bool:
        if _file_sha256(path, cancel, deadline) != expected_sha256:
            return False
        os.replace(path, verified)
        return True

    _fetch_accepted(
        url, directory, maximum_bytes=maximum_bytes, phase=phase, accept=accept, report=report,
        cancel=cancel, deadline=deadline,
    )
    return verified


def _download_uv(
    destination: Path, report: Callable[[ProgressEvent], None], cancel: threading.Event, deadline: float,
) -> None:
    """Download and extract the pinned uv, mapping failures to error codes."""
    try:
        _fetch_uv(destination, report, cancel, deadline)
    except ToolRuntimeError:
        raise
    except _download_failures() as exc:
        # A network timeout at the deadline is the task's timeout, not a
        # download failure that should fall back to another uv.
        try:
            _check(cancel, deadline)
        except ToolRuntimeError as stopped:
            raise stopped from exc
        raise ToolRuntimeError(
            "download_failed", f"Could not download uv {PINNED_UV_VERSION}: {type(exc).__name__}: {exc}",
        ) from exc


def _extract_member(
    archive_path: Path, target: str, member_name: str, output: Path,
    cancel: threading.Event, deadline: float,
) -> None:
    """Copy one archive member to ``output``; nothing else is extracted."""

    def copy(source, size: int) -> None:
        if size > _MAX_BINARY_BYTES:
            raise ToolRuntimeError("download_failed", "uv executable exceeds its size limit")
        with output.open("wb") as handle:
            while chunk := source.read(65536):
                _check(cancel, deadline)
                handle.write(chunk)

    if target.endswith("-windows-msvc"):
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo(member_name)
            with archive.open(info) as source:
                copy(source, info.file_size)
        return
    with tarfile.open(archive_path, "r:gz") as archive:
        info = archive.getmember(member_name)
        if not info.isfile():
            raise ValueError(f"{member_name} in the uv archive is not a regular file")
        source = archive.extractfile(info)
        if source is None:
            raise ValueError(f"{member_name} in the uv archive cannot be read")
        with source:
            copy(source, info.size)


def _fetch_uv(
    destination: Path, report: Callable[[ProgressEvent], None], cancel: threading.Event, deadline: float,
) -> None:
    """Install the pinned uv from its GitHub release, checked against the pinned SHA-256.

    The archive travels over the plugin market's routes (GitHub directly or
    one of its mirrors, by the user's market download preference), so the
    digest pinned in this module is the only trust anchor.
    """
    _check(cancel, deadline)
    if _UV_ARCHIVE_SHA256_VERSION != PINNED_UV_VERSION:
        # Digests of another release would reject every download as tampered.
        raise ToolRuntimeError(
            "internal_error",
            f"The pinned uv digests are for {_UV_ARCHIVE_SHA256_VERSION}, not uv {PINNED_UV_VERSION}",
        )
    target = _uv_target()
    if target is None:
        raise ToolRuntimeError("unsupported_platform", f"uv {PINNED_UV_VERSION} has no build for this platform")
    archive_name = _uv_archive_name(target)
    report(ProgressEvent("downloading_uv", 0, None))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".uv-", dir=destination.parent) as temporary:
        archive_path = _fetch_verified(
            f"{_UV_RELEASE_URL}/{archive_name}", Path(temporary), expected_sha256=_UV_ARCHIVE_SHA256[target],
            maximum_bytes=_MAX_ARCHIVE_BYTES, phase="downloading_uv", report=report, cancel=cancel,
            deadline=deadline,
        )
        binary = Path(temporary) / destination.name
        _extract_member(
            archive_path, target, _uv_archive_member(target, destination.name), binary, cancel, deadline,
        )
        _check(cancel, deadline)
        binary.chmod(0o755)
        os.replace(binary, destination)


def _system_uv_candidates() -> list[str]:
    """uv installations outside the tools root, in lookup order. No child process."""
    directories: list[Path] = []
    if sys.platform in {"darwin", "linux"}:
        # Desktop launchers start outside a login shell, so the usual user
        # install locations are often missing from PATH.
        directories.append(Path.home() / ".local" / "bin")
        if sys.platform == "darwin":
            directories.extend([Path("/opt/homebrew/bin"), Path("/usr/local/bin")])
    found: list[str] = []
    candidates = [shutil.which(os.environ["UV"]) if os.environ.get("UV") else None, shutil.which("uv")]
    for directory in directories:
        candidates.append(shutil.which("uv", path=str(directory)))
    for candidate in candidates:
        if candidate:
            path = os.path.abspath(candidate)
            if path not in found:
                found.append(path)
    return found


def _uv_version(
    uv: str, run_command: CommandRunner, env: dict[str, str], cancel: threading.Event, deadline: float,
) -> str | None:
    try:
        result = run_command([uv, "--version"], label="Check uv version", env=env, cancel=cancel, deadline=deadline)
    except OSError:
        return None
    parts = result.stdout.split()
    if result.returncode or len(parts) < 2 or parts[0] != "uv":
        return None
    try:
        return parts[1] if Version(parts[1]) >= Version(PINNED_UV_VERSION) else None
    except InvalidVersion:
        return None


def _compatible_system_uv(
    run_command: CommandRunner, env: dict[str, str], cancel: threading.Event, deadline: float,
) -> tuple[str, str] | None:
    # The bootstrap is tested against the pinned version; older PATH installs
    # may lack the python subcommands or this host's CPython build.
    for candidate in _system_uv_candidates():
        _check(cancel, deadline)
        version = _uv_version(candidate, run_command, env, cancel, deadline)
        if version is not None:
            return candidate, version
    return None


def _probe_identity(stdout: str) -> list[object] | None:
    try:
        identity = json.loads(stdout)
    except ValueError:
        return None
    return identity if isinstance(identity, list) and len(identity) == 5 else None


_PROBE = (
    "import json,sys,struct,sysconfig,platform; print(json.dumps([list(sys.version_info[:3]),"
    "sys.platform,struct.calcsize('P'),sysconfig.get_platform(),"
    "platform.machine() if sys.platform == 'darwin' else None]))"
)


@dataclass(frozen=True)
class _PythonDownload:
    url: str
    """The build on GitHub, where the market's routes can fetch it."""
    tag: str
    filename: str


def _plain_name(name: str) -> bool:
    """One path component on every platform: it cannot leave the mirror directory."""
    return bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


def _python_download(
    uv: str, request: str, run: Callable[[list[str], str], subprocess.CompletedProcess[str]],
) -> _PythonDownload | None:
    """Where the CPython build uv would install for ``request`` is published.

    Read from the list built into uv (``--offline``: nothing is fetched). None
    when uv cannot list it or names a host this module does not know.
    """
    listed = run(
        [uv, "python", "list", "--no-config", "--managed-python", "--only-downloads", "--show-urls",
         "--output-format", "json", "--offline", request],
        "List managed Python downloads",
    )
    if listed.returncode:
        return None
    try:
        entries = json.loads(listed.stdout)
    except ValueError:
        return None
    version = ".".join(str(part) for part in sys.version_info[:3])
    for entry in entries if isinstance(entries, list) else ():
        if not (
            isinstance(entry, dict) and entry.get("implementation") == "cpython"
            and entry.get("version") == version and entry.get("variant") in (None, "default")
            and isinstance(entry.get("url"), str)
        ):
            continue
        for prefix in _PYTHON_RELEASE_PREFIXES:
            if entry["url"].startswith(prefix):
                tag, _, filename = entry["url"][len(prefix):].partition("/")
                name = unquote(filename)
                if _plain_name(tag) and "/" not in filename and _plain_name(name):
                    return _PythonDownload(_PYTHON_GITHUB_PREFIX + f"{tag}/{filename}", tag, name)
        return None
    return None


def _rejected_python_archive(installed: subprocess.CompletedProcess[str]) -> bool:
    """Whether uv refused the downloaded file itself, rather than failing to install it.

    uv 0.13.6 extracts while hashing: a file that is not the build (an error
    page, a truncated archive) fails extraction, and a well-formed archive
    with other content fails the hash. Extraction failing on a local write
    carries an OS error and is an install failure.
    """
    output = f"{installed.stderr}\n{installed.stdout}".lower()
    if "hash mismatch" in output:
        return True
    return "failed to extract archive" in output and "os error" not in output


class _MirrorInstallFailed(Exception):
    """uv could not install from the ``file://`` mirror for a reason other than the file."""


def _install_downloaded_python(
    download: _PythonDownload, install: list[str],
    run: Callable[[list[str], str], subprocess.CompletedProcess[str]],
    report: Callable[[ProgressEvent], None], cancel: threading.Event, deadline: float,
) -> None:
    """Fetch the CPython build over the market's routes, then let uv install that file.

    uv reads it through a ``file://`` mirror and checks it against the
    SHA-256 built into uv (pinned with uv's own version, whose archive was
    checked against the digest pinned here); a mismatch fails the install
    and nothing falls back to the network. A file uv rejects is handled as
    in :func:`_fetch_accepted`: its route is penalized and another tried.
    When no route delivers an acceptable file, or uv fails to install from
    the mirror for another reason (the mirror path itself may be what it
    cannot handle), uv downloads the build from its own default host, as it
    did before these routes were used. A local failure to stage the file
    does not fall back.
    """
    downloads = tools_root() / ".python-download"
    shutil.rmtree(downloads, ignore_errors=True)
    mirror = downloads / "mirror"
    target = mirror / download.tag / download.filename

    def accept(path: Path) -> bool:
        try:
            os.replace(path, target)
        except OSError as exc:
            # A local failure, not a download one: no fallback either.
            raise ToolRuntimeError("python_install_failed", f"Could not stage the Python download: {exc}") from exc
        try:
            report(ProgressEvent("installing_python"))
            installed = run([*install, "--mirror", mirror.as_uri()], "Install managed Python")
            if not installed.returncode:
                return True
            if not _rejected_python_archive(installed):
                # Not the file's fault, so no other route is tried: uv's own
                # download is, in case the mirror is what uv cannot use.
                raise _MirrorInstallFailed(f"uv could not install {download.filename}: {_tail(installed)}")
            return False
        finally:
            _discard_staged(target)

    from plugin.server.infrastructure.package_download import PackageDownloadWriteError

    try:
        # The removal above ignores errors: a leftover directory is reused.
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _fetch_accepted(
                download.url, downloads, maximum_bytes=_MAX_PYTHON_ARCHIVE_BYTES, phase="installing_python",
                accept=accept, report=report, cancel=cancel, deadline=deadline,
            )
            return
        except ToolRuntimeError as exc:
            if exc.code not in ("checksum_mismatch", "download_failed"):
                raise
            market_failure: BaseException = exc
        except _MirrorInstallFailed as exc:
            market_failure = exc
        except PackageDownloadWriteError as exc:
            raise ToolRuntimeError(
                "python_install_failed", f"Could not save the Python download: {exc}",
            ) from exc
        except _download_failures() as exc:
            _check(cancel, deadline)
            market_failure = exc
    except OSError as exc:
        raise ToolRuntimeError("python_install_failed", f"Could not stage the Python download: {exc}") from exc
    finally:
        shutil.rmtree(downloads, ignore_errors=True)
    # No route delivered a file uv accepted: install as before these routes
    # existed, with uv downloading from its own default host. uv still checks
    # the build against the SHA-256 it carries.
    _check(cancel, deadline)
    report(ProgressEvent("installing_python"))
    installed = run(install, "Install managed Python")
    if installed.returncode:
        raise ToolRuntimeError(
            "python_install_failed",
            f"Could not install {download.filename} over the download routes "
            f"({type(market_failure).__name__}: {market_failure}); uv's own download failed too: {_tail(installed)}",
        )


def _prepare_python(
    uv: str, uv_version: str, run_command: CommandRunner, report: Callable[[ProgressEvent], None],
    env: dict[str, str], cancel: threading.Event, deadline: float,
) -> ToolRuntime:
    python_root = python_install_dir()
    architecture = _host_architecture()
    request = packaged_python_request()
    find = [
        uv, "python", "find", "--no-config", "--system", "--no-project", "--managed-python",
        "--no-python-downloads", request,
    ]

    def run(cmd: list[str], label: str) -> subprocess.CompletedProcess[str]:
        try:
            return run_command(cmd, label=label, env=env, cancel=cancel, deadline=deadline)
        except OSError as exc:
            raise ToolRuntimeError("python_install_failed", f"Could not run {label.lower()}: {exc}") from exc

    def reinstall() -> None:
        _check(cancel, deadline)
        # uv answers "already installed" for an existing but broken interpreter
        # (emptied, or stripped of its executable bit) and leaves it alone, so
        # `find` would keep failing. Only interpreters this module installed
        # live here, under the in-process flight: start from an empty directory.
        try:
            shutil.rmtree(python_root)
        except FileNotFoundError:
            pass  # Nothing installed yet.
        except OSError as exc:
            raise ToolRuntimeError(
                "python_install_failed", f"Could not clear the managed Python directory: {exc}",
            ) from exc
        install = [uv, "python", "install", "--no-config", request, "--install-dir", str(python_root),
                   "--no-bin", "--no-registry"]
        download = _python_download(uv, request, run)
        if download is None:
            # A uv whose list this module cannot read downloads by itself.
            installed = run(install, "Install managed Python")
            if installed.returncode:
                raise ToolRuntimeError("python_install_failed", f"uv could not install {request}: {_tail(installed)}")
            return
        _install_downloaded_python(download, install, run, report, cancel, deadline)

    def locate() -> str:
        found = run(find, "Locate managed Python")
        if found.returncode or not found.stdout.strip():
            raise ToolRuntimeError("python_install_failed", f"uv could not locate {request} after installing it")
        return found.stdout.strip().splitlines()[-1]

    def probe(python: str) -> tuple[list[object] | None, str]:
        """(identity, why it could not be read)."""
        _check(cancel, deadline)
        report(ProgressEvent("checking_python"))
        try:
            result = run_command(
                [python, "-I", "-c", _PROBE], label="Check managed Python", env=env, cancel=cancel, deadline=deadline,
            )
        except OSError as exc:
            return None, f"could not start it: {exc}"
        if result.returncode:
            return None, f"exit {result.returncode}: {_tail(result)}"
        identity = _probe_identity(result.stdout)
        return identity, "" if identity is not None else f"unreadable output: {_tail(result)}"

    report(ProgressEvent("installing_python"))
    found = run(find, "Locate managed Python")
    fresh = bool(found.returncode or not found.stdout.strip())
    if fresh:
        reinstall()
        python = locate()
    else:
        python = found.stdout.strip().splitlines()[-1]
    identity, problem = probe(python)
    if identity is None and not fresh:
        # uv can still list an interpreter that no longer starts (a quarantined
        # DLL, say): replace it once, as when `find` fails.
        reinstall()
        python = locate()
        identity, problem = probe(python)
    if identity is None:
        # A freshly installed interpreter that does not run is an install
        # failure, not a host mismatch.
        raise ToolRuntimeError("python_install_failed", f"The managed Python {request} does not run ({problem})")
    expected = [list(sys.version_info[:3]), sys.platform, struct.calcsize("P")]
    if (
        identity[:3] != expected
        or not isinstance(identity[3], str)
        or _python_architecture(identity[3], identity[4] if isinstance(identity[4], str) else None) != architecture
    ):
        raise ToolRuntimeError("python_mismatch", f"The managed Python does not match the running host ({request})")
    from utils.file_utils import atomic_write_json

    _check(cancel, deadline)
    atomic_write_json(_python_record_path(), {
        "executable": python,
        "uv": uv,
        "uv_version": uv_version,
        "environment": build_environment(),
        "python_version": list(sys.version_info[:3]),
        "python_architecture": architecture,
    })
    return ToolRuntime(python, uv, env, tools_root(), True)


def _ensure_uv(
    run_command: CommandRunner, report: Callable[[ProgressEvent], None], env: dict[str, str],
    cancel: threading.Event, deadline: float, *, prefer_system_uv: bool,
) -> tuple[str, str]:
    if prefer_system_uv:
        report(ProgressEvent("checking_uv"))
        system = _compatible_system_uv(run_command, env, cancel, deadline)
        if system is not None:
            return system
    cached = cached_uv_path()
    if cached.is_file():
        if _usable_executable(str(cached)) and _uv_version(str(cached), run_command, env, cancel, deadline):
            return str(cached), PINNED_UV_VERSION
        # Damaged (or stripped of its executable bit): download it again.
        cached.unlink()
    try:
        _download_uv(cached, report, cancel, deadline)
    except ToolRuntimeError as exc:
        # A failed download (mirrors only serving rejected files included), or
        # no pinned archive for this platform, falls back; a file from GitHub
        # itself failing the pinned checksum is not retried with whatever
        # else is installed.
        if exc.code not in ("download_failed", "unsupported_platform") or prefer_system_uv:
            raise
        report(ProgressEvent("checking_uv"))
        system = _compatible_system_uv(run_command, env, cancel, deadline)
        if system is None:
            raise
        return system
    return str(cached), PINNED_UV_VERSION


def _acquire_flight(cancel: threading.Event, deadline: float) -> None:
    """Wait for any other preparation in this process, honouring cancel and deadline."""
    while True:
        _check(cancel, deadline)
        if _flight.acquire(timeout=_POLL_SECONDS):
            return


def _prepare_in_flight(
    run_command: CommandRunner, report: Callable[[ProgressEvent], None],
    cancel: threading.Event, deadline: float, *, prefer_system_uv: bool,
) -> ToolRuntime:
    """Prepare the tools; the caller holds the in-process flight.

    Only this process prepares them: a packaged build runs one backend per
    user (the desktop shell and the launcher each hold a single-instance
    lock), and the plugin server is the only part of it that prepares.
    """
    prepared = _prepared_runtime()
    if prepared is not None:
        # Another thread finished while this one waited.
        return prepared
    tools_root().mkdir(parents=True, exist_ok=True)
    env = tool_runtime_env()
    uv, uv_version = _ensure_uv(run_command, report, env, cancel, deadline, prefer_system_uv=prefer_system_uv)
    return _prepare_python(uv, uv_version, run_command, report, env, cancel, deadline)


def _record_outcome(error: ToolRuntimeError | None) -> None:
    global _last_error, _progress
    with _state_lock:
        _progress = None
        # A cancel is the caller's choice, not a failure to report, and it
        # supersedes the outcome of any earlier attempt.
        _last_error = None if error is not None and error.code == "canceled" else error


def _unexpected(exc: Exception) -> ToolRuntimeError:
    from plugin.logging_config import get_logger

    get_logger("server.infrastructure.plugin_tool_runtime").exception(
        "plugin tool preparation failed unexpectedly",
    )
    return ToolRuntimeError("internal_error", f"{type(exc).__name__}: {exc}")


def _record_unstarted(error: ToolRuntimeError) -> None:
    """Record an attempt that stopped before it took the in-process flight.

    Only when no other preparation is under way: that one reports its own
    progress and outcome, and an unstarted attempt must not overwrite them.
    A foreground caller whose deadline had already passed did not attempt
    anything and keeps the previous state; the background preparation has no
    caller to tell, so its timeout is recorded.
    """
    global _last_error
    with _state_lock:
        is_background = _on_background_thread()
        if error.code == "timeout" and not is_background:
            return
        if _flight.locked() or (not is_background and _background_alive()):
            return
        _last_error = None if error.code == "canceled" else error


# ---------------------------------------------------------------------------
# Public preparation API


def prepare_tool_runtime(
    *,
    cancel: threading.Event,
    deadline: float,
    progress: Callable[[ProgressEvent], None] | None = None,
    prefer_system_uv: bool = False,
    run_command: CommandRunner | None = None,
) -> ToolRuntime:
    """Return a usable runtime, preparing the packaged tools first if needed.

    Call from a worker thread. ``deadline`` is a ``time.monotonic()`` value.
    Single-flight within the process: a call made while another thread
    prepares waits for it (cancel and deadline still apply) and then uses
    what it prepared. A compatible uv
    on PATH is used only when downloading the pinned one fails, or first with
    ``prefer_system_uv``. ``progress`` is called on this thread. Raises
    :class:`ToolRuntimeError`.
    """
    if not _is_packaged():
        return _source_runtime()
    if sys.platform not in _SUPPORTED_PLATFORMS:
        raise ToolRuntimeError("unsupported_platform", f"Plugin tools are not available on {sys.platform}")
    try:
        _check(cancel, deadline)
        prepared = _prepared_runtime()
        if prepared is not None:
            return prepared
        _acquire_flight(cancel, deadline)
    except ToolRuntimeError as exc:
        _record_unstarted(exc)
        raise
    except Exception as exc:
        unexpected = _unexpected(exc)
        _record_unstarted(unexpected)
        raise unexpected from exc
    runner = _run_command if run_command is None else run_command
    report = _Reporter(progress)
    outcome: ToolRuntimeError | None = None
    try:
        result = _prepare_in_flight(runner, report, cancel, deadline, prefer_system_uv=prefer_system_uv)
    except ToolRuntimeError as exc:
        outcome = exc
        raise
    except Exception as exc:
        outcome = _unexpected(exc)
        raise outcome from exc
    finally:
        _record_outcome(outcome)
        _flight.release()
    return result


async def ensure_tool_runtime(
    *,
    timeout: float,
    progress: Callable[[ProgressEvent], None] | None = None,
    prefer_system_uv: bool = False,
) -> ToolRuntime:
    """asyncio wrapper of :func:`prepare_tool_runtime`; cancelling the awaiting
    task cancels the preparation. ``progress`` runs on the worker thread.

    The preparation runs on a thread of its own, not in the loop's default
    executor: calls waiting behind another one's preparation would otherwise
    hold the executor's threads that the download, running on this loop,
    needs for its file operations.
    """
    if not _is_packaged():
        return _source_runtime()
    cancel = threading.Event()
    deadline = time.monotonic() + timeout
    loop = asyncio.get_running_loop()
    future: asyncio.Future[ToolRuntime] = loop.create_future()

    def settle(result: ToolRuntime | None, error: BaseException | None) -> None:
        if future.done():
            return  # The caller was cancelled.
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)  # type: ignore[arg-type]

    def work() -> None:
        result: ToolRuntime | None = None
        error: BaseException | None = None
        try:
            result = prepare_tool_runtime(
                cancel=cancel, deadline=deadline, progress=progress, prefer_system_uv=prefer_system_uv,
            )
        except BaseException as exc:
            # Everything is handed to the awaiting task, as concurrent.futures
            # does: anything left uncaught would end this thread silently and
            # leave the caller awaiting a future nothing settles.
            error = exc
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:
            pass  # The loop is closed: nobody awaits the outcome any more.

    threading.Thread(target=work, name="plugin-tool-runtime-call", daemon=True).start()
    try:
        return await future
    except asyncio.CancelledError:
        cancel.set()
        raise


def _background_main(cancel: threading.Event, deadline: float) -> None:
    try:
        prepare_tool_runtime(cancel=cancel, deadline=deadline)
    except ToolRuntimeError:
        # prepare_tool_runtime() already recorded it for read_tool_runtime_status().
        return


def start_tool_runtime_preparation(*, timeout: float = _DEFAULT_BACKGROUND_TIMEOUT) -> RuntimeStatus:
    """Start one background preparation when the packaged tools are missing or failed."""
    global _background
    with _state_lock:
        start = not _background_alive() and not _flight.locked()
    if start and read_tool_runtime_status().state in ("missing", "failed"):
        with _state_lock:
            if not _background_alive() and not _flight.locked():
                cancel = threading.Event()
                thread = threading.Thread(
                    target=_background_main, args=(cancel, time.monotonic() + timeout),
                    name="plugin-tool-runtime", daemon=True,
                )
                _background = (thread, cancel)
                thread.start()
    return read_tool_runtime_status()


def cancel_tool_runtime_preparation() -> bool:
    """Cancel the background preparation; False when none is running."""
    with _state_lock:
        background = _background
    if background is None or not background[0].is_alive():
        return False
    background[1].set()
    return True
