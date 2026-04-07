from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()
TARGET_DIR = SCRIPT_DIR / "target"
PLUGIN_ROOT = REPO_ROOT / "plugin" / "plugins"
PROFILES_ROOT = REPO_ROOT / "plugin" / ".neko-package-profiles"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public import unpack_package


def main() -> int:
    # Like quick_pack.py, this stays as a thin developer convenience wrapper on
    # top of the public API rather than adding a second layer of business logic.
    parser = _build_parser()
    args = parser.parse_args()

    package_path = _resolve_package_path(args.package)

    try:
        result = unpack_package(
            package_path,
            plugins_root=PLUGIN_ROOT,
            profiles_root=PROFILES_ROOT,
        )
    except Exception as exc:
        print(f"[FAIL] {package_path}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] package={result.package_path}")
    print(f"  type={result.package_type}")
    print(f"  id={result.package_id}")
    print(f"  plugins_root={result.plugins_root}")
    for item in result.unpacked_plugins:
        suffix = " (renamed)" if item.renamed else ""
        print(f"  plugin: {item.source_folder} -> {item.target_dir.name}{suffix}")
    if result.profile_dir is not None:
        print(f"  profiles={result.profile_dir}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quick unpack a .neko-plugin or .neko-bundle into runtime directories",
    )
    parser.add_argument(
        "package",
        help="Package file path or package file name under plugin/neko-plugin-cli/target",
    )
    return parser


def _resolve_package_path(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate.resolve()

    target_candidate = (TARGET_DIR / raw).resolve()
    if target_candidate.exists():
        return target_candidate

    raise FileNotFoundError(f"package file not found: {raw}")


if __name__ == "__main__":
    raise SystemExit(main())
