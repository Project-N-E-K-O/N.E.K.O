"""neko-plugin validate - check plugin source repository conventions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..core import inspect_package, pack_plugin
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("validate", help="Internal validation command")
    subparsers._choices_actions = [  # type: ignore[attr-defined]
        action for action in subparsers._choices_actions  # type: ignore[attr-defined]
        if getattr(action, "dest", None) != "validate"
    ]
    plugin_arg = parser.add_argument("plugin", help="Plugin directory name under plugin/plugins or explicit plugin path")
    plugin_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    parser.add_argument("--strict", action="store_true", help="Require repository support files such as VSCode tasks and GitHub Actions")
    parser.add_argument("--pack", action="store_true", help="Also pack and verify the plugin package")
    parser.add_argument("--target-dir", default=str(defaults.target_dir), help="Output directory for --pack validation")
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    try:
        if args.plugins_root:
            defaults = CliDefaults(
                plugin_root=defaults.plugin_root,
                target_dir=defaults.target_dir,
                plugins_root=Path(args.plugins_root).expanduser().resolve(),
                profiles_root=defaults.profiles_root,
            )
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
        issues = validate_plugin_dir(plugin_dir, strict=args.strict)
        if args.pack:
            issues.extend(_validate_pack(plugin_dir, target_dir=Path(args.target_dir).expanduser().resolve()))
    except Exception as exc:
        print(f"[FAIL] validate: {exc}", file=sys.stderr)
        return 1

    errors = [issue for issue in issues if issue[0] == "error"]
    warnings = [issue for issue in issues if issue[0] == "warning"]

    if errors:
        print(f"[FAIL] {plugin_dir.name}: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        for severity, message in issues:
            stream = sys.stderr if severity == "error" else sys.stdout
            print(f"  [{severity.upper()}] {message}", file=stream)
        return 1

    print(f"[OK] {plugin_dir.name}: validation passed")
    for _severity, message in warnings:
        print(f"  [WARNING] {message}")
    return 0


def validate_plugin_dir(plugin_dir: Path, *, strict: bool = False) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    source = load_plugin_source(plugin_dir)
    plugin_table = source.plugin_table

    if source.plugin_id != plugin_dir.name:
        issues.append(("warning", f"plugin.id '{source.plugin_id}' does not match directory name '{plugin_dir.name}'"))

    entry = source.entry_point
    if not entry:
        issues.append(("error", "plugin.entry is missing"))
    else:
        expected_prefix = f"plugin.plugins.{source.plugin_id}:"
        if not entry.startswith(expected_prefix):
            issues.append(("warning", f"plugin.entry should usually start with '{expected_prefix}', got '{entry}'"))

    if not plugin_table.get("sdk"):
        issues.append(("warning", "[plugin.sdk] is missing"))

    _check_optional_file(plugin_dir / "README.md", "README.md", issues, strict=strict)
    _check_optional_file(plugin_dir / "tests" / "test_smoke.py", "tests/test_smoke.py", issues, strict=strict)
    _check_optional_file(plugin_dir / "pyproject.toml", "pyproject.toml", issues, strict=False)

    _check_json_file(plugin_dir / ".vscode" / "settings.json", ".vscode/settings.json", issues, strict=strict)
    _check_json_file(plugin_dir / ".vscode" / "tasks.json", ".vscode/tasks.json", issues, strict=strict)
    _check_optional_file(plugin_dir / ".github" / "workflows" / "verify.yml", ".github/workflows/verify.yml", issues, strict=strict)

    _check_gitignore(plugin_dir / ".gitignore", issues, strict=strict)

    return issues


def _validate_pack(plugin_dir: Path, *, target_dir: Path) -> list[tuple[str, str]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    package_path = target_dir / f"{plugin_dir.name}.neko-plugin"
    result = pack_plugin(plugin_dir, package_path)
    inspect_result = inspect_package(result.package_path)
    if inspect_result.payload_hash_verified is not True:
        return [("error", "package payload hash verification failed")]
    return []


def _check_optional_file(path: Path, label: str, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if path.is_file():
        return
    issues.append(("error" if strict else "warning", f"{label} is missing"))


def _check_json_file(path: Path, label: str, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if not path.is_file():
        issues.append(("error" if strict else "warning", f"{label} is missing"))
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(("error", f"{label} is invalid JSON: {exc}"))


def _check_gitignore(path: Path, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if not path.is_file():
        issues.append(("error" if strict else "warning", ".gitignore is missing"))
        return

    text = path.read_text(encoding="utf-8")
    required_patterns = ["__pycache__/", ".pytest_cache/", "store.db"]
    for pattern in required_patterns:
        if not re.search(rf"(^|\n){re.escape(pattern)}($|\n)", text):
            issues.append(("warning", f".gitignore should include {pattern}"))
