"""neko-plugin install — install a package archive into plugin roots."""

from __future__ import annotations

import argparse
import sys

from ..core import install_package
from ..paths import CliDefaults
from ._completers import PACKAGE_FILE_COMPLETER
from ._resolve import resolve_package_path


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("install", help="Install a package archive")
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
    defaults: CliDefaults = args._defaults

    try:
        package_path = resolve_package_path(args.package, defaults=defaults)
    except Exception as exc:
        print(f"[FAIL] {args.package}: {exc}", file=sys.stderr)
        return 1

    try:
        result = install_package(
            package_path,
            plugins_root=args.plugins_root,
            profiles_root=args.profiles_root,
            on_conflict=args.on_conflict,
        )
    except Exception as exc:
        print(f"[FAIL] {package_path}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] package={result.package_path}")
    print(f"  type={result.package_type}")
    print(f"  id={result.package_id}")
    print(f"  plugins_root={result.plugins_root}")
    print(f"  conflict_strategy={result.conflict_strategy}")
    print(f"  metadata_found={result.metadata_found}")
    if result.payload_hash:
        print(f"  payload_hash={result.payload_hash}")
    if result.payload_hash_verified is not None:
        print(f"  payload_hash_verified={result.payload_hash_verified}")
    for item in result.installed_plugins:
        suffix = " (renamed)" if item.renamed else ""
        print(f"  plugin: {item.source_folder} -> {item.target_dir.name}{suffix}")
    if result.profile_dir is not None:
        print(f"  profiles={result.profile_dir}")
    return 0
