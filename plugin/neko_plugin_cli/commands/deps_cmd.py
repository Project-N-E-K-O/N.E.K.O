"""neko-plugin sync — materialize declared Python dependencies in vendor/."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
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
            "Target Python interpreter; uv installs the dependencies for it "
            "(falls back to its pip, with a warning, only when uv is not found)"
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
    lock_name = hashlib.sha256(_lock_identity(plugin_dir)).hexdigest()
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

            if vendor_dir.is_dir() and _is_mount_point(vendor_dir):
                print(
                    f"[FAIL] {vendor_dir} is a mount point; sync replaces vendor/ by "
                    "renaming, which a mount point does not allow. Use a plain "
                    "directory for vendor/ and retry.",
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

            exit_code = _install_to_vendor(
                external_deps, vendor_dir=staging_dir, python=args.python,
            )
            if exit_code != 0:
                return exit_code
            # _clean_vendor recurses, and the swap would expose staging as
            # vendor/: stop if anything got mounted inside during the install.
            # (An unreadable mount table does not stop the sync: vendor/ was
            # checked before, and the deletions later keep what they can not
            # rule out.)
            mounted = _find_mount(staging_dir)
            if mounted is not None:
                print(
                    f"[FAIL] {mounted} got mounted inside {staging_dir} during the "
                    "install; not touching it. Unmount it, then retry.",
                    file=sys.stderr,
                )
                return 1  # the finally block's mount check keeps staging
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

    On POSIX a lock left directly in a shared, sticky /tmp could be
    pre-created by another user (unopenable, and undeletable by its victim),
    so use a private cache dir instead, and fall back to a uid-named dir in
    the shared temp dir only if that is unusable. On Windows use the user's
    LocalAppData as the shell reports it. Neither location depends on the
    process environment (TEMP, HOME, XDG_CACHE_HOME), so every sync process
    of one user picks the same lock.
    """
    if not hasattr(os, "getuid"):
        local = _windows_known_folder(_FOLDERID_LOCAL_APPDATA)
        if local is not None:
            private = local / "neko-plugin" / "sync-locks"
            try:
                private.mkdir(parents=True, exist_ok=True)
                return private
            except OSError:
                pass
        return Path(gettempdir())
    base = _lock_cache_base()
    if base is not None:
        private = base / "neko-plugin" / "sync-locks"
        try:
            private.mkdir(parents=True, exist_ok=True, mode=0o700)
            _make_private(private)
            return private
        except OSError:
            pass  # e.g. a read-only home in a container; use the fallback below
    fallback = _shared_tmp() / f"neko-plugin-sync-{os.getuid()}"
    fallback.mkdir(exist_ok=True, mode=0o700)
    _make_private(fallback)
    return fallback


def _lock_identity(plugin_dir: Path) -> bytes:
    """The plugin directory itself, not one of its names: two bind-mount
    aliases (or other paths) to one directory must share one lock. The
    resolved path is the fallback where the directory has no usable id."""
    try:
        info = plugin_dir.stat()
    except OSError:
        info = None
    if info is not None and info.st_ino:
        return f"{info.st_dev}:{info.st_ino}".encode()
    # fsencode: a POSIX path may hold undecodable bytes (surrogate escapes).
    return os.fsencode(os.path.normcase(str(plugin_dir.resolve())))


# FOLDERID_LocalAppData, as SHGetKnownFolderPath reports it.
_FOLDERID_LOCAL_APPDATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"


def _windows_known_folder(folder_id: str) -> Path | None:
    """A known folder from the shell, not from LOCALAPPDATA / TEMP, which a
    process may have set to something else."""
    try:
        import ctypes

        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(folder_id).bytes_le)
        path = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path)
        )
    except (AttributeError, ImportError, OSError):
        return None
    try:
        return Path(path.value) if result == 0 and path.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def _lock_cache_base() -> Path | None:
    """The user's cache dir from the account database, not from HOME or
    XDG_CACHE_HOME: every sync process of one user must pick the same lock,
    whatever its environment, or two could run at once and one could delete
    the other's live staging dir as stale. None when the uid has no account
    record (common in containers)."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache"
    except (ImportError, KeyError):
        return None


def _shared_tmp() -> Path:
    # The POSIX /tmp rather than gettempdir(), which follows TMPDIR and so
    # could differ between two processes of the same user. Every process of
    # one uid sees /tmp's usability the same way, so the choice stays stable;
    # an unusable /tmp (read-only in some containers) falls back to TMPDIR.
    fixed = Path("/tmp")
    if fixed.is_dir() and os.access(fixed, os.W_OK | os.X_OK):
        return fixed
    return Path(gettempdir())


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


def _find_mount(path: Path) -> Path | None:
    """A mount point that is, or is inside, path, if one is positively seen."""
    return path if _is_mount_point(path) else _find_foreign_subdir(path, junctions=False)


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
    mount = _find_mount(path)
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


def _tri(english: str, chinese: str, japanese: str) -> str:
    return f"{english} / {chinese} / {japanese}"


_PIP_FALLBACK_BANNER = "!" * 78
_PIP_FALLBACK_WARNING = "\n".join([
    _PIP_FALLBACK_BANNER,
    "[WARN] uv was not found; falling back to pip.",
    "  This project requires uv. pip reads a different configuration (pip.conf,",
    "  PIP_* variables) and resolves dependencies its own way, so vendor/ may",
    "  not match what uv installs, with unpredictable results. Install uv",
    "  (https://docs.astral.sh/uv/) and run this command again.",
    "[警告] 未找到 uv，改用 pip 安装。",
    "  本项目强制要求使用 uv。pip 读取的是另一套配置（pip.conf、PIP_* 环境变量），",
    "  解析依赖的方式也不同，装出的 vendor/ 可能与 uv 不一致，后果不可预测。",
    "  请安装 uv（https://docs.astral.sh/uv/）后重新运行本命令。",
    "[警告] uv が見つからないため、pip にフォールバックします。",
    "  本プロジェクトは uv の使用を必須としています。pip は別の設定（pip.conf、",
    "  PIP_* 環境変数）を読み、依存関係の解決方法も異なるため、vendor/ が uv の",
    "  結果と一致せず、予測できない問題が起きる可能性があります。",
    "  uv（https://docs.astral.sh/uv/）をインストールして、再実行してください。",
    _PIP_FALLBACK_BANNER,
])


def _find_uv() -> str | None:
    # `uv run` exports its own path as UV, which finds uv even when it is not
    # on PATH (installed with pipx, or `py -m uv` on Windows).
    for candidate in (os.environ.get("UV"), "uv"):
        found = shutil.which(candidate) if candidate else None
        if found:
            return found
    return None


def _install_to_vendor(
    packages: list[str],
    *,
    vendor_dir: Path,
    python: str,
) -> int:
    """Install packages into vendor/ for the target interpreter.

    The project requires uv: `uv pip install` (uv's own installer, not pip)
    installs for the target interpreter, which needs no pip of its own, and
    reads uv's configuration (uv.toml, UV_* variables). Only when uv can not
    be found does the target's own pip install them, behind a warning: pip
    reads another configuration and resolves differently.
    """
    if not packages:
        return 0

    vendor_dir.mkdir(parents=True, exist_ok=True)

    uv = _find_uv()
    if uv is not None:
        command = [
            uv, "pip", "install",
            "--python", python,
            "--target", str(vendor_dir),
            "--upgrade",
            *packages,
        ]
        label = "uv pip install"
    else:
        print(_PIP_FALLBACK_WARNING, file=sys.stderr)
        command = [
            python, "-m", "pip", "install",
            "--target", str(vendor_dir),
            "--upgrade",
            "--no-user",
            *packages,
        ]
        label = "pip install"
    result = _run_installer(command, label=label)
    if result is not None and result.returncode != 0:
        print(f"[FAIL] {label} failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
    if uv is None:
        # Last, after any installer output that may have scrolled the
        # warning away.
        print(
            "[WARN] "
            + _tri(
                "This sync used pip, not uv; see the warning above.",
                "本次同步用的是 pip 而不是 uv，见上方警告。",
                "今回の同期は uv ではなく pip を使用しました。上の警告を参照してください。",
            ),
            file=sys.stderr,
        )
    return 0 if result is not None and result.returncode == 0 else 1


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
