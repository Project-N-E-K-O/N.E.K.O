"""neko-plugin analyze — analyze bundle candidate plugins."""

from __future__ import annotations

import argparse
import sys

from ..core import analyze_bundle_plugins
from ..paths import CliDefaults
from ._resolve import resolve_plugin_dir_candidate


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("analyze", help="Analyze bundle candidate plugins")
    parser.add_argument("plugins", nargs="+", help="Plugin directory names or explicit paths")
    parser.add_argument("--current-sdk-version", help="Optional current SDK version to evaluate")
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    plugin_dirs = [resolve_plugin_dir_candidate(item, defaults=defaults) for item in args.plugins]

    try:
        result = analyze_bundle_plugins(plugin_dirs, current_sdk_version=args.current_sdk_version)
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
