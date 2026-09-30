"""neko-plugin sync — materialize declared Python dependencies in vendor/."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from tempfile import gettempdir

import portalocker

from ..core.build_rules import VENDOR_SYNC_BACKUP_PREFIX, VENDOR_SYNC_STAGING_PREFIX
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
    # One lock file per user: the lock is never deleted, and a file another
    # user created in a shared /tmp may not be openable (umask,
    # fs.protected_regular). Windows temp dirs are already per user.
    owner = f"{os.getuid()}-" if hasattr(os, "getuid") else ""
    lock_path = Path(gettempdir()) / f"neko-plugin-sync-{owner}{lock_name}.lock"
    staging_dir: Path | None = None
    try:
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

            # --clean may discard an unreconciled backup, but only when an
            # install rebuilds vendor/; the no-dependency path has nothing to
            # reconcile it with.
            unreconciled = _unreconciled_backups(plugin_dir, vendor_dir)
            if unreconciled and (not args.clean or not external_deps):
                hint = (
                    "recover it or use explicit --clean"
                    if external_deps
                    else "recover it before retrying"
                )
                locations = ", ".join(str(path) for path in unreconciled)
                print(
                    f"[FAIL] Cannot sync with an unreconciled dependency backup; "
                    f"{hint}: {locations}",
                    file=sys.stderr,
                )
                return 1

            if not external_deps:
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
            for backup in _retained_backups(plugin_dir):
                try:
                    if _mounted_inside(backup):
                        continue
                    shutil.rmtree(backup)
                except OSError as exc:
                    print(f"[WARN] Could not remove old dependency backup {backup}: {exc}", file=sys.stderr)
    except portalocker.exceptions.LockException:
        print(f"[FAIL] Dependency sync already in progress for {plugin_dir}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[FAIL] Could not sync dependencies for {plugin_dir}: {exc}", file=sys.stderr)
        return 1
    finally:
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    print(f"[OK] {plugin_dir.name}: synced {len(external_deps)} dependencies to vendor/")
    print(f"  vendor={vendor_dir}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST_PROVIDED = {"n-e-k-o"}
_RECOVERY_MARKER = ".recovery-pending"


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


def _mounted_inside(path: Path) -> bool:
    """Whether a leftover work dir is, or contains, a mount point, which
    rmtree would descend into and empty. Such a dir is kept, with a warning."""
    mount = path if _is_mount_point(path) else _find_foreign_subdir(path, junctions=False)
    if mount is None:
        return False
    print(
        f"[WARN] Not removing {path}: {mount} is a mount point. "
        "Unmount it, then delete the directory.",
        file=sys.stderr,
    )
    return True


def _remove_stale_staging(plugin_dir: Path) -> None:
    # The sync lock is per user, so holding it only rules out this user's
    # own runs; another user's staging dir may belong to a live install.
    uid = os.getuid() if hasattr(os, "getuid") else None
    for path in _sync_work_dirs(plugin_dir, VENDOR_SYNC_STAGING_PREFIX):
        try:
            if uid is not None and path.stat().st_uid != uid:
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
    pending = [path for path in backups if (path / _RECOVERY_MARKER).is_file()]
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

    Interpreters created by uv ship without pip; only then fall back to
    ``uv pip install``. Trying pip first keeps pip's own configuration
    (pip.conf, PIP_INDEX_URL mirrors) in effect for everyone who has pip, and
    a uv failure can never block an install pip would have completed.

    The uv fallback runs with uv's own configuration untouched. Mapping pip's
    index variables into UV_* would override uv.toml / [tool.uv] indexes,
    because uv ranks environment variables above its config files. But when
    pip has a package source that no matching uv variable covers, uv would
    resolve from public PyPI instead, letting a same-name public package
    stand in for a private one; that case fails closed.
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
    # Only the interpreter's own launch failure means pip is absent; the same
    # words anywhere in a real install log (a build step, a child process)
    # must not reroute a genuine pip failure to uv.
    output_lines = (result.stdout or "").strip().splitlines()
    pip_missing = len(output_lines) == 1 and output_lines[0].endswith("No module named pip")
    if not pip_missing:
        print(f"[FAIL] pip install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    uv = shutil.which("uv")
    if not uv:
        print(
            "[FAIL] Unable to install plugin dependencies: the target Python "
            "has no pip and uv was not found. Install uv, or run "
            "python -m ensurepip --upgrade.",
            file=sys.stderr,
        )
        return 1
    sources, kinds = _pip_package_sources(python)
    uncovered = [
        kind for kind in sorted(kinds)
        if not any(_uv_env_set(name) for name in _UV_COVERS[kind])
    ]
    if uncovered:
        needed = "; ".join(
            f"{kind}: {' or '.join(_UV_COVERS[kind])}" for kind in uncovered
        )
        print(
            "[FAIL] The target Python has no pip, and pip is configured with "
            f"package source or hash settings ({', '.join(sources)}) that uv "
            "does not read. Installing with uv would ignore them. "
            f"Configure uv the same way ({needed}), or run "
            "python -m ensurepip --upgrade.",
            file=sys.stderr,
        )
        return 1
    print("  target Python has no pip; installing with uv instead")
    result = _run_installer(
        [
            uv, "pip", "install",
            "--python", python,
            "--target", str(vendor_dir),
            "--upgrade",
            *packages,
        ],
        label="uv pip install",
    )
    if result is None:
        return 1
    if result.returncode != 0:
        print(f"[FAIL] uv pip install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1
    return 0


# pip package-source settings (and the require-hashes install policy), by
# kind, as env vars and config file keys.
_PIP_ENV_KINDS = {
    "PIP_INDEX_URL": "index",
    "PIP_EXTRA_INDEX_URL": "extra-index",
    "PIP_NO_INDEX": "no-index",
    "PIP_FIND_LINKS": "find-links",
    "PIP_REQUIRE_HASHES": "require-hashes",
}
_PIP_KEY_KINDS = {
    "index-url": "index",
    "extra-index-url": "extra-index",
    "no-index": "no-index",
    "find-links": "find-links",
    "require-hashes": "require-hashes",
}
_PIP_INDEX_KEYS = set(_PIP_KEY_KINDS)
# Kinds set by a boolean; an explicit false value is no setting at all.
_BOOLEAN_KINDS = {"no-index", "require-hashes"}
# uv settings that keep each kind of pip source from falling through to
# PyPI. pip's index-url replaces PyPI, so only a replaced uv default index
# (or UV_NO_INDEX) covers it; UV_INDEX / UV_EXTRA_INDEX_URL / UV_FIND_LINKS
# are only added next to PyPI, so each covers only pip's additive kinds.
_UV_COVERS = {
    "index": ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_NO_INDEX"),
    "extra-index": ("UV_DEFAULT_INDEX", "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_NO_INDEX"),
    "find-links": ("UV_FIND_LINKS", "UV_NO_INDEX"),
    "no-index": ("UV_NO_INDEX",),
    # uv only reads its own variable; without it uv installs unhashed.
    "require-hashes": ("UV_REQUIRE_HASHES",),
}
_UV_BOOLEAN_ENV = {"UV_NO_INDEX", "UV_REQUIRE_HASHES"}


def _pip_package_sources(python: str) -> tuple[list[str], set[str]]:
    """Where pip takes non-default package sources from, and their kinds."""
    sources: list[str] = []
    kinds: set[str] = set()
    for name, kind in _PIP_ENV_KINDS.items():
        value = os.environ.get(name)
        if value and not (kind in _BOOLEAN_KINDS and _pip_false(value)):
            sources.append(name)
            kinds.add(kind)
    config_file = os.environ.get("PIP_CONFIG_FILE")
    if config_file == os.devnull:
        # pip documents this value as "load no config files".
        return sources, kinds
    candidates = [Path(config_file)] if config_file else []
    for path in [*candidates, *_pip_config_files(python)]:
        keys = _config_source_keys(path)
        if keys:
            sources.append(str(path))
            kinds.update(_PIP_KEY_KINDS[key] for key in keys)
    return sources, kinds


def _pip_config_files(python: str) -> list[Path]:
    """pip's documented global, user and site config file locations."""
    home = Path.home()
    files: list[Path] = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMDATA"), os.environ.get("APPDATA")):
            if base:
                files.append(Path(base, "pip", "pip.ini"))
        files.append(home / "pip" / "pip.ini")
        site_name = "pip.ini"
    else:
        xdg_dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
        files.extend(Path(d, "pip", "pip.conf") for d in xdg_dirs.split(os.pathsep) if d)
        files.append(Path("/etc/pip.conf"))
        if sys.platform == "darwin":
            files.append(Path("/Library/Application Support/pip/pip.conf"))
            files.append(home / "Library" / "Application Support" / "pip" / "pip.conf")
        files.append(Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config", "pip", "pip.conf"))
        files.append(home / ".pip" / "pip.conf")
        site_name = "pip.conf"
    # Site config sits in the target environment's prefix.
    interpreter = Path(shutil.which(python) or python)
    prefix = interpreter.parent
    if sys.platform != "win32" or prefix.name.lower() == "scripts":
        prefix = prefix.parent
    files.append(prefix / site_name)
    return files


def _config_source_keys(path: Path) -> set[str]:
    parser = configparser.RawConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return set()
    except (configparser.Error, UnicodeDecodeError):
        # pip itself would reject this file; assume the strictest setting.
        return set(_PIP_INDEX_KEYS)
    # Any file enabling a setting counts, even if another file might override
    # it: emulating pip's full config precedence is not worth the risk here,
    # and the error in that case only asks for an equivalent uv setting.
    return {
        key.replace("_", "-")
        for section in parser.sections()
        for key, value in parser[section].items()
        if not (_PIP_KEY_KINDS.get(key.replace("_", "-")) in _BOOLEAN_KINDS and _pip_false(value))
    } & _PIP_INDEX_KEYS


def _uv_env_set(name: str) -> bool:
    value = os.environ.get(name)
    if not value:
        return False
    if name in _UV_BOOLEAN_ENV:
        # uv parses these as booleans: "0" / "false" turn them off.
        return value.strip().lower() in {"y", "yes", "t", "true", "on", "1"}
    return True


def _pip_false(value: str) -> bool:
    # pip parses booleans with strtobool; unparseable values stay strict.
    return value.strip().lower() in {"n", "no", "f", "false", "off", "0"}


def _run_installer(cmd: list[str], *, label: str) -> subprocess.CompletedProcess[str] | None:
    print(f"  running: {' '.join(cmd)}")
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        print(f"[FAIL] {label} could not start: {exc}", file=sys.stderr)
        return None


def _replace_vendor(vendor_dir: Path, staging_dir: Path) -> bool:
    """Swap the staging dir into vendor/; on failure rename the old one back."""
    backup_dir = vendor_dir.parent / f"{VENDOR_SYNC_BACKUP_PREFIX}{_short_token()}"
    had_vendor = vendor_dir.exists()
    try:
        if had_vendor:
            vendor_dir.replace(backup_dir)
    except OSError as exc:
        # vendor/ was not moved, so there is nothing to roll back.
        _report_replace_failure(vendor_dir, exc)
        return False
    try:
        if had_vendor:
            # Persist the uncertain state before the second rename, so a crash
            # or a failed rollback leaves a backup that blocks plain retries.
            (backup_dir / _RECOVERY_MARKER).touch()
        staging_dir.replace(vendor_dir)
    except OSError as exc:
        _report_replace_failure(vendor_dir, exc)
        if had_vendor:
            _roll_back_vendor(vendor_dir, backup_dir)
        return False
    if had_vendor:
        try:
            (backup_dir / _RECOVERY_MARKER).unlink(missing_ok=True)
        except OSError as exc:
            print(f"[WARN] Could not clear recovery marker in {backup_dir}: {exc}", file=sys.stderr)
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
        (vendor_dir / _RECOVERY_MARKER).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[WARN] Could not clear recovery marker in {vendor_dir}: {exc}", file=sys.stderr)


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

    # A rollback interrupted before clearing its marker leaves it in vendor/;
    # never carry it into the next tree.
    (vendor_dir / _RECOVERY_MARKER).unlink(missing_ok=True)

    # Remove bin/ directory (CLI scripts we don't need)
    bin_dir = vendor_dir / "bin"
    if bin_dir.is_symlink():
        bin_dir.unlink()
    elif bin_dir.is_dir():
        shutil.rmtree(bin_dir, ignore_errors=True)
