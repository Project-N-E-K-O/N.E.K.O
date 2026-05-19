"""neko-plugin add / sync — manage plugin Python dependencies.

- ``add``: Install packages into vendor/ and add them to pyproject.toml
- ``sync``: Reinstall all declared dependencies into vendor/ from pyproject.toml
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    # neko-plugin add
    add_parser = subparsers.add_parser(
        "add",
        help="Add Python dependencies to a plugin (installs into vendor/ and updates pyproject.toml)",
    )
    plugin_arg = add_parser.add_argument(
        "plugin",
        help="Plugin directory name or path",
    )
    plugin_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    add_parser.add_argument(
        "packages",
        nargs="+",
        help="Package specifiers to add (e.g. httpx>=0.27 pydantic)",
    )
    add_parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for pip install",
    )
    add_parser.set_defaults(handler=handle_add, _defaults=defaults)

    # neko-plugin sync
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


def handle_add(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    try:
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    pyproject_path = plugin_dir / "pyproject.toml"
    packages: list[str] = args.packages

    # 1. Ensure pyproject.toml exists
    if not pyproject_path.is_file():
        print(f"[FAIL] {plugin_dir.name}: pyproject.toml not found. Run 'neko-plugin init' first.", file=sys.stderr)
        return 1

    # 2. Read current dependencies
    existing_deps = _read_dependencies(pyproject_path)

    # 3. Install into vendor/
    vendor_dir = plugin_dir / "vendor"
    all_deps = _merge_new_packages(existing_deps, packages)
    exit_code = _pip_install_to_vendor(
        all_deps,
        vendor_dir=vendor_dir,
        python=args.python,
    )
    if exit_code != 0:
        return exit_code

    # 4. Update pyproject.toml
    _update_pyproject_dependencies(pyproject_path, all_deps)

    # 5. Clean vendor artifacts
    _clean_vendor(vendor_dir)

    print(f"[OK] {plugin_dir.name}: added {', '.join(packages)}")
    print(f"  vendor={vendor_dir}")
    print(f"  dependencies={all_deps}")
    return 0


def handle_sync(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    try:
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    pyproject_path = plugin_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        print(f"[FAIL] {plugin_dir.name}: pyproject.toml not found.", file=sys.stderr)
        return 1

    # 1. Read declared dependencies
    all_deps = _read_dependencies(pyproject_path)
    external_deps = _filter_external(all_deps)
    if not external_deps:
        print(f"[OK] {plugin_dir.name}: no external dependencies to sync")
        return 0

    # 2. Optionally clean vendor/
    vendor_dir = plugin_dir / "vendor"
    if args.clean and vendor_dir.exists():
        shutil.rmtree(vendor_dir)

    # 3. Install all declared deps into vendor/
    exit_code = _pip_install_to_vendor(
        external_deps,
        vendor_dir=vendor_dir,
        python=args.python,
    )
    if exit_code != 0:
        return exit_code

    # 4. Clean vendor artifacts
    _clean_vendor(vendor_dir)

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


def _merge_new_packages(existing: list[str], new_packages: list[str]) -> list[str]:
    """Merge new packages into existing list, replacing if same name."""
    import re
    name_re = re.compile(r"[-_.]+")

    def canonical_name(spec: str) -> str:
        name = re.split(r"[<>=!~;\[\s@]", spec, maxsplit=1)[0].strip()
        return name_re.sub("-", name).lower()

    # Build map of existing deps by canonical name
    result_map: dict[str, str] = {}
    for dep in existing:
        result_map[canonical_name(dep)] = dep

    # Override/add new packages
    for pkg in new_packages:
        canon = canonical_name(pkg)
        if canon in _HOST_PROVIDED:
            continue
        result_map[canon] = pkg

    return sorted(result_map.values(), key=lambda d: d.lower())


def _pip_install_to_vendor(
    packages: list[str],
    *,
    vendor_dir: Path,
    python: str,
) -> int:
    """Run pip install --target vendor/ for the given packages."""
    if not packages:
        return 0

    vendor_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        python, "-m", "pip", "install",
        "--target", str(vendor_dir),
        "--upgrade",
        "--no-user",
        *packages,
    ]

    print(f"  running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(f"[FAIL] pip install failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1
    return 0


def _update_pyproject_dependencies(pyproject_path: Path, deps: list[str]) -> None:
    """Rewrite the dependencies list in pyproject.toml preserving structure."""
    content = pyproject_path.read_text(encoding="utf-8")

    # Simple approach: find and replace the dependencies = [...] block
    import re

    # Match dependencies = [...] (possibly multiline)
    pattern = re.compile(
        r"(dependencies\s*=\s*)\[([^\]]*)\]",
        re.DOTALL,
    )

    if pattern.search(content):
        # Format new dependencies list
        if not deps:
            replacement = r"\g<1>[]"
        else:
            lines = ",\n".join(f'  "{d}"' for d in sorted(deps, key=str.lower))
            replacement = f"\\g<1>[\n{lines},\n]"
        content = pattern.sub(replacement, content, count=1)
    else:
        # No dependencies field found — append under [project]
        project_pattern = re.compile(r"(\[project\][^\[]*)", re.DOTALL)
        match = project_pattern.search(content)
        if match:
            section = match.group(1).rstrip()
            lines = ",\n".join(f'  "{d}"' for d in sorted(deps, key=str.lower))
            section += f"\ndependencies = [\n{lines},\n]\n"
            content = project_pattern.sub(section, content, count=1)

    pyproject_path.write_text(content, encoding="utf-8", newline="\n")


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
    if bin_dir.is_dir():
        shutil.rmtree(bin_dir, ignore_errors=True)
