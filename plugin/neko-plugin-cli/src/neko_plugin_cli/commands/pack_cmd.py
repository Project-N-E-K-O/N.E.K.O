"""neko-plugin pack — package one or more plugins."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core import pack_bundle, pack_plugin
from ..paths import CliDefaults
from ._completers import PLUGIN_NAME_COMPLETER
from ._resolve import resolve_plugin_dirs


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("pack", help="Pack one plugin, multiple plugins, or all plugins")
    plugins_arg = parser.add_argument("plugins", nargs="*", help="Plugin directory names under plugin/plugins")
    plugins_arg.complete = PLUGIN_NAME_COMPLETER  # type: ignore[attr-defined]
    parser.add_argument("--all", action="store_true", help="Pack all plugins under plugin/plugins")
    parser.add_argument("--out", help="Output file path for a single packed plugin")
    parser.add_argument("--target-dir", default=str(defaults.target_dir), help="Output directory for packed plugin archives")
    parser.add_argument("--keep-staging", action="store_true", help="Keep staging directories and expose staged file paths in results")
    parser.add_argument("--bundle", action="store_true", help="Pack selected plugins into a single .neko-bundle archive")
    parser.add_argument("--bundle-id", help="Bundle package id")
    parser.add_argument("--package-name", help="Bundle package name")
    parser.add_argument("--package-description", help="Bundle package description")
    parser.add_argument("--version", help="Bundle package version")
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    plugin_dirs = resolve_plugin_dirs(plugin_names=args.plugins, pack_all=args.all, defaults=defaults)
    target_dir = Path(args.target_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if args.out and not (args.bundle or len(plugin_dirs) == 1):
        print("[FAIL] --out requires a single plugin or --bundle mode", file=sys.stderr)
        return 1

    if args.bundle or len(plugin_dirs) > 1:
        return _handle_bundle(args, plugin_dirs=plugin_dirs, target_dir=target_dir)

    return _handle_single(args, plugin_dirs=plugin_dirs, target_dir=target_dir)


def _handle_bundle(args: argparse.Namespace, *, plugin_dirs: list[Path], target_dir: Path) -> int:
    bundle_id = args.bundle_id or "__".join(sorted(item.name for item in plugin_dirs))
    output_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else target_dir / f"{bundle_id}.neko-bundle"
    )
    try:
        result = pack_bundle(
            plugin_dirs,
            output_path,
            bundle_id=bundle_id,
            package_name=args.package_name,
            package_description=args.package_description,
            version=args.version or "0.1.0",
            keep_staging=args.keep_staging,
        )
    except Exception as exc:
        print(f"[FAIL] bundle: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] {result.plugin_id} -> {result.package_path}")
    print(f"  package_type={result.package_type}")
    print(f"  plugin_count={result.plugin_count}")
    print(f"  plugins={', '.join(result.plugin_ids)}")
    if result.staging_dir is not None:
        print(f"  staging_dir={result.staging_dir}")
        print(f"  packaged_file_count={result.packaged_file_count}")
        print(f"  profile_file_count={result.profile_file_count}")
    print("Completed: packed=1, failed=0")
    return 0


def _handle_single(args: argparse.Namespace, *, plugin_dirs: list[Path], target_dir: Path) -> int:
    packed_count = 0
    failed_count = 0

    for plugin_dir in plugin_dirs:
        output_path = (
            Path(args.out).expanduser().resolve()
            if args.out
            else target_dir / f"{plugin_dir.name}.neko-plugin"
        )
        try:
            result = pack_plugin(plugin_dir, output_path, keep_staging=args.keep_staging)
        except Exception as exc:
            failed_count += 1
            print(f"[FAIL] {plugin_dir.name}: {exc}", file=sys.stderr)
            continue

        packed_count += 1
        print(f"[OK] {result.plugin_id} -> {result.package_path}")
        if result.staging_dir is not None:
            print(f"  staging_dir={result.staging_dir}")
            print(f"  packaged_file_count={result.packaged_file_count}")
            print(f"  profile_file_count={result.profile_file_count}")

    if failed_count:
        print(f"Completed with failures: packed={packed_count}, failed={failed_count}", file=sys.stderr)
        return 1

    print(f"Completed: packed={packed_count}, failed=0")
    return 0
