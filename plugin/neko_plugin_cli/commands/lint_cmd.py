"""neko-plugin lint — run the pinned repository ruff check on a plugin.

The pinned version and flags live in ``core.lint``; the Market CI workflows
run the same command text (a unit test keeps both token-identical), and
``precheck`` invokes it as its first gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from ..core.lint import ruff_command
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dir_candidate
from .release_cmd import _defaults_from_args


def register(subparsers: argparse._SubparsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser(
        "lint",
        help="Run the pinned ruff check used by Market CI on a plugin source",
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
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults = _defaults_from_args(args, defaults=args._defaults)
    try:
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    try:
        completed = subprocess.run(ruff_command(plugin_dir), text=True)
    except OSError as exc:
        print(f"[FAIL] ruff could not run: {exc}", file=sys.stderr)
        return 1
    return completed.returncode
