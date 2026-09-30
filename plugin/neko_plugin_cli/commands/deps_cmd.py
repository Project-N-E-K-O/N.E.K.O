"""neko-plugin sync — materialize declared Python dependencies in vendor/."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from tempfile import gettempdir, mkdtemp

import portalocker

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
        help="Python interpreter to use for pip install",
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

    pyproject_path = plugin_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        print(f"[OK] {plugin_dir.name}: no external dependencies to sync")
        return 0

    # 1. Read declared dependencies
    all_deps = _read_dependencies(pyproject_path)
    external_deps = _filter_external(all_deps)
    if not external_deps:
        print(f"[OK] {plugin_dir.name}: no external dependencies to sync")
        return 0

    # 2. Install into a sibling staging directory.  Keeping the current
    # vendor untouched until installation succeeds makes sync transactional.
    vendor_dir = plugin_dir / "vendor"
    # Keep a persistent OS lock file outside the plugin. Unlinking lock files
    # can let waiting processes lock different inodes for the same plugin.
    identity = os.path.normcase(str(plugin_dir.resolve()))
    lock_name = hashlib.sha256(identity.encode()).hexdigest()
    lock_path = Path(gettempdir()) / f"neko-plugin-sync-{lock_name}.lock"
    staging_dir: Path | None = None
    try:
        with portalocker.Lock(lock_path, timeout=0):
            retained_backups = [
                path
                for path in plugin_dir.glob(".vendor.backup-*")
                if path.is_dir() and not path.is_symlink()
            ]
            if not args.clean and not vendor_dir.exists() and retained_backups:
                locations = ", ".join(str(path) for path in retained_backups)
                print(
                    f"[FAIL] Cannot sync without a live vendor; retained dependency "
                    f"backup requires recovery or explicit --clean: {locations}",
                    file=sys.stderr,
                )
                return 1
            staging_dir = Path(mkdtemp(prefix=".vendor.staging-", dir=plugin_dir))
            if not args.clean and vendor_dir.is_dir():
                shutil.copytree(vendor_dir, staging_dir, dirs_exist_ok=True, symlinks=True)

            exit_code = _pip_install_to_vendor(
                external_deps, vendor_dir=staging_dir, python=args.python,
            )
            if exit_code != 0:
                return exit_code
            _clean_vendor(staging_dir)
            if not _replace_vendor(vendor_dir, staging_dir):
                return 1
            # A complete successful sync supersedes retained recovery backups.
            for backup in plugin_dir.glob(".vendor.backup-*"):
                if backup.is_dir() and not backup.is_symlink():
                    try:
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


def _pip_install_to_vendor(
    packages: list[str],
    *,
    vendor_dir: Path,
    python: str,
) -> int:
    """Install packages into vendor/ using uv, then fall back to pip."""
    if not packages:
        return 0

    vendor_dir.mkdir(parents=True, exist_ok=True)

    uv = shutil.which("uv")
    if uv:
        cmd = [
            uv, "pip", "install",
            "--python", python,
            "--target", str(vendor_dir),
            "--upgrade",
            *packages,
        ]
    else:
        cmd = [
            python, "-m", "pip", "install",
            "--target", str(vendor_dir),
            "--upgrade",
            "--no-user",
            *packages,
        ]

    print(f"  running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        installer = "uv pip install" if uv else f"target Python {python!r}"
        print(f"[FAIL] {installer} could not start: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        output = result.stdout or ""
        if not uv and "No module named pip" in output:
            print(
                "[FAIL] Unable to install plugin dependencies: uv was not found "
                "and the target Python has no pip. Install uv, or run "
                "python -m ensurepip --upgrade.",
                file=sys.stderr,
            )
        else:
            installer = "uv pip" if uv else "pip"
            print(f"[FAIL] {installer} install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1
    return 0


def _replace_vendor(vendor_dir: Path, staging_dir: Path) -> bool:
    """Replace vendor/ and roll back if the second rename fails."""
    backup_dir = vendor_dir.parent / f".{vendor_dir.name}.backup-{uuid.uuid4().hex}"
    had_vendor = vendor_dir.exists()
    try:
        if had_vendor:
            vendor_dir.replace(backup_dir)
        try:
            staging_dir.replace(vendor_dir)
        except PermissionError:
            print(
                f"[FAIL] Cannot replace {vendor_dir}: files are in use. "
                "Close processes using the plugin and retry.",
                file=sys.stderr,
            )
            if vendor_dir.exists():
                shutil.rmtree(vendor_dir, ignore_errors=True)
            if backup_dir.exists():
                restore_dir = vendor_dir.parent / (
                    f".{vendor_dir.name}.restore-{uuid.uuid4().hex}"
                )
                try:
                    # Copy to a sibling restore directory before publishing it,
                    # so a failed copy can never expose a partial vendor tree.
                    shutil.copytree(backup_dir, restore_dir, symlinks=True)
                    restore_dir.replace(vendor_dir)
                except OSError as exc:
                    # Do not leave a partially restored live directory.
                    if restore_dir.exists():
                        shutil.rmtree(restore_dir, ignore_errors=True)
                    if vendor_dir.exists():
                        shutil.rmtree(vendor_dir, ignore_errors=True)
                    print(
                        f"[FAIL] Could not restore vendor; backup retained at "
                        f"{backup_dir}: {exc}",
                        file=sys.stderr,
                    )
            return False
        except OSError as exc:
            print(f"[FAIL] Failed to replace {vendor_dir}: {exc}", file=sys.stderr)
            if vendor_dir.exists():
                try:
                    shutil.rmtree(vendor_dir)
                except OSError as cleanup_exc:
                    print(
                        f"[FAIL] Could not clear failed vendor; backup retained at "
                        f"{backup_dir}: {cleanup_exc}",
                        file=sys.stderr,
                    )
                    return False
                if vendor_dir.exists():
                    print(
                        f"[FAIL] Could not clear failed vendor; backup retained at "
                        f"{backup_dir}: removal was incomplete",
                        file=sys.stderr,
                    )
                    return False
            if backup_dir.exists():
                try:
                    backup_dir.replace(vendor_dir)
                except OSError as rollback_exc:
                    print(
                        f"[FAIL] Could not roll back vendor; backup retained at "
                        f"{backup_dir}: {rollback_exc}",
                        file=sys.stderr,
                    )
            return False
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        return True
    except PermissionError:
        location = f" Backup retained at {backup_dir}." if backup_dir.exists() else ""
        print(
            f"[FAIL] Cannot replace {vendor_dir}: files are in use. "
            f"Close processes using the plugin and retry.{location}",
            file=sys.stderr,
        )
        return False
    except OSError as exc:
        location = f" Backup retained at {backup_dir}." if backup_dir.exists() else ""
        print(f"[FAIL] Failed to replace {vendor_dir}: {exc}.{location}", file=sys.stderr)
        return False

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
