"""neko-plugin verify — verify package payload hash."""

from __future__ import annotations

import argparse
import sys

from ..core import inspect_package
from ..paths import CliDefaults
from ._completers import PACKAGE_FILE_COMPLETER
from ._resolve import resolve_package_path


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("verify", help="Verify package payload hash")
    pkg_arg = parser.add_argument("package", help="Package file path or filename under target/")
    pkg_arg.complete = PACKAGE_FILE_COMPLETER  # type: ignore[attr-defined]
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    package_path = resolve_package_path(args.package, defaults=defaults)

    try:
        result = inspect_package(package_path)
    except Exception as exc:
        print(f"[FAIL] {package_path}: {exc}", file=sys.stderr)
        return 1

    status = "[OK]" if result.payload_hash_verified is True else "[FAIL]"
    print(f"{status} package={result.package_path}")
    print(f"  metadata_found={result.metadata_found}")
    print(f"  payload_hash={result.payload_hash}")
    print(f"  payload_hash_verified={result.payload_hash_verified}")

    if result.payload_hash_verified is True:
        return 0

    if result.payload_hash_verified is None:
        print(
            "[FAIL] metadata.toml is missing from the package, so the payload hash "
            "could not be verified. The package may still be valid, but its integrity "
            "cannot be confirmed. Re-pack the plugin to include metadata.toml.",
            file=sys.stderr,
        )
    else:
        print(
            "[FAIL] payload hash verification failed: the hash computed from the "
            "archive content does not match the hash stored in metadata.toml. "
            "This can happen when:\n"
            "  - the package was built on a different OS (e.g. Windows) with an older\n"
            "    version of neko_plugin_cli that had cross-platform sorting issues\n"
            "  - the archive was modified or corrupted after packaging\n"
            "  - the plugin source changed but the package was not re-built\n"
            "Try re-packing the plugin with the latest neko_plugin_cli.",
            file=sys.stderr,
        )
    return 1
