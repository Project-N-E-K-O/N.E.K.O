"""Repository health and release readiness commands."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import inspect_package, pack_plugin
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate
from .validate_cmd import validate_plugin_dir


Issue = tuple[str, str]


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    doctor_parser = subparsers.add_parser("doctor", help="Diagnose plugin repository readiness")
    plugin_arg = doctor_parser.add_argument("plugin", help="Plugin directory name under plugin/plugins or explicit plugin path")
    plugin_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    doctor_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    doctor_parser.add_argument("--strict", action="store_true", help="Treat missing repository support files as errors")
    doctor_parser.set_defaults(handler=handle_doctor, _defaults=defaults)

    release_parser = subparsers.add_parser("release-check", help="Run strict pre-release checks for one plugin")
    release_arg = release_parser.add_argument("plugin", help="Plugin directory name under plugin/plugins or explicit plugin path")
    release_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    release_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    release_parser.add_argument("--target-dir", default=str(defaults.target_dir), help="Output directory for the checked package")
    release_parser.add_argument("--skip-tests", action="store_true", help="Do not run plugin tests")
    release_parser.set_defaults(handler=handle_release_check, _defaults=defaults)


def handle_doctor(args: argparse.Namespace) -> int:
    try:
        defaults = _defaults_from_args(args, defaults=args._defaults)
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
        source = load_plugin_source(plugin_dir)
        issues = validate_plugin_dir(plugin_dir, strict=args.strict)
        issues.extend(_diagnose_repository(plugin_dir))
    except Exception as exc:
        print(f"[FAIL] doctor: {exc}", file=sys.stderr)
        return 1

    errors = [issue for issue in issues if issue[0] == "error"]
    warnings = [issue for issue in issues if issue[0] == "warning"]

    status = "[FAIL]" if errors else "[OK]"
    stream = sys.stderr if errors else sys.stdout
    print(f"{status} {source.plugin_id}: doctor found {len(errors)} error(s), {len(warnings)} warning(s)", file=stream)
    print(f"  path={plugin_dir}")
    print(f"  version={source.version}")
    print(f"  entry={source.entry_point}")
    _print_issues(issues)
    return 1 if errors else 0


def handle_release_check(args: argparse.Namespace) -> int:
    try:
        defaults = _defaults_from_args(args, defaults=args._defaults)
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
        source = load_plugin_source(plugin_dir)
        issues = validate_plugin_dir(plugin_dir, strict=True)
        errors = [issue for issue in issues if issue[0] == "error"]
        if errors:
            print(f"[FAIL] {source.plugin_id}: release-check blocked by validation errors", file=sys.stderr)
            _print_issues(issues)
            return 1

        test_result = _run_tests(plugin_dir, skip_tests=args.skip_tests)
        target_dir = Path(args.target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        package_path = target_dir / f"{source.plugin_id}.neko-plugin"
        pack_result = pack_plugin(plugin_dir, package_path)
        inspect_result = inspect_package(pack_result.package_path)
        if inspect_result.payload_hash_verified is not True:
            print("[FAIL] package payload hash verification failed", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"[FAIL] release-check: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] {source.plugin_id}: release-check passed")
    print(f"  version={source.version}")
    print(f"  package={pack_result.package_path}")
    print(f"  package_sha256={_sha256_file(pack_result.package_path)}")
    print(f"  payload_hash={inspect_result.payload_hash}")
    print(f"  payload_hash_verified={inspect_result.payload_hash_verified}")
    print(f"  tests={test_result}")
    for severity, message in issues:
        if severity == "warning":
            print(f"  [WARNING] {message}")
    return 0


def _defaults_from_args(args: argparse.Namespace, *, defaults: CliDefaults) -> CliDefaults:
    plugins_root = getattr(args, "plugins_root", None)
    if not plugins_root:
        return defaults
    return CliDefaults(
        plugin_root=defaults.plugin_root,
        target_dir=defaults.target_dir,
        plugins_root=Path(plugins_root).expanduser().resolve(),
        profiles_root=defaults.profiles_root,
    )


def _diagnose_repository(plugin_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    if shutil.which("git") is None:
        return [("warning", "git executable not found")]

    if not (plugin_dir / ".git").exists():
        issues.append(("warning", "plugin directory is not a standalone git repository"))
        return issues

    remote = _run_git(["remote", "get-url", "origin"], cwd=plugin_dir)
    if remote.returncode != 0 or not remote.stdout.strip():
        issues.append(("warning", "git remote 'origin' is not configured"))

    status = _run_git(["status", "--porcelain"], cwd=plugin_dir)
    if status.returncode != 0:
        issues.append(("warning", "git status failed"))
    elif status.stdout.strip():
        issues.append(("warning", "git working tree has uncommitted changes"))

    return issues


def _run_tests(plugin_dir: Path, *, skip_tests: bool) -> str:
    tests_dir = plugin_dir / "tests"
    if skip_tests:
        return "skipped"
    if not tests_dir.is_dir():
        return "not-found"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir)],
        cwd=plugin_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        output = completed.stdout.strip()
        if output:
            print(output, file=sys.stderr)
        raise RuntimeError(f"tests failed with exit code {completed.returncode}")
    return "passed"


def _run_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *command],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_issues(issues: list[Issue]) -> None:
    for severity, message in issues:
        stream = sys.stderr if severity == "error" else sys.stdout
        print(f"  [{severity.upper()}] {message}", file=stream)
