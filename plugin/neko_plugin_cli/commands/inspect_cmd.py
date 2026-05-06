"""neko-plugin inspect — read-only package inspection."""

from __future__ import annotations

import argparse
import sys

from ..core import inspect_package
from ..paths import CliDefaults
from ._completers import PACKAGE_FILE_COMPLETER
from ._resolve import resolve_package_path


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("inspect", help="Inspect a package archive")
    pkg_arg = parser.add_argument("package", help="Package file path or filename under target/")
    pkg_arg.complete = PACKAGE_FILE_COMPLETER  # type: ignore[attr-defined]
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults

    try:
        package_path = resolve_package_path(args.package, defaults=defaults)
        result = inspect_package(package_path)
    except Exception as exc:
        print(f"[FAIL] {args.package}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] package={result.package_path}")
    print(f"  type={result.package_type}")
    print(f"  id={result.package_id}")
    if result.schema_version:
        print(f"  schema_version={result.schema_version}")
    if result.package_name:
        print(f"  package_name={result.package_name}")
    if result.version:
        print(f"  version={result.version}")
    if result.package_description:
        print(f"  package_description={result.package_description}")
    print(f"  metadata_found={result.metadata_found}")
    if result.payload_hash:
        print(f"  payload_hash={result.payload_hash}")
    if result.payload_hash_verified is not None:
        print(f"  payload_hash_verified={result.payload_hash_verified}")
    for item in result.plugins:
        print(f"  plugin: {item.plugin_id} -> {item.archive_path}")
    for profile_name in result.profile_names:
        print(f"  profile: {profile_name}")
    return 0
