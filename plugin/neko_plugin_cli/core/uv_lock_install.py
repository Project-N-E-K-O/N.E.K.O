"""Install a plugin's locked Python dependencies into vendor/ with uv.

One set of uv arguments shared by the host (installing a plugin that ships
``pyproject.toml`` + ``uv.lock`` without ``vendor/``) and the developer CLI.
uv does all the real work -- reading the lock, evaluating markers, choosing,
downloading and hash-checking wheels, installing them -- and this module only
builds its command lines, reads what it reports, and records which lock a
``vendor/`` was installed from. It depends on nothing in the server layer.

The commands, in order::

    uv export ...                 # the lock as requirements.txt with hashes
    uv pip install --dry-run ...  # what would be installed for this Python
    uv pip install ...            # into a staging directory, swapped in after

Every switch is deliberate:

* ``--no-config``: a plugin's own uv.toml / ``[tool.uv]`` must not choose the
  index or loosen any of the switches below.
* ``--frozen``: export the lock as it is, without resolving again.
* ``--no-default-groups``: runtime dependencies only (``--no-dev`` would keep
  groups a plugin lists in ``default-groups``).
* ``--no-emit-project`` / ``--no-emit-workspace``: the plugin itself is not a
  dependency. Local path or git dependencies are *not* dropped: they carry no
  hash, so ``--require-hashes`` rejects them instead of skipping them silently.
* ``--require-hashes``: every wheel must match the lock's digest.
* ``--only-binary :all:``: wheels only, nothing is built on the user's machine.
* ``--no-deps``: the export already lists every transitive dependency.
* ``--python <interpreter>``: the interpreter the plugin will run on; uv takes
  the Python version and platform tags from it, so no ``--python-platform``
  (which would override uv's own glibc / macOS detection).
* ``--no-python-downloads``: the interpreter is given, never downloaded here.
* ``--link-mode copy``: vendor/ shares no hard links with the uv cache.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import sysconfig

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from packaging.tags import Tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

LOCK_FILE_NAME = "uv.lock"
VENDOR_DIR_NAME = "vendor"
# Written into vendor/ after a successful install. Its presence is what says
# "the host installed this vendor/ from the lock"; a vendor/ shipped in the
# package or copied in by hand never has it.
LOCK_MARKER_NAME = ".neko-uv-lock.json"
_MARKER_SCHEMA = 1

DEFAULT_PYPI_INDEX_URL = "https://pypi.org/simple"

# Index variables a developer environment may carry. They are replaced by the
# one index given to lock_install_env(), so which index uv uses never depends
# on which of these happened to be set.
_INDEX_ENV_VARS = frozenset({
    "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS", "UV_NO_INDEX", "UV_INDEX_STRATEGY",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS", "PIP_NO_INDEX",
})

# uv prints one "+ name==version" line per package it would install / installed.
_PLAN_LINE_RE = re.compile(r"^\s*\+\s+([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)\s*$")


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    size: int | None
    """Compressed size of the wheel chosen for this host, from uv.lock; None if unknown."""


def lock_path(plugin_dir: Path) -> Path:
    return plugin_dir / LOCK_FILE_NAME


def has_lock(plugin_dir: Path) -> bool:
    return lock_path(plugin_dir).is_file()


def lock_sha256(plugin_dir: Path) -> str:
    """SHA-256 of the plugin's uv.lock bytes. Raises OSError when unreadable."""
    return hashlib.sha256(lock_path(plugin_dir).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Commands


def uv_export_command(uv: str, project_dir: Path) -> list[str]:
    """``uv export`` printing the locked runtime dependencies, with hashes, to stdout."""
    return [
        uv, "export",
        "--no-config", "--frozen", "--no-default-groups",
        "--no-emit-project", "--no-emit-workspace",
        "--no-python-downloads",
        "--format", "requirements.txt", "--no-header", "--no-annotate",
        "--quiet", "--color", "never",
        "--directory", str(project_dir),
    ]


def uv_install_command(
    uv: str, python: str, requirements_file: Path, target: Path, *, dry_run: bool = False,
) -> list[str]:
    """``uv pip install`` of an exported lock into ``target`` (a staging directory)."""
    command = [
        uv, "pip", "install",
        "--no-config", "--no-python-downloads",
        "--python", python,
        "--require-hashes", "--only-binary", ":all:", "--no-deps",
        "--link-mode", "copy",
        "--color", "never",
        "--target", str(target),
        "-r", str(requirements_file),
    ]
    if dry_run:
        command.insert(3, "--dry-run")
    return command


def lock_install_env(base: Mapping[str, str], index_url: str) -> dict[str, str]:
    """``base`` with exactly one package index, handed to uv as ``UV_DEFAULT_INDEX``.

    Environment variables are not configuration files: ``--no-config`` leaves
    them in effect, which is how the index reaches uv. Any other index
    setting in ``base`` is dropped (compared case-insensitively, as Windows does).
    """
    env = {name: value for name, value in base.items() if name.upper() not in _INDEX_ENV_VARS}
    env["UV_DEFAULT_INDEX"] = index_url
    env["NO_COLOR"] = "1"
    return env


# ---------------------------------------------------------------------------
# Reading uv's output and the lock


def parse_install_plan(output: str) -> list[tuple[str, str]]:
    """(name, version) of every ``+ name==version`` line uv printed, in order."""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _PLAN_LINE_RE.match(line)
        if match is None:
            continue
        name = canonicalize_name(match.group(1))
        if name in seen:
            continue
        seen.add(name)
        result.append((name, match.group(2)))
    return result


def _wheel_rank(filename: str, ranks: Mapping[Tag, int]) -> int | None:
    try:
        _name, _version, _build, tags = parse_wheel_filename(filename)
    except Exception:
        return None
    found = [ranks[tag] for tag in tags if tag in ranks]
    return min(found) if found else None


def locked_wheel_sizes(
    lock_data: Mapping[str, object],
    packages: Iterable[tuple[str, str]],
    *,
    tags: Iterable[Tag] | None = None,
) -> list[LockedPackage]:
    """Attach the size of the wheel this host would get to each (name, version).

    uv does not report it: ``uv tree --show-sizes`` (0.13.6) shows one wheel
    per package whatever the platform. The lock lists every wheel with its
    size, so pick the one ranked first by this interpreter's tags -- the host
    and the interpreter the plugin runs on share version and architecture.
    """
    ranks: dict[Tag, int] = {}
    for index, tag in enumerate(sys_tags() if tags is None else tags):
        ranks.setdefault(tag, index)
    entries: dict[tuple[str, str], list[object]] = {}
    raw_packages = lock_data.get("package")
    for package in raw_packages if isinstance(raw_packages, list) else []:
        if not isinstance(package, dict):
            continue
        name, version = package.get("name"), package.get("version")
        wheels = package.get("wheels")
        if isinstance(name, str) and isinstance(version, str) and isinstance(wheels, list):
            entries[(canonicalize_name(name), version)] = wheels
    result: list[LockedPackage] = []
    for name, version in packages:
        best: tuple[int, int | None] | None = None
        for wheel in entries.get((canonicalize_name(name), version), []):
            if not isinstance(wheel, dict):
                continue
            url = wheel.get("url") or wheel.get("filename") or wheel.get("path")
            if not isinstance(url, str):
                continue
            rank = _wheel_rank(url.rsplit("/", 1)[-1].split("#", 1)[0], ranks)
            if rank is None:
                continue
            size = wheel.get("size")
            if best is None or rank < best[0]:
                best = (rank, size if isinstance(size, int) and size >= 0 else None)
        result.append(LockedPackage(canonicalize_name(name), version, None if best is None else best[1]))
    return result


def read_lock(plugin_dir: Path) -> dict[str, object]:
    """Parse uv.lock. Raises OSError or ValueError (tomllib errors included)."""
    with lock_path(plugin_dir).open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("uv.lock root must be a table")
    return data


# ---------------------------------------------------------------------------
# vendor/ marker


def runtime_python_tag() -> str:
    """What compiled wheels in vendor/ depend on: Python minor version and platform.

    The interpreter a plugin runs on matches the host process (version and
    architecture), so the host's own values describe it.
    """
    return f"cp{sys.version_info.major}{sys.version_info.minor}-{sysconfig.get_platform()}"


def write_lock_marker(vendor_dir: Path, lock_digest: str, *, python: str | None = None) -> None:
    payload = {
        "schema": _MARKER_SCHEMA,
        "lock_sha256": lock_digest,
        "python": runtime_python_tag() if python is None else python,
    }
    (vendor_dir / LOCK_MARKER_NAME).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _read_marker(vendor_dir: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((vendor_dir / LOCK_MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    digest = payload.get("lock_sha256")
    if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
        return None
    return payload


def read_lock_marker(vendor_dir: Path) -> str | None:
    """The lock digest vendor/ was installed from; None when it has no valid marker."""
    payload = _read_marker(vendor_dir)
    return None if payload is None else payload["lock_sha256"]  # type: ignore[return-value]


def vendor_installed_from_lock(plugin_dir: Path) -> bool:
    """Whether vendor/ exists and was installed by this module (it has a valid marker)."""
    vendor_dir = plugin_dir / VENDOR_DIR_NAME
    return vendor_dir.is_dir() and read_lock_marker(vendor_dir) is not None


def vendor_absent(plugin_dir: Path) -> bool:
    """No vendor/, or an empty directory (an archive can carry an explicit empty one)."""
    vendor_dir = plugin_dir / VENDOR_DIR_NAME
    if not vendor_dir.exists():
        return True
    try:
        return vendor_dir.is_dir() and not any(vendor_dir.iterdir())
    except OSError:
        return False


def lock_install_applies(plugin_dir: Path) -> bool:
    """Whether missing dependencies of this plugin are this module's to install.

    Only a plugin with uv.lock whose vendor/ is absent (or empty) or was
    installed from a lock by the host. A vendor/ the package shipped (or a
    user copied in) is never replaced: such plugins keep their previous
    behavior. Neither is a plugin still declaring dependencies in the legacy
    requirements.txt, which packaging rejects and the lock cannot cover.
    """
    if not has_lock(plugin_dir) or (plugin_dir / "requirements.txt").exists():
        return False
    return vendor_absent(plugin_dir) or vendor_installed_from_lock(plugin_dir)


def lock_vendor_stale(plugin_dir: Path) -> bool:
    """Whether vendor/ was installed from the lock for another Python or platform.

    Its compiled wheels may not load after the host's Python changed, though
    the installed versions still satisfy the declared requirements.
    """
    marker = _read_marker(plugin_dir / VENDOR_DIR_NAME)
    return marker is not None and marker.get("python") != runtime_python_tag()


def unpinned_requirements(exported: str) -> list[str]:
    """Requirements of an exported lock that would not come from the package index.

    ``uv export`` writes ``name==version`` for registry packages and a URL or
    path for direct, git and local sources. Those are rejected before uv runs:
    uv would fetch such URLs (or follow their redirects) while planning,
    before the user confirmed anything, and they bypass the chosen index.
    """
    offending: list[str] = []
    for raw in exported.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.fullmatch(r"--hash=[a-z0-9]+:[0-9a-f]+(\s*\\)?", line):
            continue
        requirement = line.rstrip("\\").strip()
        name_part = requirement.split(";", 1)[0].strip()
        # PEP 440 characters only after ==, so no URL can hide in the version.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,\s-]*\])?==[A-Za-z0-9.!+_-]+", name_part):
            offending.append(name_part)
    return offending


def vendor_matches_lock(old_plugin_dir: Path, new_plugin_dir: Path) -> bool:
    """Whether the old install's vendor/ can serve the new one unchanged.

    True only when the new tree ships no vendor/ but a uv.lock, and the old
    vendor/ carries a marker (it was installed from a lock by the host) for a
    lock with the same digest and for this Python and platform. A shipped or
    hand-copied vendor/ never counts.
    """
    if not vendor_absent(new_plugin_dir) or not lock_install_applies(new_plugin_dir):
        return False
    old_vendor = old_plugin_dir / VENDOR_DIR_NAME
    if not old_vendor.is_dir():
        return False
    marker = _read_marker(old_vendor)
    if marker is None:
        return False
    if marker.get("python") != runtime_python_tag():
        # Installed for another Python or platform (the host was updated):
        # its compiled wheels may not load, so install again.
        return False
    try:
        return marker["lock_sha256"] == lock_sha256(new_plugin_dir)
    except OSError:
        return False
