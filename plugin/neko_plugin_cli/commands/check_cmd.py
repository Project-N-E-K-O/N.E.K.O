"""neko-plugin check — unified plugin readiness checks."""

from __future__ import annotations

import argparse

from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from . import release_cmd


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser(
        "check",
        help="Check plugin readiness; use --release for pre-release checks",
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
        "-s",
        "--strict",
        action="store_true",
        help="Treat missing repository support files as errors",
    )
    parser.add_argument(
        "-r",
        "--release",
        action="store_true",
        help="Run strict pre-release checks, tests, package build, and hash verification",
    )
    parser.add_argument(
        "-t",
        "--target-dir",
        default=str(defaults.target_dir),
        help="Output directory for --release package checks",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Do not run plugin tests during --release checks",
    )
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    if args.release:
        args._command_label = "check --release"
        return release_cmd.handle_release_check(args)

    args._command_label = "check"
    return release_cmd.handle_check(args)
