from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TARGET_DIR = SCRIPT_DIR / "target"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public import inspect_package


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    package_path = _resolve_package_path(args.package)

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quick inspect a .neko-plugin or .neko-bundle package",
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
