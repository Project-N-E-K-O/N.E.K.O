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
    requested: list[str] = args.packages

    # 1. Ensure pyproject.toml exists
    if not pyproject_path.is_file():
        print(f"[FAIL] {plugin_dir.name}: pyproject.toml not found. Run 'neko-plugin init' first.", file=sys.stderr)
        return 1

    # 2. Filter out host-provided packages BEFORE we touch pyproject.toml or
    #    pip. Bug 1.22 (PR #1480 review-fix): the previous flow merged
    #    ``packages`` into ``existing_deps`` via ``_merge_new_packages``, which
    #    silently dropped host-provided packages (canonical name in
    #    ``_HOST_PROVIDED``) inside its loop. The merge logic kept those
    #    packages out of ``vendor/`` and out of ``pyproject.toml`` correctly,
    #    but the success message ``added {requested}`` still echoed them
    #    back to the user — making it look like they had been installed when
    #    they had been silently discarded. Hoisting the filter here means the
    #    user sees an explicit ``[WARN]`` for each dropped package and the
    #    success message only lists what actually took effect.
    import re as _re
    _name_re = _re.compile(r"[-_.]+")

    def _canonical(spec: str) -> str:
        name = _re.split(r"[<>=!~;\[\s@]", spec, maxsplit=1)[0].strip()
        return _name_re.sub("-", name).lower()

    effective: list[str] = []
    for pkg in requested:
        if _canonical(pkg) in _HOST_PROVIDED:
            print(
                f"[WARN] {plugin_dir.name}: skipping host-provided package: {pkg}",
                file=sys.stderr,
            )
            continue
        effective.append(pkg)

    if not effective:
        # All packages were host-provided. Nothing to install or write — but
        # the user did request a no-op explicitly, so the exit code is 0.
        print(
            f"[OK] {plugin_dir.name}: no packages to add "
            f"(all {len(requested)} requested package(s) are host-provided)"
        )
        return 0

    # 3. Read current dependencies
    existing_deps = _read_dependencies(pyproject_path)

    # 4. Install into vendor/
    vendor_dir = plugin_dir / "vendor"
    all_deps = _merge_new_packages(existing_deps, effective)
    exit_code = _pip_install_to_vendor(
        all_deps,
        vendor_dir=vendor_dir,
        python=args.python,
    )
    if exit_code != 0:
        return exit_code

    # 5. Update pyproject.toml
    _update_pyproject_dependencies(pyproject_path, all_deps)

    # 6. Clean vendor artifacts
    _clean_vendor(vendor_dir)

    print(f"[OK] {plugin_dir.name}: added {', '.join(effective)}")
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
    """Rewrite ``[project].dependencies`` preserving file structure.

    Bug 1.23 (PR #1480 review-fix): the previous implementation used a
    single unscoped regex ``dependencies\\s*=\\s*\\[...\\]`` and called
    ``pattern.sub(..., count=1)``, which matched the FIRST occurrence in
    document order. TOML allows the literal substring ``dependencies =
    [...]`` in many other tables (``[tool.uv]``, ``[tool.poetry.group.*]``,
    arbitrary ``[tool.<vendor>]`` tables, ...). Whenever any such table
    appeared *before* ``[project]`` in source order, the rewrite clobbered
    the unrelated section while ``[project].dependencies`` itself stayed
    untouched. The exploration test
    ``plugin/tests/unit/test_neko_plugin_cli_deps_regex_unscoped_exploration.py``
    pins the failure mode.

    Strategy
    --------

    1. Locate the ``[project]`` section header and its end (next top-level
       ``^\\[`` table header or end of file).
    2. Within that range — and ONLY that range — locate the existing
       ``dependencies = [...]`` field, multi-line aware. Replace just
       that field. If absent, append a ``dependencies = ...`` line at
       the end of the section.
    3. Re-parse the rewritten content with ``tomllib`` and assert that
       ``data['project']['dependencies']`` equals the sorted ``deps``
       list. If the rewrite failed for any reason (mis-scoped match,
       malformed TOML, ...), raise ``RuntimeError`` and DO NOT write the
       file. This makes silent corruption of pyproject.toml impossible.
    """

    content = pyproject_path.read_text(encoding="utf-8")
    sorted_deps = sorted(deps, key=str.lower)
    deps_body = (
        "[]"
        if not sorted_deps
        else "[\n" + ",\n".join(f'  "{d}"' for d in sorted_deps) + ",\n]"
    )

    import re

    # Locate the [project] table. ``(?ms)`` enables multi-line and
    # dot-all so ``.*?`` consumes newlines lazily up to the next ``^[``
    # header (any TOML table) or end of file. Section bounds are
    # captured for slice-and-rewrite below.
    section_re = re.compile(
        r"(?ms)^\[project\]\s*\n(?P<body>.*?)(?=^\[|\Z)"
    )
    section_match = section_re.search(content)
    if section_match is None:
        # No [project] section at all. PEP 621 requires one for any
        # reasonable plugin pyproject, but we also can't safely fabricate
        # the rest of the metadata block, so append a minimal table.
        appended_section = f"\n[project]\ndependencies = {deps_body}\n"
        new_content = content.rstrip() + appended_section + "\n"
    else:
        section_text = section_match.group(0)
        body_start, body_end = section_match.start(), section_match.end()

        # Scoped regex: only matches ``dependencies = [...]`` when it sits
        # at the start of a line (after optional whitespace) inside the
        # ``[project]`` body. ``re.MULTILINE | re.DOTALL`` so ``^`` is
        # line-start and ``.*?`` can span quoted multi-line arrays.
        deps_re = re.compile(
            r"(?ms)^(?P<lead>[ \t]*)dependencies\s*=\s*\[.*?\]"
        )
        deps_match = deps_re.search(section_text)
        if deps_match is not None:
            lead = deps_match.group("lead")
            replacement = f"{lead}dependencies = {deps_body}"
            new_section = (
                section_text[: deps_match.start()]
                + replacement
                + section_text[deps_match.end():]
            )
        else:
            # Section exists but no dependencies field. Append on its
            # own line, preserving the trailing newline before the next
            # section header (or EOF).
            stripped = section_text.rstrip()
            new_section = stripped + f"\ndependencies = {deps_body}\n"
            # Preserve the original whitespace after the section so we
            # don't collapse blank lines that separated sections.
            trailing = section_text[len(stripped):]
            if not trailing.endswith("\n"):
                trailing = trailing + "\n"
            new_section = stripped + f"\ndependencies = {deps_body}" + trailing

        new_content = content[:body_start] + new_section + content[body_end:]

    # Defensive re-parse: if any of the above branches produced something
    # that doesn't round-trip through tomllib, abort BEFORE writing so we
    # don't leave the user's pyproject.toml in a half-rewritten state.
    try:
        parsed = tomllib.loads(new_content)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(
            f"deps_cmd: rewrite produced invalid TOML for {pyproject_path}: {exc}"
        ) from exc
    actual = parsed.get("project", {}).get("dependencies")
    if not isinstance(actual, list) or list(actual) != sorted_deps:
        raise RuntimeError(
            f"deps_cmd: rewrite mismatch for {pyproject_path}: "
            f"expected [project].dependencies={sorted_deps!r}, got {actual!r}"
        )

    pyproject_path.write_text(new_content, encoding="utf-8", newline="\n")


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
