"""neko-plugin CLI entry point.

Can be invoked as:
  - ``python -m neko_plugin_cli`` (when installed)
  - ``python plugin/neko-plugin-cli/cli.py`` (legacy, via shim)
  - ``neko-plugin <command>`` (when installed via pip/uv with entry_points)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .commands import pack_cmd, inspect_cmd, verify_cmd, unpack_cmd, analyze_cmd
from .paths import resolve_default_paths


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    defaults = resolve_default_paths()

    parser = argparse.ArgumentParser(
        prog="neko-plugin",
        description="N.E.K.O plugin packaging and inspection CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    pack_cmd.register(subparsers, defaults=defaults)
    inspect_cmd.register(subparsers, defaults=defaults)
    verify_cmd.register(subparsers, defaults=defaults)
    unpack_cmd.register(subparsers, defaults=defaults)
    analyze_cmd.register(subparsers, defaults=defaults)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
