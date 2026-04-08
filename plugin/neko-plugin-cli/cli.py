from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()
PLUGIN_ROOT = REPO_ROOT / "plugin" / "plugins"
TARGET_DIR = SCRIPT_DIR / "target"
DEFAULT_PLUGINS_ROOT = REPO_ROOT / "plugin" / "plugins"
DEFAULT_PROFILES_ROOT = REPO_ROOT / "plugin" / ".neko-package-profiles"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public import analyze_bundle_plugins, inspect_package, pack_bundle, pack_plugin, unpack_package


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neko-plugin",
        description="N.E.K.O plugin packaging and inspection CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    pack_parser = subparsers.add_parser("pack", help="Pack one plugin, multiple plugins, or all plugins")
    pack_parser.add_argument(
        "plugins",
        nargs="*",
        help="Plugin directory names under plugin/plugins",
    )
    pack_parser.add_argument(
        "--all",
        action="store_true",
        help="Pack all plugins under plugin/plugins",
    )
    pack_parser.add_argument(
        "--out",
        help="Output file path for a single packed plugin",
    )
    pack_parser.add_argument(
        "--target-dir",
        default=str(TARGET_DIR),
        help="Output directory for packed plugin archives",
    )
    pack_parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="Keep staging directories and expose staged file paths in results",
    )
    pack_parser.add_argument(
        "--bundle",
        action="store_true",
        help="Pack selected plugins into a single .neko-bundle archive",
    )
    pack_parser.add_argument("--bundle-id", help="Bundle package id")
    pack_parser.add_argument("--package-name", help="Bundle package name")
    pack_parser.add_argument("--package-description", help="Bundle package description")
    pack_parser.add_argument("--version", help="Bundle package version")
    pack_parser.set_defaults(handler=handle_pack)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a package archive")
    inspect_parser.add_argument(
        "package",
        help="Package file path or package file name under plugin/neko-plugin-cli/target",
    )
    inspect_parser.set_defaults(handler=handle_inspect)

    verify_parser = subparsers.add_parser("verify", help="Verify package payload hash")
    verify_parser.add_argument(
        "package",
        help="Package file path or package file name under plugin/neko-plugin-cli/target",
    )
    verify_parser.set_defaults(handler=handle_verify)

    unpack_parser = subparsers.add_parser("unpack", help="Unpack a package archive")
    unpack_parser.add_argument(
        "package",
        help="Package file path or package file name under plugin/neko-plugin-cli/target",
    )
    unpack_parser.add_argument(
        "--plugins-root",
        default=str(DEFAULT_PLUGINS_ROOT),
        help="Destination root for extracted plugin directories",
    )
    unpack_parser.add_argument(
        "--profiles-root",
        default=str(DEFAULT_PROFILES_ROOT),
        help="Destination root for extracted package profiles",
    )
    unpack_parser.add_argument(
        "--on-conflict",
        choices=("rename", "fail"),
        default="rename",
        help="How to handle existing target plugin/profile directories",
    )
    unpack_parser.set_defaults(handler=handle_unpack)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze bundle candidate plugins")
    analyze_parser.add_argument(
        "plugins",
        nargs="+",
        help="Plugin directory names under plugin/plugins or explicit plugin paths",
    )
    analyze_parser.add_argument(
        "--current-sdk-version",
        help="Optional current SDK version to evaluate against all plugins",
    )
    analyze_parser.set_defaults(handler=handle_analyze)

    return parser


def handle_pack(args: argparse.Namespace) -> int:
    plugin_dirs = resolve_plugin_dirs(plugin_names=args.plugins, pack_all=args.all)
    target_dir = Path(args.target_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if args.out and not args.bundle and len(plugin_dirs) != 1:
        raise ValueError("--out can only be used when packing a single plugin")

    if args.bundle or len(plugin_dirs) > 1:
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
        print(
            f"Completed with failures: packed={packed_count}, failed={failed_count}",
            file=sys.stderr,
        )
        return 1

    print(f"Completed: packed={packed_count}, failed=0")
    return 0


def handle_inspect(args: argparse.Namespace) -> int:
    package_path = resolve_package_path(args.package)

    try:
        result = inspect_package(package_path)
    except Exception as exc:
        print(f"[FAIL] {package_path}: {exc}", file=sys.stderr)
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


def handle_verify(args: argparse.Namespace) -> int:
    package_path = resolve_package_path(args.package)

    try:
        result = inspect_package(package_path)
    except Exception as exc:
        print(f"[FAIL] {package_path}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] package={result.package_path}")
    print(f"  metadata_found={result.metadata_found}")
    print(f"  payload_hash={result.payload_hash}")
    print(f"  payload_hash_verified={result.payload_hash_verified}")

    if result.payload_hash_verified is True:
        return 0

    if result.payload_hash_verified is None:
        print("[FAIL] metadata.toml missing, payload hash could not be verified", file=sys.stderr)
    else:
        print("[FAIL] payload hash verification failed", file=sys.stderr)
    return 1


def handle_unpack(args: argparse.Namespace) -> int:
    package_path = resolve_package_path(args.package)

    try:
        result = unpack_package(
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
    for item in result.unpacked_plugins:
        suffix = " (renamed)" if item.renamed else ""
        print(f"  plugin: {item.source_folder} -> {item.target_dir.name}{suffix}")
    if result.profile_dir is not None:
        print(f"  profiles={result.profile_dir}")
    return 0


def handle_analyze(args: argparse.Namespace) -> int:
    plugin_dirs = [resolve_plugin_dir_candidate(item) for item in args.plugins]

    try:
        result = analyze_bundle_plugins(
            plugin_dirs,
            current_sdk_version=args.current_sdk_version,
        )
    except Exception as exc:
        print(f"[FAIL] analyze: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] plugin_count={result.plugin_count}")
    print(f"  plugins={', '.join(result.plugin_ids)}")

    if result.sdk_supported_analysis is not None:
        print(f"  sdk_supported_overlap={result.sdk_supported_analysis.has_overlap}")
        if result.sdk_supported_analysis.matching_versions:
            print(f"  sdk_supported_matching={', '.join(result.sdk_supported_analysis.matching_versions)}")

    if result.sdk_recommended_analysis is not None:
        print(f"  sdk_recommended_overlap={result.sdk_recommended_analysis.has_overlap}")
        if result.sdk_recommended_analysis.matching_versions:
            print(f"  sdk_recommended_matching={', '.join(result.sdk_recommended_analysis.matching_versions)}")

    for dep in result.shared_dependencies:
        print(f"  shared_dependency: {dep.name} -> {', '.join(dep.plugin_ids)}")

    return 0


def resolve_plugin_dirs(*, plugin_names: list[str], pack_all: bool) -> list[Path]:
    if pack_all:
        plugin_dirs = sorted(
            path.parent.resolve()
            for path in PLUGIN_ROOT.glob("*/plugin.toml")
            if path.is_file()
        )
        if not plugin_dirs:
            raise FileNotFoundError(f"No plugin.toml files found under {PLUGIN_ROOT}")
        return plugin_dirs

    if not plugin_names:
        raise ValueError("Please provide a plugin name or use --all")

    return [resolve_plugin_dir_candidate(item) for item in plugin_names]


def resolve_plugin_dir_candidate(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        plugin_dir = candidate.resolve()
    else:
        plugin_dir = (PLUGIN_ROOT / raw).resolve()

    plugin_toml = plugin_dir / "plugin.toml"
    if not plugin_toml.is_file():
        raise FileNotFoundError(f"plugin.toml not found for plugin '{raw}': {plugin_toml}")
    return plugin_dir


def resolve_package_path(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate.resolve()

    target_candidate = (TARGET_DIR / raw).resolve()
    if target_candidate.exists():
        return target_candidate

    raise FileNotFoundError(f"package file not found: {raw}")


if __name__ == "__main__":
    raise SystemExit(main())
