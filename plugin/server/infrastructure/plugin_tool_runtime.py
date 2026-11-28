"""uv and Python for plugin tooling, shared by every packaged-build feature.

Source checkouts already have a Python: tooling runs on ``sys.executable``
with whatever uv is on PATH, or, when there is none, the same pinned uv a
packaged server downloads (pip cannot install from a uv.lock). A packaged (Nuitka) server has neither a usable
interpreter nor uv, so this module prepares them on demand under one app-owned
root::

    <app_docs_dir>/plugin-runtime/tools/
        uv/<version>/<os>-<arch>/uv[.exe]   pinned uv, downloaded from PyPI
        python/                             uv-managed CPython (UV_PYTHON_INSTALL_DIR)
        python.json                         record of the prepared interpreter
        .prepare.lock                       cross-process preparation lock
        envs/                               reserved for tool virtualenvs (not created here)

Preparation downloads the pinned uv (wheel chosen by this interpreter's tags,
SHA-256 checked against PyPI, size-capped, placed atomically), then installs
the CPython release matching the host's version and architecture exactly and
records it. A compatible uv on PATH is only an optional fallback.

Reading the status never starts a child process, touches the network or
creates a directory. Preparing runs in a worker thread, honours a cancel event
and a deadline, is single-flight within the process and serialized across
processes by a file lock polled without blocking, so cancellation stays
responsive while another process prepares.

Installing a plugin's locked dependencies (plugin_lock_dependencies.py)
builds on it; dependency sync and the plugin CLI tool environment are meant to.
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
import tempfile
import threading
import time
from typing import Literal, Protocol
from urllib.parse import urlsplit
import zipfile

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion, Version
import portalocker

from plugin.core.packaged_metadata import build_environment
from plugin.neko_plugin_cli.git_env import git_env
from plugin.utils.http_imports import load_httpx

PINNED_UV_VERSION = "0.13.6"
_PYPI_METADATA_URL = f"https://pypi.org/pypi/uv/{PINNED_UV_VERSION}/json"
_DOWNLOAD_HOST = "files.pythonhosted.org"
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_MAX_WHEEL_BYTES = 128 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_MAX_LICENSE_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_SUPPORTED_PLATFORMS = frozenset({"win32", "darwin", "linux"})
# How often lock waits re-check the cancel event and the deadline.
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
    "busy",
    "internal_error",
]
ProgressPhase = Literal["waiting", "downloading_uv", "checking_uv", "installing_python", "checking_python"]


class ToolRuntimeError(RuntimeError):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail
        # False when the status should keep its previous state, e.g. a timeout
        # spent waiting for another process that is preparing the same tools.
        self.reportable = True


@dataclass(frozen=True)
class ToolRuntime:
    python: str
    """``sys.executable`` from source; the managed CPython when packaged."""
    uv: str
    """From source: the uv on PATH, else the pinned uv downloaded under the tools root."""
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


def _source_runtime(uv: str) -> ToolRuntime:
    # A source checkout keeps the developer's uv configuration (UV_* included).
    return ToolRuntime(sys.executable, uv, _with_utf8(git_env()), None, False)


def _source_uv() -> str | None:
    # Same rules as the CLI's deps command: `uv run` exports its own path as UV.
    for candidate in (os.environ.get("UV"), "uv"):
        found = shutil.which(candidate) if candidate else None
        if found:
            return os.path.abspath(found)
    return None


def _cached_source_uv() -> str | None:
    """The pinned uv a source checkout downloaded earlier, when still usable. No child process."""
    root = _known_tools_root()
    if root is None:
        return None
    name = "uv.exe" if sys.platform == "win32" else "uv"
    path = root / "uv" / PINNED_UV_VERSION / f"{sys.platform}-{_host_architecture()}" / name
    return str(path) if _usable_executable(str(path)) else None


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
        # uv stays None until a preparation has downloaded the pinned one.
        return RuntimeStatus(
            "source", False, PINNED_UV_VERSION, python=sys.executable, uv=_source_uv() or _cached_source_uv(),
        )
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


# For callers running tools with a prepared runtime (same cancel / deadline rules).
run_tool_command: CommandRunner = _run_command


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    lines = ((result.stderr or "") + "\n" + (result.stdout or "")).strip().splitlines()
    return " ".join(lines[-3:])[-500:]


def _select_uv_wheel(metadata: dict, compatible_tags: list | None = None) -> dict:
    tags = list(sys_tags()) if compatible_tags is None else compatible_tags
    ranks = {tag: rank for rank, tag in enumerate(tags)}
    matches: list[tuple[int, dict]] = []
    for item in metadata.get("urls", []):
        if item.get("packagetype") != "bdist_wheel" or item.get("yanked"):
            continue
        try:
            name, version, _, wheel_tags = parse_wheel_filename(item["filename"])
        except (ValueError, KeyError):
            continue
        matching = [ranks[tag] for tag in wheel_tags if tag in ranks]
        if name == "uv" and str(version) == PINNED_UV_VERSION and matching:
            matches.append((min(matching), item))
    if not matches:
        raise ToolRuntimeError("unsupported_platform", f"uv {PINNED_UV_VERSION} has no wheel for this platform")
    return min(matches, key=lambda match: match[0])[1]


def _download_timeout(httpx, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ToolRuntimeError("timeout", "Plugin tool preparation exceeded its time budget")
    return httpx.Timeout(min(10.0, remaining), connect=min(5.0, remaining))


def _download_uv(
    destination: Path, report: Callable[[ProgressEvent], None], cancel: threading.Event, deadline: float,
) -> None:
    """Download and extract the pinned uv, mapping failures to error codes."""
    httpx = load_httpx()
    try:
        _fetch_uv(httpx, destination, report, cancel, deadline)
    except ToolRuntimeError:
        raise
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        # A network timeout at the deadline is the task's timeout, not a
        # download failure that should fall back to another uv.
        try:
            _check(cancel, deadline)
        except ToolRuntimeError as stopped:
            raise stopped from exc
        raise ToolRuntimeError(
            "download_failed", f"Could not download uv {PINNED_UV_VERSION}: {type(exc).__name__}: {exc}",
        ) from exc


def _fetch_uv(
    httpx, destination: Path, report: Callable[[ProgressEvent], None],
    cancel: threading.Event, deadline: float,
) -> None:
    """Extract pinned-version uv, checking integrity against PyPI's SHA-256.

    PyPI HTTPS metadata and its files.pythonhosted.org artifacts are trusted;
    the digest is supplied by PyPI, rather than embedded as a trust anchor.
    """
    _check(cancel, deadline)
    report(ProgressEvent("downloading_uv", 0, None))
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
        with client.stream("GET", _PYPI_METADATA_URL, timeout=_download_timeout(httpx, deadline)) as response:
            response.raise_for_status()
            metadata_bytes = bytearray()
            for chunk in response.iter_bytes():
                _check(cancel, deadline)
                metadata_bytes.extend(chunk)
                if len(metadata_bytes) > _MAX_METADATA_BYTES:
                    raise ToolRuntimeError("download_failed", "uv release metadata exceeds its size limit")
        _check(cancel, deadline)
        wheel = _select_uv_wheel(json.loads(metadata_bytes))
        address = urlsplit(wheel["url"])
        if address.scheme != "https" or address.hostname != _DOWNLOAD_HOST:
            raise ToolRuntimeError("download_failed", "Unexpected uv download origin")
        expected = wheel.get("digests", {}).get("sha256", "")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ToolRuntimeError("checksum_mismatch", "uv release metadata has no usable SHA-256")
        size = wheel.get("size")
        total = size if isinstance(size, int) and size > 0 else None
        if total is not None and total > _MAX_WHEEL_BYTES:
            raise ToolRuntimeError("download_failed", "uv download exceeds its size limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".uv-", dir=destination.parent) as temporary:
            archive_path = Path(temporary) / "uv.whl"
            digest = hashlib.sha256()
            received = 0
            with client.stream(
                "GET", wheel["url"], timeout=_download_timeout(httpx, deadline),
            ) as response, archive_path.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    _check(cancel, deadline)
                    received += len(chunk)
                    if received > _MAX_WHEEL_BYTES:
                        raise ToolRuntimeError("download_failed", "uv download exceeds its size limit")
                    digest.update(chunk)
                    output.write(chunk)
                    report(ProgressEvent("downloading_uv", received, total))
            if digest.hexdigest() != expected:
                raise ToolRuntimeError("checksum_mismatch", "uv download failed its SHA-256 check")
            binary = Path(temporary) / destination.name
            with zipfile.ZipFile(archive_path) as archive:
                member = archive.getinfo(f"uv-{PINNED_UV_VERSION}.data/scripts/{destination.name}")
                if member.file_size > _MAX_BINARY_BYTES:
                    raise ToolRuntimeError("download_failed", "uv executable exceeds its size limit")
                with archive.open(member) as source, binary.open("wb") as output:
                    while chunk := source.read(65536):
                        _check(cancel, deadline)
                        output.write(chunk)
                for name in archive.namelist():
                    if ".dist-info/licenses/" in name and not name.endswith("/"):
                        license_info = archive.getinfo(name)
                        if license_info.file_size <= _MAX_LICENSE_BYTES:
                            license_path = destination.parent / f"{Path(name).name}.txt"
                            license_path.write_bytes(archive.read(name))
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
        # live here, under the preparation lock: start from an empty directory.
        try:
            shutil.rmtree(python_root)
        except FileNotFoundError:
            pass  # Nothing installed yet.
        except OSError as exc:
            raise ToolRuntimeError(
                "python_install_failed", f"Could not clear the managed Python directory: {exc}",
            ) from exc
        installed = run(
            [uv, "python", "install", "--no-config", request, "--install-dir", str(python_root),
             "--no-bin", "--no-registry"],
            "Install managed Python",
        )
        if installed.returncode:
            raise ToolRuntimeError("python_install_failed", f"uv could not install {request}: {_tail(installed)}")

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
        # A failed download, or no pinned wheel for this platform, falls back;
        # a checksum mismatch is not retried with whatever else is installed.
        if exc.code not in ("download_failed", "unsupported_platform") or prefer_system_uv:
            raise
        report(ProgressEvent("checking_uv"))
        system = _compatible_system_uv(run_command, env, cancel, deadline)
        if system is None:
            raise
        return system
    return str(cached), PINNED_UV_VERSION


def _acquire_file_lock(lock: portalocker.Lock, cancel: threading.Event, deadline: float, *, wait: bool) -> None:
    # Never block inside the OS call: a cancel or the deadline must be noticed
    # while another process holds the lock.
    held_elsewhere = False
    while True:
        try:
            _check(cancel, deadline)
        except ToolRuntimeError as exc:
            if held_elsewhere and exc.code == "timeout" and not _on_background_thread():
                # A foreground caller gets the error itself, and the other
                # process may well finish; like busy, this says nothing about
                # the tools. The background preparation has no caller to tell.
                exc.reportable = False
            raise
        try:
            lock.acquire()
            return
        except portalocker.exceptions.LockException:
            if not wait:
                raise ToolRuntimeError("busy", "Another N.E.K.O. process is preparing plugin tools") from None
            held_elsewhere = True
        cancel.wait(_POLL_SECONDS)


def _acquire_flight(cancel: threading.Event, deadline: float, *, wait: bool) -> None:
    while True:
        _check(cancel, deadline)
        acquired = _flight.acquire(timeout=_POLL_SECONDS) if wait else _flight.acquire(blocking=False)
        if acquired:
            return
        if not wait:
            raise ToolRuntimeError("busy", "Plugin tools are already being prepared")


def _prepare_locked(
    run_command: CommandRunner, report: Callable[[ProgressEvent], None],
    cancel: threading.Event, deadline: float, *, wait: bool, prefer_system_uv: bool,
) -> ToolRuntime:
    prepared = _prepared_runtime()
    if prepared is not None:
        # Another thread finished while this one waited.
        return prepared
    root = tools_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(root / ".prepare.lock", timeout=0, fail_when_locked=True)
    _acquire_file_lock(lock, cancel, deadline, wait=wait)
    try:
        result = _prepared_runtime()
        if result is None:
            env = tool_runtime_env()
            uv, uv_version = _ensure_uv(
                run_command, report, env, cancel, deadline, prefer_system_uv=prefer_system_uv,
            )
            result = _prepare_python(uv, uv_version, run_command, report, env, cancel, deadline)
    finally:
        lock.release()
    return result


def _prepare_source_uv(
    run_command: CommandRunner, report: Callable[[ProgressEvent], None],
    cancel: threading.Event, deadline: float, *, wait: bool,
) -> str:
    """The pinned uv for a source checkout that has none on PATH.

    Downloaded and checked exactly as for a packaged server, under the same
    locks; the interpreter stays ``sys.executable``, so no Python is
    installed. A failed download falls back to a compatible uv in the usual
    install locations. The status is not recorded: a source checkout reports
    "source" whatever happens here. A cached uv is version-checked first (one
    ``uv --version``), so a damaged one is downloaded again instead of reused.
    """
    _acquire_flight(cancel, deadline, wait=wait)
    try:
        root = tools_root()
        root.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(root / ".prepare.lock", timeout=0, fail_when_locked=True)
        _acquire_file_lock(lock, cancel, deadline, wait=wait)
        try:
            uv, _version = _ensure_uv(
                run_command, report, tool_runtime_env(root=root), cancel, deadline, prefer_system_uv=False,
            )
        finally:
            lock.release()
    finally:
        _flight.release()
    return uv


def _record_outcome(error: ToolRuntimeError | None) -> None:
    global _last_error, _progress
    with _state_lock:
        _progress = None
        if error is not None and (error.code == "busy" or not error.reportable):
            # Another process is preparing; this attempt says nothing new.
            return
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
    if error.code == "busy":
        return
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
    wait_for_lock: bool = True,
    prefer_system_uv: bool = False,
    run_command: CommandRunner | None = None,
) -> ToolRuntime:
    """Return a usable runtime, preparing the packaged tools first if needed.

    Call from a worker thread. ``deadline`` is a ``time.monotonic()`` value.
    With ``wait_for_lock=False`` a preparation already running in this or
    another process raises ``busy`` instead of waiting for it. A compatible uv
    on PATH is used only when downloading the pinned one fails, or first with
    ``prefer_system_uv``. ``progress`` is called on this thread. Raises
    :class:`ToolRuntimeError`.
    """
    if not _is_packaged():
        source_uv = _source_uv()
        if source_uv is not None:
            return _source_runtime(source_uv)
        return _source_runtime(_prepare_source_uv(
            run_command or _run_command, _Reporter(progress), cancel, deadline, wait=wait_for_lock,
        ))
    if sys.platform not in _SUPPORTED_PLATFORMS:
        raise ToolRuntimeError("unsupported_platform", f"Plugin tools are not available on {sys.platform}")
    try:
        _check(cancel, deadline)
        prepared = _prepared_runtime()
        if prepared is not None:
            return prepared
        _acquire_flight(cancel, deadline, wait=wait_for_lock)
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
        report(ProgressEvent("waiting"))
        result = _prepare_locked(
            runner, report, cancel, deadline, wait=wait_for_lock, prefer_system_uv=prefer_system_uv,
        )
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
    wait_for_lock: bool = True,
    prefer_system_uv: bool = False,
) -> ToolRuntime:
    """asyncio wrapper of :func:`prepare_tool_runtime`; cancelling the awaiting
    task cancels the preparation. ``progress`` runs on the worker thread."""
    if not _is_packaged() and (source_uv := _source_uv()) is not None:
        return _source_runtime(source_uv)
    cancel = threading.Event()
    deadline = time.monotonic() + timeout
    try:
        return await asyncio.to_thread(
            prepare_tool_runtime, cancel=cancel, deadline=deadline, progress=progress,
            wait_for_lock=wait_for_lock, prefer_system_uv=prefer_system_uv,
        )
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
