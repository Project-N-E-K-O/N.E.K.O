from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()
PLUGIN_ROOT = REPO_ROOT / "plugin" / "plugins"
TARGET_DIR = SCRIPT_DIR / "target"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public import pack_plugin


def main() -> int:
    # This script intentionally stays thin: it is a developer convenience entry
    # on top of the public packing API, not a second business-logic layer.
    parser = _build_parser()
    args = parser.parse_args()

    plugin_dirs = _resolve_plugin_dirs(
        plugin_name=args.plugin,
        pack_all=args.all,
    )

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    packed_count = 0
    failed_count = 0

    for plugin_dir in plugin_dirs:
        output_path = TARGET_DIR / f"{plugin_dir.name}.neko-plugin"
        try:
            result = pack_plugin(plugin_dir, output_path)
        except Exception as exc:
            failed_count += 1
            print(f"[FAIL] {plugin_dir.name}: {exc}", file=sys.stderr)
            continue

        packed_count += 1
        print(f"[OK] {result.plugin_id} -> {result.package_path}")

    if failed_count:
        print(
            f"Completed with failures: packed={packed_count}, failed={failed_count}",
            file=sys.stderr,
        )
        return 1

    print(f"Completed: packed={packed_count}, failed=0")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quick pack N.E.K.O plugins into plugin/neko-plugin-cli/target",
    )
    parser.add_argument(
        "plugin",
        nargs="?",
        help="Plugin directory name under plugin/plugins (ignored when --all is set)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Pack all plugins under plugin/plugins",
    )
    return parser


def _resolve_plugin_dirs(*, plugin_name: str | None, pack_all: bool) -> list[Path]:
    if pack_all:
        plugin_dirs = sorted(
            path.parent.resolve()
            for path in PLUGIN_ROOT.glob("*/plugin.toml")
            if path.is_file()
        )
        if not plugin_dirs:
            raise FileNotFoundError(f"No plugin.toml files found under {PLUGIN_ROOT}")
        return plugin_dirs

    if not plugin_name:
        raise ValueError("Please provide a plugin name or use --all")

    plugin_dir = (PLUGIN_ROOT / plugin_name).resolve()
    plugin_toml = plugin_dir / "plugin.toml"
    if not plugin_toml.is_file():
        raise FileNotFoundError(f"plugin.toml not found for plugin '{plugin_name}': {plugin_toml}")
    return [plugin_dir]


if __name__ == "__main__":
    raise SystemExit(main())
