"""neko-plugin validate - check plugin source repository conventions."""

from __future__ import annotations

import argparse
import ast
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
        _check_entry_target(plugin_dir, source.plugin_id, entry, source.package_type, issues)

    if not plugin_table.get("sdk"):
        issues.append(("warning", "[plugin.sdk] is missing"))

    _check_optional_file(plugin_dir / "README.md", "README.md", issues, strict=strict)
    _check_optional_file(plugin_dir / "tests" / "test_smoke.py", "tests/test_smoke.py", issues, strict=strict)
    _check_optional_file(plugin_dir / "pyproject.toml", "pyproject.toml", issues, strict=False)

    _check_json_file(plugin_dir / ".vscode" / "settings.json", ".vscode/settings.json", issues, strict=strict)
    _check_json_file(plugin_dir / ".vscode" / "tasks.json", ".vscode/tasks.json", issues, strict=strict)
    _check_optional_file(plugin_dir / ".github" / "workflows" / "verify.yml", ".github/workflows/verify.yml", issues, strict=strict)

    _check_gitignore(plugin_dir / ".gitignore", issues, strict=strict)
    _check_python_decorators(plugin_dir, issues)

    return issues


def _check_entry_target(
    plugin_dir: Path,
    plugin_id: str,
    entry: str,
    package_type: str,
    issues: list[tuple[str, str]],
) -> None:
    if ":" not in entry:
        issues.append(("error", "plugin.entry must use 'module:ClassName' format"))
        return

    module_name, class_name = (part.strip() for part in entry.split(":", 1))
    if not module_name or not class_name:
        issues.append(("error", "plugin.entry must include both module and class name"))
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", module_name):
        issues.append(("error", f"plugin.entry module path is invalid: {module_name}"))
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_name):
        issues.append(("error", f"plugin.entry class name is invalid: {class_name}"))
        return

    module_path = _resolve_entry_module_path(plugin_dir, plugin_id, module_name)
    if module_path is None:
        issues.append(("warning", f"plugin.entry module '{module_name}' is outside the plugin directory; static entry checks skipped"))
        return
    if not module_path.is_file():
        issues.append(("error", f"plugin.entry module file is missing: {module_path.relative_to(plugin_dir)}"))
        return

    tree = _parse_python_file(module_path, issues, label=str(module_path.relative_to(plugin_dir)))
    if tree is None:
        return

    class_node = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
    if class_node is None:
        issues.append(("error", f"plugin.entry class '{class_name}' was not found in {module_path.relative_to(plugin_dir)}"))
        return

    if not _has_decorator(class_node.decorator_list, "neko_plugin"):
        issues.append(("error", f"plugin.entry class '{class_name}' must be decorated with @neko_plugin"))

    expected_bases = {
        "plugin": {"NekoPluginBase"},
        "adapter": {"NekoAdapterPlugin"},
        "extension": {"NekoExtensionBase"},
    }.get(package_type, {"NekoPluginBase"})
    actual_bases = {_name_of(base) for base in class_node.bases}
    if expected_bases and actual_bases.isdisjoint(expected_bases):
        issues.append(("warning", f"plugin.entry class '{class_name}' should inherit one of: {', '.join(sorted(expected_bases))}"))

    lifecycle_ids = _decorator_ids_in_class(class_node, "lifecycle")
    if "startup" not in lifecycle_ids:
        issues.append(("warning", "plugin.entry class should define @lifecycle(id=\"startup\")"))
    if "shutdown" not in lifecycle_ids:
        issues.append(("warning", "plugin.entry class should define @lifecycle(id=\"shutdown\")"))


def _resolve_entry_module_path(plugin_dir: Path, plugin_id: str, module_name: str) -> Path | None:
    prefix = f"plugin.plugins.{plugin_id}"
    if module_name == prefix:
        return plugin_dir / "__init__.py"
    if module_name.startswith(prefix + "."):
        parts = module_name.removeprefix(prefix + ".").split(".")
        base = plugin_dir.joinpath(*parts)
        file_path = base.with_suffix(".py")
        return file_path if file_path.exists() else base / "__init__.py"
    if "." not in module_name:
        base = plugin_dir / module_name
        file_path = base.with_suffix(".py")
        return file_path if file_path.exists() else base / "__init__.py"
    return None


def _check_python_decorators(plugin_dir: Path, issues: list[tuple[str, str]]) -> None:
    seen_ids: dict[str, str] = {}
    for path in sorted(plugin_dir.rglob("*.py")):
        relative = path.relative_to(plugin_dir)
        if any(part in {"__pycache__", ".venv", "venv"} for part in relative.parts):
            continue
        tree = _parse_python_file(path, issues, label=str(relative))
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name == "plugin_entry":
                    entry_id = _decorator_keyword_string(decorator, "id") or node.name
                    _check_identifier(entry_id, f"@plugin_entry id in {relative}:{node.lineno}", issues)
                    _check_schema_keyword(decorator, "input_schema", f"{relative}:{node.lineno}", issues)
                    _check_mutually_exclusive(decorator, {"input_schema", "params"}, f"@plugin_entry in {relative}:{node.lineno}", issues)
                    _check_mutually_exclusive(decorator, {"llm_result_fields", "llm_result_model", "fields"}, f"@plugin_entry in {relative}:{node.lineno}", issues)
                    previous = seen_ids.get(entry_id)
                    location = f"{relative}:{node.lineno}"
                    if previous:
                        issues.append(("warning", f"duplicate @plugin_entry id '{entry_id}' in {location}; first seen at {previous}"))
                    else:
                        seen_ids[entry_id] = location
                elif name == "lifecycle":
                    lifecycle_id = _decorator_keyword_string(decorator, "id")
                    allowed = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}
                    if not lifecycle_id:
                        issues.append(("error", f"@lifecycle in {relative}:{node.lineno} must declare a non-empty id"))
                    elif lifecycle_id not in allowed:
                        issues.append(("error", f"@lifecycle id '{lifecycle_id}' in {relative}:{node.lineno} must be one of: {', '.join(sorted(allowed))}"))
                elif name == "timer_interval":
                    timer_id = _decorator_keyword_string(decorator, "id")
                    if not timer_id:
                        issues.append(("error", f"@timer_interval in {relative}:{node.lineno} must declare a non-empty id"))
                    seconds = _decorator_keyword_literal(decorator, "seconds")
                    if not isinstance(seconds, int) or seconds <= 0:
                        issues.append(("error", f"@timer_interval in {relative}:{node.lineno} must declare seconds > 0"))
                elif name == "message":
                    message_id = _decorator_keyword_string(decorator, "id")
                    if not message_id:
                        issues.append(("error", f"@message in {relative}:{node.lineno} must declare a non-empty id"))
                    _check_schema_keyword(decorator, "input_schema", f"{relative}:{node.lineno}", issues)


def _parse_python_file(path: Path, issues: list[tuple[str, str]], *, label: str) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        issues.append(("error", f"Python syntax error in {label}: {exc.msg} at line {exc.lineno}"))
    except UnicodeDecodeError as exc:
        issues.append(("error", f"Python file is not valid UTF-8: {label}: {exc}"))
    return None


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return _name_of(target)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _has_decorator(decorators: list[ast.expr], name: str) -> bool:
    return any(_decorator_name(item) == name for item in decorators)


def _decorator_ids_in_class(class_node: ast.ClassDef, decorator_name: str) -> set[str]:
    ids: set[str] = set()
    for item in class_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in item.decorator_list:
            if _decorator_name(decorator) == decorator_name:
                value = _decorator_keyword_string(decorator, "id")
                if value:
                    ids.add(value)
    return ids


def _decorator_keyword_string(decorator: ast.expr, keyword: str) -> str:
    value = _decorator_keyword_literal(decorator, keyword)
    return value.strip() if isinstance(value, str) else ""


def _decorator_keyword_literal(decorator: ast.expr, keyword: str) -> object:
    if not isinstance(decorator, ast.Call):
        return None
    for item in decorator.keywords:
        if item.arg == keyword:
            try:
                return ast.literal_eval(item.value)
            except (ValueError, TypeError):
                return None
    return None


def _decorator_keywords(decorator: ast.expr) -> set[str]:
    if not isinstance(decorator, ast.Call):
        return set()
    return {item.arg for item in decorator.keywords if item.arg}


def _check_identifier(value: str, label: str, issues: list[tuple[str, str]]) -> None:
    if not value:
        issues.append(("error", f"{label} must be non-empty"))
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        issues.append(("warning", f"{label} should contain only A-Z, a-z, 0-9, _, -"))


def _check_schema_keyword(decorator: ast.expr, keyword: str, label: str, issues: list[tuple[str, str]]) -> None:
    value = _decorator_keyword_literal(decorator, keyword)
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", f"{keyword} in {label} must be a dict literal when statically declared"))
        return
    if value.get("type") != "object":
        issues.append(("warning", f"{keyword} in {label} should declare type='object'"))
    properties = value.get("properties")
    if properties is not None and not isinstance(properties, dict):
        issues.append(("error", f"{keyword}.properties in {label} must be an object"))
    required = value.get("required")
    if required is not None and not isinstance(required, list):
        issues.append(("error", f"{keyword}.required in {label} must be a list"))


def _check_mutually_exclusive(decorator: ast.expr, names: set[str], label: str, issues: list[tuple[str, str]]) -> None:
    present = names & _decorator_keywords(decorator)
    if len(present) > 1:
        issues.append(("error", f"{label} cannot combine mutually exclusive options: {', '.join(sorted(present))}"))


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
