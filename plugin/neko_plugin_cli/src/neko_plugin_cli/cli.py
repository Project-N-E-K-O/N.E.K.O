"""neko-plugin CLI entry point.

Can be invoked as:
  - ``python -m neko_plugin_cli`` (when installed)
  - ``python plugin/neko_plugin_cli/cli.py`` (legacy, via shim)
  - ``neko-plugin <command>`` (when installed via pip/uv with entry_points)

Shell completion (requires ``shtab``)::

    # zsh
    neko-plugin --print-completion zsh > ~/.zsh/completions/_neko-plugin
    # bash
    neko-plugin --print-completion bash > ~/.bash_completion.d/neko-plugin
    # fish (not yet supported by shtab)
"""

from __future__ import annotations

import argparse
import textwrap

from .commands import pack_cmd, inspect_cmd, verify_cmd, unpack_cmd, analyze_cmd, init_cmd, validate_cmd, release_cmd
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
        description="N.E.K.O plugin development, packaging, and release CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Recommended workflow:
              neko-plugin init-repo <plugin>       Create a standalone plugin repo
              neko-plugin setup-repo <plugin>      Adopt an existing plugin directory
              neko-plugin doctor <plugin>          Diagnose local repo readiness
              neko-plugin release-check <plugin>   Run the pre-release check used by CI

            Advanced/debug commands:
              pack, inspect, verify, unpack, analyze
            """
        ),
    )

    # Shell completion support (optional dependency).
    try:
        import shtab
        shtab.add_argument_to(parser)
    except ImportError:
        pass

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init_cmd.register(subparsers, defaults=defaults)
    validate_cmd.register(subparsers, defaults=defaults)
    release_cmd.register(subparsers, defaults=defaults)
    pack_cmd.register(subparsers, defaults=defaults)
    inspect_cmd.register(subparsers, defaults=defaults)
    verify_cmd.register(subparsers, defaults=defaults)
    unpack_cmd.register(subparsers, defaults=defaults)
    analyze_cmd.register(subparsers, defaults=defaults)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
