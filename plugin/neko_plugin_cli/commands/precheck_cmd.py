"""neko-plugin precheck — aggregate the Market CI gates locally.

Runs the same three gates the Market verify workflow runs, in the same
order and as separate CLI subprocesses, so a failure here reads exactly
like the failure CI would report:

1. ``lint``        — the pinned repository ruff check
2. ``sync --clean`` — vendor/ dependency materialization
3. ``check -r``    — strict validation, plugin tests, package build and hash

``--json`` emits one machine-readable report (used by the plugin manager
workbench); without it the stage output streams through untouched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.lint import RUFF_LINT_SPEC
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate
from .release_cmd import _diagnose_repository, _print_issues

STAGE_LINT = "lint"
STAGE_SYNC = "sync"
STAGE_RELEASE_CHECK = "release-check"


@dataclass
class StageResult:
    name: str
    passed: bool
    output: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "output": self.output}


def register(subparsers: argparse._SubparsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser(
        "precheck",
        help="Run the Market CI gates (lint, sync, release check) against a plugin source",
    )
    plugin_arg = parser.add_argument(
        "plugin",
        help="Plugin directory name under plugin/plugins or explicit plugin path",
    )
    plugin_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    parser.add_argument(
        "--plugins-root",
        help="Plugin root directory (default: N.E.K.O/plugin/plugins)",
    )
    parser.add_argument(
        "-t",
        "--target-dir",
        default=str(defaults.target_dir),
        help="Output directory for the release-check package build",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Do not run plugin tests during the release-check stage",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a single JSON report instead of streaming stage output",
    )
    parser.set_defaults(handler=handle, _defaults=defaults)


def _stage_argv(stage: str, plugin_dir_arg: str, args: argparse.Namespace) -> list[str]:
    base = [sys.executable, "-m", "plugin.neko_plugin_cli.cli"]
    if stage == STAGE_LINT:
        return [*base, "lint", plugin_dir_arg]
    if stage == STAGE_SYNC:
        return [*base, "sync", plugin_dir_arg, "--clean"]
    return [
        *base,
        "check",
        plugin_dir_arg,
        "--release",
        "--target-dir",
        str(Path(args.target_dir).expanduser().resolve()),
        *(["--skip-tests"] if args.skip_tests else []),
    ]


def _run_stage(stage: str, plugin_dir_arg: str, args: argparse.Namespace, *, capture: bool) -> StageResult:
    try:
        completed = subprocess.run(
            _stage_argv(stage, plugin_dir_arg, args),
            text=True,
            capture_output=capture,
            cwd=args._defaults.repo_root,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        # A missing interpreter or executor is a gate failure, not a crash.
        return StageResult(name=stage, passed=False, output=str(exc))
    output = (completed.stdout or "") + (completed.stderr or "") if capture else ""
    return StageResult(name=stage, passed=completed.returncode == 0, output=output)


def handle(args: argparse.Namespace) -> int:
    defaults = args._defaults.with_plugins_root(getattr(args, "plugins_root", None))
    capture = bool(args.as_json)
    try:
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
    except Exception as exc:
        # Keep the report shape stable: the failure reads as a failed lint gate.
        report = {
            "plugin_id": None,
            "version": None,
            "plugin_dir": str(args.plugin),
            "passed": False,
            "ruff_version": RUFF_LINT_SPEC.version,
            "environment_issues": [],
            "stages": [StageResult(STAGE_LINT, False, str(exc)).as_dict()],
        }
        if capture:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    plugin_dir_arg = str(plugin_dir)
    stages = [_run_stage(STAGE_LINT, plugin_dir_arg, args, capture=capture)]
    if stages[-1].passed:
        stages.append(_run_stage(STAGE_SYNC, plugin_dir_arg, args, capture=capture))
    if all(stage.passed for stage in stages):
        stages.append(_run_stage(STAGE_RELEASE_CHECK, plugin_dir_arg, args, capture=capture))
    passed = all(stage.passed for stage in stages)
    environment_issues = _diagnose_repository(plugin_dir, defaults=defaults)

    if not capture:
        _print_issues(environment_issues, plugin_dir=plugin_dir, show_fixes=True)
        status = "[OK]" if passed else "[FAIL]"
        print(f"{status} {plugin_dir.name}: precheck {'passed' if passed else 'failed'}")
        return 0 if passed else 1

    try:
        source = load_plugin_source(plugin_dir)
        plugin_id: str | None = source.plugin_id
        version: str | None = source.version
    except Exception:
        plugin_id = None
        version = None
    report = {
        "plugin_id": plugin_id,
        "version": version,
        "plugin_dir": str(plugin_dir),
        "passed": passed,
        "ruff_version": RUFF_LINT_SPEC.version,
        "environment_issues": [list(issue) for issue in environment_issues],
        "stages": [stage.as_dict() for stage in stages],
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1
