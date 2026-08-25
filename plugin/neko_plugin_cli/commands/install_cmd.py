"""Compatibility command that directs runtime installs to Plugin Center."""

from __future__ import annotations

import argparse
import sys

from ..paths import CliDefaults
from ._completers import PACKAGE_FILE_COMPLETER


_RUNTIME_INSTALL_DISABLED_MESSAGE = (
    "neko-plugin install does not write plugin runtime directories. "
    "Import the .neko-plugin or .neko-bundle file from the N.E.K.O Plugin Center "
    "so installation, confirmation, rollback, and source tracking use one safe workflow."
)


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser(
        "install",
        help="Show how to import a package through the N.E.K.O Plugin Center",
    )
    pkg_arg = parser.add_argument("package", help="Package file path or filename under target/")
    pkg_arg.complete = PACKAGE_FILE_COMPLETER  # type: ignore[attr-defined]
    parser.add_argument("--plugins-root", default=str(defaults.plugins_root), help="Destination root for extracted plugin directories")
    parser.add_argument("--profiles-root", default=str(defaults.profiles_root), help="Destination root for extracted package profiles")
    parser.add_argument(
        "--on-conflict",
        choices=("fail",),
        default="fail",
        help="Abort if an executable plugin directory already exists",
    )
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    del args
    print(f"[DISABLED] {_RUNTIME_INSTALL_DISABLED_MESSAGE}", file=sys.stderr)
    return 2
