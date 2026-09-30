"""neko-plugin sync — materialize declared Python dependencies in vendor/."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import gettempdir

import portalocker

from ..core.build_rules import (
    VENDOR_SYNC_BACKUP_PREFIX,
    VENDOR_SYNC_PENDING_SUFFIX,
    VENDOR_SYNC_STAGING_PREFIX,
)
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync vendor/ with all dependencies declared in pyproject.toml",
    )
    sync_plugin_arg = sync_parser.add_argument(
        "plugin",
        help="Plugin directory name or path",
    )
    sync_plugin_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    sync_parser.add_argument(
        "--python",
        default=sys.executable,
        help=(
            "Target Python interpreter; its pip installs the dependencies, "
            "or uv does when that interpreter has no pip"
        ),
    )
    sync_parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove vendor/ before reinstalling (fresh sync)",
    )
    sync_parser.set_defaults(handler=handle_sync, _defaults=defaults)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_sync(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    try:
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    vendor_dir = plugin_dir / "vendor"
    # Keep a persistent OS lock file outside the plugin. Unlinking lock files
    # can let waiting processes lock different inodes for the same plugin.
    identity = os.path.normcase(str(plugin_dir.resolve()))
    lock_name = hashlib.sha256(identity.encode()).hexdigest()
    staging_dir: Path | None = None
    try:
        # Inside the try: an unusable lock dir reports like any other OSError.
        lock_path = _lock_dir() / f"neko-plugin-sync-{lock_name}.lock"
        with portalocker.Lock(lock_path, timeout=0):
            # Holding the lock means no other sync of this plugin by this user
            # is running, so this user's staging dirs were left by killed runs.
            _remove_stale_staging(plugin_dir)

            pyproject_path = plugin_dir / "pyproject.toml"
            external_deps = (
                _filter_external(_read_dependencies(pyproject_path))
                if pyproject_path.is_file()
                else []
            )

            # A user's explicit `sync --clean` may discard an unreconciled
            # backup, but only when an install rebuilds vendor/; the
            # no-dependency path has nothing to reconcile it with. Callers
            # that clean by default (publish) pass discard_backups=False, so
            # the only full copy of the old vendor/ is never dropped silently.
            discard_backups = getattr(args, "discard_backups", args.clean)
            unreconciled = _unreconciled_backups(plugin_dir, vendor_dir)
            # A backup another user is mid-swap on is never removed here, so
            # --clean cannot clear it; say how to instead.
            foreign = [path for path in unreconciled if _swapped_by_other_user(path)]
            # Another user's sync may have finished it meanwhile.
            unreconciled = [path for path in unreconciled if path.exists()]
            foreign = [path for path in foreign if path.exists()]
            if unreconciled and (not discard_backups or not external_deps or foreign):
                if foreign:
                    hint = (
                        "it belongs to another user and may be their sync in "
                        "progress; let it finish or have them recover it, or once "
                        "no sync is running remove the backup and its .pending file"
                    )
                elif external_deps:
                    hint = "recover it or run `neko-plugin sync --clean` explicitly"
                else:
                    hint = "recover it before retrying"
                locations = ", ".join(str(path) for path in unreconciled)
                print(
                    f"[FAIL] Cannot sync with an unreconciled dependency backup; "
                    f"{hint}: {locations}",
                    file=sys.stderr,
                )
                return 1

            if not external_deps:
                # Leftovers of finished swaps would otherwise never go away.
                _remove_retained_backups(plugin_dir)
                print(f"[OK] {plugin_dir.name}: no external dependencies to sync")
                return 0

            # The swap renames vendor/ itself, which would replace a link to
            # another disk with a real directory. Refuse before touching it.
            if _is_link(vendor_dir):
                print(
                    f"[FAIL] {vendor_dir} is a symlink; sync replaces vendor/ as a "
                    "whole and cannot keep the link. Replace it with a real "
                    "directory and retry.",
                    file=sys.stderr,
                )
                return 1

            if vendor_dir.exists() and not vendor_dir.is_dir():
                print(
                    f"[FAIL] {vendor_dir} is not a directory; sync would move it "
                    "aside as a backup. Remove or rename it and retry.",
                    file=sys.stderr,
                )
                return 1

            if vendor_dir.is_dir():
                foreign = _find_foreign_subdir(vendor_dir, junctions=not args.clean)
                if foreign is not None:
                    print(
                        f"[FAIL] {foreign} is a directory junction or mount point "
                        "inside vendor/; sync would copy or delete what it points "
                        "to. Remove it and retry.",
                        file=sys.stderr,
                    )
                    return 1

            # Install into a sibling staging dir so vendor/ stays untouched
            # until the install succeeds. A plain mkdir (unlike mkdtemp's 0700)
            # keeps the new vendor/ readable by other users per the umask.
            staging_dir = plugin_dir / f"{VENDOR_SYNC_STAGING_PREFIX}{_short_token()}"
            if not args.clean and vendor_dir.is_dir():
                shutil.copytree(vendor_dir, staging_dir, symlinks=True)
            else:
                staging_dir.mkdir()

            exit_code = _pip_install_to_vendor(
                external_deps, vendor_dir=staging_dir, python=args.python,
            )
            if exit_code != 0:
                return exit_code
            _clean_vendor(staging_dir)
            if not _replace_vendor(vendor_dir, staging_dir):
                return 1
            # A complete successful sync supersedes retained backups.
            _remove_retained_backups(plugin_dir)
    except portalocker.exceptions.LockException:
        print(f"[FAIL] Dependency sync already in progress for {plugin_dir}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[FAIL] Could not sync dependencies for {plugin_dir}: {exc}", file=sys.stderr)
        return 1
    finally:
        # The install ran inside staging; rule out a mount there before rmtree.
        if staging_dir is not None and staging_dir.exists() and not _mounted_inside(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)

    print(f"[OK] {plugin_dir.name}: synced {len(external_deps)} dependencies to vendor/")
    print(f"  vendor={vendor_dir}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST_PROVIDED = {"n-e-k-o"}
# A backup whose swap or rollback has not finished carries a sibling marker
# file. Keeping it beside the backup (never inside a vendor tree) means it
# can not collide with package data or travel into vendor/.
def _pending_marker(backup_dir: Path) -> Path:
    return backup_dir.with_name(backup_dir.name + VENDOR_SYNC_PENDING_SUFFIX)


def _read_dependencies(pyproject_path: Path) -> list[str]:
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    deps = project.get("dependencies")
    if not isinstance(deps, list):
        return []
    return [str(d).strip() for d in deps if isinstance(d, str) and str(d).strip()]


def _filter_external(deps: list[str]) -> list[str]:
    """Filter out host-provided packages (like N.E.K.O)."""
    import re
    name_re = re.compile(r"[-_.]+")
    result = []
    for dep in deps:
        # Extract package name (before any version specifier)
        name = re.split(r"[<>=!~;\[\s@]", dep, maxsplit=1)[0].strip()
        canonical = name_re.sub("-", name).lower()
        if canonical not in _HOST_PROVIDED:
            result.append(dep)
    return result


def _is_link(path: Path) -> bool:
    """Symlink, or a Windows junction (which is_symlink() misses on 3.11)."""
    if path.is_symlink():
        return True
    try:
        os.readlink(path)
    except (OSError, ValueError):
        return False
    return True


def _find_foreign_subdir(root: Path, *, junctions: bool) -> Path | None:
    """A subdirectory of vendor/ that belongs to another tree.

    A POSIX mount point would have its contents deleted when the old vendor/
    (now a backup) is removed. A Windows junction is only a problem for the
    non-clean copy: copytree(symlinks=True) copies its whole target tree in
    as a real directory, while rmtree removes just the junction.
    """
    if sys.platform == "win32" and not junctions:
        return None
    # ismount() misses a bind mount from the same filesystem (same st_dev);
    # the kernel's mount table on Linux lists every mount point.
    mount_points = _linux_mount_points()
    if mount_points is not None:
        real_root = os.path.realpath(root)
        for point in mount_points:
            try:
                inside = point != real_root and os.path.commonpath([real_root, point]) == real_root
            except ValueError:  # different drives or mixed absolute/relative
                inside = False
            if inside:
                return Path(point)
        return None
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
            path = Path(dirpath, name)
            if sys.platform == "win32":
                if junctions and not path.is_symlink() and _is_link(path):
                    return path
            elif os.path.ismount(path):
                return path
    return None


def _linux_mount_points() -> list[str] | None:
    """Mount points from /proc/self/mountinfo, or None where unavailable."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as f:
            lines = f.readlines()
    except OSError:
        return None
    return _parse_mountinfo_points(lines)


def _parse_mountinfo_points(lines: list[str]) -> list[str]:
    points = []
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            # Field 5 is the mount point, with space/tab/newline/backslash
            # written as octal escapes.
            points.append(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]))
    return points


def _lock_dir() -> Path:
    """A per-user directory for the persistent sync lock.

    Windows temp dirs are already per user. On POSIX a lock left directly in
    a shared, sticky /tmp could be pre-created by another user (unopenable,
    and undeletable by its victim), so use a private cache dir instead, and
    fall back to a uid-named file in the temp dir only if that is unusable.
    """
    if not hasattr(os, "getuid"):
        return Path(gettempdir())
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    private = base / "neko-plugin" / "sync-locks"
    try:
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        _make_private(private)
        return private
    except OSError:
        pass  # e.g. a read-only home in a container; use the fallback below
    fallback = Path(gettempdir()) / f"neko-plugin-sync-{os.getuid()}"
    fallback.mkdir(exist_ok=True, mode=0o700)
    _make_private(fallback)
    return fallback


def _make_private(directory: Path) -> None:
    """Require the lock dir to be ours, writable, and closed to group and
    others.

    mkdir(mode=0o700) does not touch an existing directory, which may have
    been created (or later opened up) with group/world write access.
    """
    # lstat: a symlinked lock dir would make the chmod below change whatever
    # shared directory it points to.
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise PermissionError(f"lock directory {directory} is a symlink")
    if info.st_uid != os.getuid():
        raise PermissionError(f"lock directory {directory} is owned by another user")
    if info.st_mode & 0o077:
        os.chmod(directory, 0o700)
    # An existing dir may be read-only (mode 0500, a read-only mount): fail
    # here so the private dir falls back instead of every lock open failing.
    if not os.access(directory, os.W_OK | os.X_OK):
        raise PermissionError(f"lock directory {directory} is not writable")


def _absolute_if_path(program: str) -> str:
    """Pin an interpreter to what it means in this process's cwd: a path is
    made absolute, and a bare name is resolved through PATH here (a relative
    PATH entry would change meaning in another cwd)."""
    if any(sep and sep in program for sep in (os.sep, os.altsep)):
        return os.path.abspath(program)
    found = shutil.which(program)
    return os.path.abspath(found) if found else program


def _short_token() -> str:
    # Installers write deep package paths under the work dir, so keep its
    # name short: Windows without long-path support caps paths at 260 chars.
    return uuid.uuid4().hex[:8]


def _sync_work_dirs(plugin_dir: Path, prefix: str) -> list[Path]:
    """Work dirs this command created: exactly the prefix plus a token.

    A plugin may have its own directory that merely shares the prefix
    (".vendor.staging-assets"); only exact generated names are ever deleted
    or treated as dependency backups.
    """
    pattern = re.compile(re.escape(prefix) + r"[0-9a-f]{8}")
    return [
        path
        for path in plugin_dir.glob(f"{prefix}*")
        if pattern.fullmatch(path.name) and path.is_dir() and not path.is_symlink()
    ]


def _is_mount_point(path: Path) -> bool:
    # On Windows, rmtree refuses a junction or mounted folder itself.
    if sys.platform == "win32":
        return False
    mount_points = _linux_mount_points()
    if mount_points is not None:
        return os.path.realpath(path) in mount_points
    return os.path.ismount(path)


def _remove_retained_backups(plugin_dir: Path) -> None:
    """Delete backups of finished swaps, except one another user is
    mid-swap on (the lock is per user) or one with a mount inside."""
    for backup in _retained_backups(plugin_dir):
        try:
            if _swapped_by_other_user(backup):
                continue
            if _mounted_inside(backup):
                continue
            shutil.rmtree(backup)
            _pending_marker(backup).unlink(missing_ok=True)
        except OSError as exc:
            print(f"[WARN] Could not remove old dependency backup {backup}: {exc}", file=sys.stderr)


def _mounted_inside(path: Path) -> bool:
    """Whether a leftover work dir is, or contains, a mount point, which
    rmtree would descend into and empty. Such a dir is kept, with a warning."""
    if sys.platform.startswith("linux") and _linux_mount_points() is None:
        # Without the kernel's mount table a same-filesystem bind mount can
        # not be ruled out (ismount misses it), so keep the directory.
        print(
            f"[WARN] Not removing {path}: /proc/self/mountinfo is unavailable, "
            "so mounts inside it can not be ruled out. Delete it by hand.",
            file=sys.stderr,
        )
        return True
    mount = path if _is_mount_point(path) else _find_foreign_subdir(path, junctions=False)
    if mount is None:
        return False
    print(
        f"[WARN] Not removing {path}: {mount} is a mount point. "
        "Unmount it, then delete the directory.",
        file=sys.stderr,
    )
    return True


def _owned_by_other_user(path: Path) -> bool:
    """POSIX only; Windows has no cheap owner id and treats every dir as own."""
    if not hasattr(os, "getuid"):
        return False
    return path.stat().st_uid != os.getuid()


def _swapped_by_other_user(backup: Path) -> bool:
    """Whether another user's sync is (or was) mid-swap on this backup.

    The backup dir keeps the owner of the vendor/ it was renamed from, so it
    says nothing about who is swapping. Its pending marker is created by the
    syncing user right after the rename; a backup without one has finished
    its swap and belongs to nobody's live work.
    """
    if not hasattr(os, "getuid"):
        return False
    try:
        return _pending_marker(backup).stat().st_uid != os.getuid()
    except FileNotFoundError:
        return False


def _remove_stale_staging(plugin_dir: Path) -> None:
    # The sync lock is per user, so holding it only rules out this user's
    # own runs; another user's staging dir may belong to a live install.
    for path in _sync_work_dirs(plugin_dir, VENDOR_SYNC_STAGING_PREFIX):
        try:
            if _owned_by_other_user(path):
                continue
            if _mounted_inside(path):
                continue
            shutil.rmtree(path)
        except FileNotFoundError:
            # Another user's sync moved it away between glob and here.
            continue
        except OSError as exc:
            print(f"[WARN] Could not remove stale staging dir {path}: {exc}", file=sys.stderr)


def _retained_backups(plugin_dir: Path) -> list[Path]:
    return _sync_work_dirs(plugin_dir, VENDOR_SYNC_BACKUP_PREFIX)


def _unreconciled_backups(plugin_dir: Path, vendor_dir: Path) -> list[Path]:
    """Backups that may hold the only complete copy of the old vendor/.

    A backup still marked pending never finished its swap or rollback. With
    no live vendor/, every backup is the only copy of the old tree. A backup
    next to a live vendor/ without the marker is only a leftover from a sync
    whose cleanup failed, and does not block.
    """
    backups = _retained_backups(plugin_dir)
    pending = [path for path in backups if _pending_marker(path).is_file()]
    if pending:
        return pending
    return [] if vendor_dir.exists() else backups


def _pip_install_to_vendor(
    packages: list[str],
    *,
    vendor_dir: Path,
    python: str,
) -> int:
    """Install packages into vendor/ with the target Python's pip.

    Interpreters created by uv ship without pip; only then (as the target
    itself reports) fall back to
    ``uv pip install``. Trying pip first keeps pip's own configuration
    (pip.conf, PIP_INDEX_URL mirrors) in effect for everyone who has pip, and
    a uv failure can never block an install pip would have completed.

    The uv fallback runs with uv's own configuration untouched. Mapping pip's
    index variables into UV_* would override uv.toml / [tool.uv] indexes,
    because uv ranks environment variables above its config files. But uv
    silently ignores pip's settings: a private index would fall through to
    public PyPI (a same-name public package could stand in), and policies
    like require-hashes or only-binary would be dropped. So any pip setting
    that is not known to be harmless, and not covered by a matching uv
    variable, fails closed.
    """
    if not packages:
        return 0

    vendor_dir.mkdir(parents=True, exist_ok=True)

    result = _run_installer(
        [
            python, "-m", "pip", "install",
            "--target", str(vendor_dir),
            "--upgrade",
            "--no-user",
            *packages,
        ],
        label=f"target Python {python!r}",
    )
    if result is None:
        return 1
    if result.returncode == 0:
        return 0
    # Ask the target whether it has pip at all, rather than reading the
    # install log: a startup banner can surround the launch error, and a real
    # install log can mention "No module named pip" too. If the target can
    # not be asked, do not fall back.
    target = _probe_target(python)
    if target is None or target.has_pip:
        print(f"[FAIL] pip install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    uv = shutil.which("uv")
    # uv runs in target.cwd; a relative PATH entry may have found it here.
    uv = os.path.abspath(uv) if uv else None
    if not uv:
        print(
            "[FAIL] Unable to install plugin dependencies: the target Python "
            "has no pip and uv was not found. Install uv, or run "
            "python -m ensurepip --upgrade.",
            file=sys.stderr,
        )
        return 1
    settings = _pip_settings(target)
    # pip's no-index is passed on as uv's --no-index flag (uv reads no
    # environment variable for it). With no index, pip ignores its index
    # settings, and so will uv.
    no_index = "no-index" in settings
    uncovered: list[str] = []
    for name, where in sorted(settings.items()):
        if name in _PIP_HARMLESS_SETTINGS or name == "no-index":
            continue
        kind = _PIP_SETTING_KINDS.get(name, "other")
        if no_index and kind in {"index", "extra-index"}:
            continue
        if name == "require-virtualenv":
            # pip refuses to install outside a venv; uv would not check.
            if not target.in_venv:
                uncovered.append(
                    f"{name} from {', '.join(where)} (the target Python is not a virtual environment)"
                )
            continue
        covers = _UV_COVERS[kind]
        # pip's explicit proxy ignores NO_PROXY; uv honors it per host. Which
        # hosts uv will contact (indexes, redirected downloads) can not be
        # listed up front, so any bypass list at all fails closed.
        bypass_all = kind == "proxy" and any(
            (os.environ.get(name) or "").strip() for name in ("NO_PROXY", "no_proxy")
        )
        if bypass_all or not any(_uv_env_set(uv_name) for uv_name in covers):
            fix = f"set {' or '.join(covers)}" if covers else "no uv equivalent"
            uncovered.append(f"{name} from {', '.join(where)} ({fix})")
    if uncovered:
        print(
            "[FAIL] The target Python has no pip, and pip has settings that uv "
            f"does not read: {'; '.join(uncovered)}. Installing with uv would "
            "ignore them. Configure uv the same way, or run "
            "python -m ensurepip --upgrade so pip installs with its own settings.",
            file=sys.stderr,
        )
        return 1
    print("  target Python has no pip; installing with uv instead")
    result = _run_installer(
        [
            uv, "pip", "install",
            # uv runs in target.cwd, so pin the interpreter to what it means
            # here, where the pip attempt resolved it.
            "--python", _absolute_if_path(python),
            "--target", str(vendor_dir.absolute()),
            "--upgrade",
            *(["--no-index"] if no_index else []),
            *packages,
        ],
        label="uv pip install",
        # The pip attempt ran (through any launcher) in target.cwd, so
        # relative requirements such as "pkg @ file:./pkg" resolve the same.
        cwd=target.cwd,
        env=_uv_env(),
    )
    if result is None:
        return 1
    if result.returncode != 0:
        print(f"[FAIL] uv pip install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1
    return 0


# pip settings by option name; env PIP_FOO_BAR is the same setting as the
# config key foo-bar. Package-source and hash settings have uv counterparts.
_PIP_SETTING_KINDS = {
    "index-url": "index",
    "extra-index-url": "extra-index",
    "no-index": "no-index",
    "find-links": "find-links",
    "require-hashes": "require-hashes",
    "proxy": "proxy",
    "cert": "cert",
}
# Settings that cannot make uv install different packages when dropped:
# output, caching, retries, a client certificate (without it uv only fails),
# and options both installers are given explicitly anyway. Every other pip setting (only-binary,
# constraint, pre, ...) has no checked uv counterpart here and blocks the
# fallback, since uv would silently ignore it.
_PIP_HARMLESS_SETTINGS = {
    "break-system-packages",
    "cache-dir",
    "client-cert",
    "default-timeout",
    "disable-pip-version-check",
    "log",
    "log-file",
    "no-cache-dir",
    "no-color",
    "no-input",
    "no-python-version-warning",
    "no-warn-script-location",
    "progress-bar",
    "quiet",
    "retries",
    "root-user-action",
    # Overridden by the explicit --target/--upgrade of both installers.
    "target",
    "upgrade",
    "timeout",
    "trusted-host",
    "user",
    "verbose",
}
# uv environment variables that keep each kind of pip setting from being
# lost (all checked against `uv pip install --help` env bindings). pip's
# index-url replaces PyPI, so only a replaced uv default index covers it;
# UV_INDEX / UV_EXTRA_INDEX_URL / UV_FIND_LINKS are only added next to PyPI,
# so each covers only pip's additive kinds. uv has no environment variable
# for --no-index: pip's no-index is carried over by passing the flag itself
# (see _pip_install_to_vendor), which only makes uv stricter.
_UV_COVERS = {
    "index": ("UV_DEFAULT_INDEX", "UV_INDEX_URL"),
    "extra-index": ("UV_DEFAULT_INDEX", "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL"),
    "find-links": ("UV_FIND_LINKS",),
    # uv only reads its own variable; without it uv installs unhashed.
    "require-hashes": ("UV_REQUIRE_HASHES",),
    # uv has no proxy option but honors the standard proxy variables;
    # without one, uv would bypass a filtering proxy and connect directly.
    # Package indexes are HTTPS, which HTTP_PROXY does not cover.
    "proxy": ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"),
    # pip's cert replaces the default CA bundle (possibly a restrictive one);
    # uv would otherwise trust its bundled roots. uv reads SSL_CERT_FILE but
    # silently ignores a path that does not exist; its default TLS backend
    # does not read SSL_CERT_DIR at all.
    "cert": ("SSL_CERT_FILE",),
    "other": (),
}
_UV_BOOLEAN_ENV = {"UV_REQUIRE_HASHES"}


@dataclass(frozen=True)
class _TargetPython:
    """What the target interpreter reports about itself."""

    has_pip: bool
    prefix: Path
    in_venv: bool
    # The environment and working directory the target actually runs with:
    # a launcher or shim (pyenv, asdf, a wrapper script) may export PIP_*,
    # or cd, before exec'ing it.
    env: dict[str, str]
    cwd: Path


_TARGET_PROBE = (
    "import importlib.util, json, os, sys; "
    "print(json.dumps({"
    "'has_pip': importlib.util.find_spec('pip') is not None, "
    "'prefix': sys.prefix, "
    "'in_venv': sys.prefix != sys.base_prefix, "
    "'env': dict(os.environ), "
    "'cwd': os.getcwd()}))"
)


def _probe_target(python: str) -> _TargetPython | None:
    """Ask the target interpreter about itself; None if it can not answer."""
    try:
        result = subprocess.run(
            [python, "-c", _TARGET_PROBE],
            text=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # Startup output (a sitecustomize banner) may precede the JSON line.
    for line in reversed((result.stdout or "").splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and {"has_pip", "prefix", "in_venv", "env", "cwd"} <= data.keys():
            return _TargetPython(
                has_pip=bool(data["has_pip"]),
                prefix=Path(data["prefix"]),
                in_venv=bool(data["in_venv"]),
                env={str(k): str(v) for k, v in dict(data["env"]).items()},
                cwd=Path(data["cwd"]),
            )
    return None


def _pip_settings(target: _TargetPython) -> dict[str, list[str]]:
    """pip settings in effect for the target (its environment and pip's
    config files), mapped to where each one is set. A boolean option set to
    false does not count."""
    env = target.env
    found: dict[str, list[str]] = {}
    for env_name, value in env.items():
        # Environment names are case-insensitive only on Windows; elsewhere
        # pip ignores e.g. "pip_constraint".
        upper = env_name.upper() if sys.platform == "win32" else env_name
        if not upper.startswith("PIP_") or upper == "PIP_CONFIG_FILE" or not value:
            continue
        name = upper[4:].lower().replace("_", "-")
        if not _explicitly_off(name, value):
            found.setdefault(name, []).append(env_name)
    config_file = _env_get(env, "PIP_CONFIG_FILE")
    if config_file == os.devnull:
        # pip documents this value as "load no config files".
        return found
    # pip resolves a relative PIP_CONFIG_FILE from its own working directory.
    candidates = [target.cwd / config_file] if config_file else []
    for path in [*candidates, *_pip_config_files(target)]:
        for key in _config_setting_keys(path):
            found.setdefault(key, []).append(str(path))
    return found


def _env_get(env: dict[str, str], name: str) -> str | None:
    # Windows environment names are case-insensitive.
    if sys.platform == "win32":
        return next((v for k, v in env.items() if k.upper() == name), None)
    return env.get(name)


def _pip_config_files(target: _TargetPython) -> list[Path]:
    """pip's documented global, user and site config file locations, as the
    target's environment resolves them."""
    env = target.env
    files: list[Path] = []
    if sys.platform == "win32":
        home = Path(_env_get(env, "USERPROFILE") or Path.home())
        for base in (_env_get(env, "PROGRAMDATA"), _env_get(env, "APPDATA")):
            if base:
                files.append(Path(base, "pip", "pip.ini"))
        files.append(home / "pip" / "pip.ini")
        site_name = "pip.ini"
    else:
        home = Path(env.get("HOME") or Path.home())
        xdg_dirs = env.get("XDG_CONFIG_DIRS") or "/etc/xdg"
        files.extend(Path(d, "pip", "pip.conf") for d in xdg_dirs.split(os.pathsep) if d)
        files.append(Path("/etc/pip.conf"))
        if sys.platform == "darwin":
            files.append(Path("/Library/Application Support/pip/pip.conf"))
            files.append(home / "Library" / "Application Support" / "pip" / "pip.conf")
        files.append(Path(env.get("XDG_CONFIG_HOME") or home / ".config", "pip", "pip.conf"))
        files.append(home / ".pip" / "pip.conf")
        site_name = "pip.conf"
    # Site config sits in the target environment's own prefix.
    files.append(target.prefix / site_name)
    return files


def _config_setting_keys(path: Path) -> set[str]:
    parser = configparser.RawConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return set()
    except (configparser.Error, UnicodeDecodeError):
        # pip itself would reject this file; it counts as an unknown setting.
        return {"(unreadable config)"}
    # Any file enabling a setting counts, even if another file might override
    # it: emulating pip's full config precedence is not worth the risk here,
    # and the error in that case only asks for a uv setting or pip itself.
    # `pip install` reads only [global] and its own [install] section.
    return {
        key.replace("_", "-")
        for section in parser.sections()
        if section.lower() in {"global", "install"}
        for key, value in parser[section].items()
        if not _explicitly_off(key.replace("_", "-"), value)
    }


def _uv_env_set(name: str) -> bool:
    value = os.environ.get(name)
    if not value:
        return False
    if name == "SSL_CERT_FILE":
        return Path(value).is_file()
    if name in _UV_BOOLEAN_ENV:
        # uv parses these as booleans: "0" / "false" turn them off.
        return value.strip().lower() in {"y", "yes", "t", "true", "on", "1"}
    return True


# pip's boolean flags (store_true options of `pip install` and the general
# options, per pip 25 --help). Only these can be switched off with a false
# value; for any other option "off" or "0" is a real value (a file name, a
# package list) and the option stays set.
_PIP_BOOLEAN_SETTINGS = {
    "break-system-packages",
    "check-build-dependencies",
    "compile",
    "debug",
    "disable-pip-version-check",
    "dry-run",
    "force-reinstall",
    "ignore-installed",
    "ignore-requires-python",
    "isolated",
    "no-build-isolation",
    "no-cache-dir",
    "no-clean",
    "no-color",
    "no-compile",
    "no-deps",
    "no-index",
    "no-input",
    "no-proxy-env",
    "no-require-hashes",
    "no-warn-conflicts",
    "no-warn-script-location",
    "pre",
    "prefer-binary",
    "require-hashes",
    "require-virtualenv",
    "upgrade",
    "use-pep517",
    "user",
}


def _explicitly_off(name: str, value: str) -> bool:
    return name in _PIP_BOOLEAN_SETTINGS and _pip_false(value)


def _pip_false(value: str) -> bool:
    # pip parses booleans with strtobool; unparseable values stay strict.
    return value.strip().lower() in {"n", "no", "f", "false", "off", "0"}


def _uv_env() -> dict[str, str]:
    """This process's environment for uv, with a relative SSL_CERT_FILE
    pinned: uv runs in another cwd, where the checked file may not exist and
    uv would silently fall back to its bundled roots."""
    env = dict(os.environ)
    cert = env.get("SSL_CERT_FILE")
    if cert:
        env["SSL_CERT_FILE"] = os.path.abspath(cert)
    return env


def _run_installer(
    cmd: list[str],
    *,
    label: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    print(f"  running: {' '.join(cmd)}")
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        print(f"[FAIL] {label} could not start: {exc}", file=sys.stderr)
        return None


def _replace_vendor(vendor_dir: Path, staging_dir: Path) -> bool:
    """Swap the staging dir into vendor/; on failure rename the old one back."""
    backup_dir = vendor_dir.parent / f"{VENDOR_SYNC_BACKUP_PREFIX}{_short_token()}"
    marker = _pending_marker(backup_dir)
    had_vendor = vendor_dir.exists()
    try:
        if had_vendor:
            # Mark before the rename, so the backup never exists unmarked while
            # its swap is live: a crash or failed rollback leaves a backup that
            # blocks plain retries, and another user's sync can not take it for
            # a finished leftover and delete it.
            marker.touch()
            try:
                vendor_dir.replace(backup_dir)
            except OSError:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass  # A marker without its backup dir blocks nothing.
                raise
    except OSError as exc:
        # vendor/ was not moved, so there is nothing to roll back.
        _report_replace_failure(vendor_dir, exc)
        return False
    try:
        staging_dir.replace(vendor_dir)
    except OSError as exc:
        _report_replace_failure(vendor_dir, exc)
        if had_vendor:
            _roll_back_vendor(vendor_dir, backup_dir)
        return False
    if had_vendor:
        try:
            _pending_marker(backup_dir).unlink(missing_ok=True)
        except OSError as exc:
            print(f"[WARN] Could not clear recovery marker for {backup_dir}: {exc}", file=sys.stderr)
        # vendor/ was checked for mounts before the install, but one may have
        # been added since; check again right before deleting.
        if not _mounted_inside(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
    return True


def _roll_back_vendor(vendor_dir: Path, backup_dir: Path) -> None:
    if vendor_dir.exists() or vendor_dir.is_symlink():
        # Whatever now occupies vendor/ is neither tree we manage; never
        # delete it. The marked backup blocks plain retries until recovered.
        print(
            f"[FAIL] Could not roll back: {vendor_dir} reappeared after the failed swap; "
            f"backup retained at {backup_dir}",
            file=sys.stderr,
        )
        return
    try:
        # Keep the marker on the backup until the rename succeeds, so a failed
        # rollback stays blocking even if vendor/ reappears before a retry.
        backup_dir.replace(vendor_dir)
    except OSError as exc:
        print(
            f"[FAIL] Could not roll back vendor; backup retained at {backup_dir}: {exc}",
            file=sys.stderr,
        )
        return
    try:
        _pending_marker(backup_dir).unlink(missing_ok=True)
    except OSError as exc:
        # Without its backup dir the marker blocks nothing.
        print(f"[WARN] Could not clear recovery marker for {backup_dir}: {exc}", file=sys.stderr)


def _report_replace_failure(vendor_dir: Path, exc: OSError) -> None:
    if isinstance(exc, PermissionError):
        print(
            f"[FAIL] Cannot replace {vendor_dir}: files are in use. "
            f"Close processes using the plugin and retry. ({exc})",
            file=sys.stderr,
        )
    else:
        print(f"[FAIL] Failed to replace {vendor_dir}: {exc}", file=sys.stderr)


def _clean_vendor(vendor_dir: Path) -> None:
    """Remove common unwanted artifacts from vendor/."""
    if not vendor_dir.is_dir():
        return

    # Remove __pycache__ directories
    for cache_dir in vendor_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)

    # Remove .pyc files
    for pyc in vendor_dir.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    # Remove bin/ directory (CLI scripts we don't need)
    bin_dir = vendor_dir / "bin"
    if bin_dir.is_symlink():
        bin_dir.unlink()
    elif bin_dir.is_dir():
        shutil.rmtree(bin_dir, ignore_errors=True)
