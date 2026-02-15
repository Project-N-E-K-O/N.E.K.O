#!/usr/bin/env python3
"""
i18n Locale File Synchronization Tool
Synchronizes translation keys across all language files based on zh-CN.json
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Set

# Default locales directory (relative to script location)
# Can be overridden by setting LOCALES_DIR environment variable
DEFAULT_LOCALES_DIR = Path(__file__).parent.parent.parent.parent.parent / "static" / "locales"
LOCALES_DIR = Path(os.environ.get("LOCALES_DIR", DEFAULT_LOCALES_DIR))


def get_all_keys(obj: Dict, prefix: str = "") -> Set[str]:
    """Recursively get all key paths from nested dict"""
    keys = set()
    for key, value in obj.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(get_all_keys(value, full_key))
        else:
            keys.add(full_key)
    return keys


def get_nested_value(obj: Dict, key_path: str) -> Any:
    """Get nested value by dot-separated key path"""
    keys = key_path.split(".")
    value = obj
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


def set_nested_value(obj: Dict, key_path: str, value: Any):
    """Set nested value by dot-separated key path"""
    keys = key_path.split(".")
    current = obj
    for key in keys[:-1]:
        # Ensure current is a dict before indexing
        if not isinstance(current, dict):
            return
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    if isinstance(current, dict):
        current[keys[-1]] = value


def delete_nested_key(obj: Dict, key_path: str):
    """Delete nested key by dot-separated key path, cleaning up empty parent objects"""
    keys = key_path.split(".")
    if len(keys) == 0:
        return

    # Track (parent_dict, child_key) pairs that may need cleanup after deletion
    # These are the parent containers of each segment in the path
    path_stack = []
    current = obj

    # Walk to the parent of the target key, recording the path
    for idx, key in enumerate(keys[:-1]):
        if not isinstance(current, dict) or key not in current:
            return
        # Record (parent, child_key) where parent[child_key] is the dict we're about to descend into
        # After deletion, we'll check if parent[child_key] is empty
        path_stack.append((current, key))
        current = current[key]

    # Delete the target key
    if isinstance(current, dict) and keys[-1] in current:
        del current[keys[-1]]

    # Clean up empty parent objects (from leaf to root)
    for parent_dict, child_key in reversed(path_stack):
        if child_key in parent_dict and isinstance(parent_dict[child_key], dict):
            if len(parent_dict[child_key]) == 0:
                del parent_dict[child_key]


def sync_locales(dry_run: bool = True, translate: bool = False):
    """Sync all locale files with zh-CN.json as base"""
    print("=== i18n Locale Sync Report ===\n")

    if translate:
        print("⚠️  Note: --translate flag is not implemented yet.")
        print("   Automatic translation is not available in this version.\n")

    # Read base file
    zh_cn_path = LOCALES_DIR / "zh-CN.json"
    if not zh_cn_path.exists():
        print(f"❌ Base file not found: {zh_cn_path}")
        return

    with open(zh_cn_path, "r", encoding="utf-8") as f:
        zh_cn = json.load(f)

    zh_cn_keys = get_all_keys(zh_cn)
    print("📁 Base file: zh-CN.json")
    print(f"   Total keys: {len(zh_cn_keys)}\n")

    # Target languages
    target_langs = ["en", "ja", "ko", "zh-TW"]

    # Store lang_data for summary calculation (avoid re-reading files)
    lang_data_cache = {}

    for lang in target_langs:
        lang_path = LOCALES_DIR / f"{lang}.json"
        if not lang_path.exists():
            print(f"📁 {lang}.json")
            print("   ❌ File not found\n")
            continue

        with open(lang_path, "r", encoding="utf-8") as f:
            lang_data = json.load(f)

        lang_keys = get_all_keys(lang_data)
        missing_keys = zh_cn_keys - lang_keys
        extra_keys = lang_keys - zh_cn_keys

        print(f"📁 {lang}.json")
        print(f"   ✅ Matching: {len(lang_keys & zh_cn_keys)}")

        if missing_keys:
            print(f"   ➕ Missing: {len(missing_keys)}")
            for key in sorted(list(missing_keys))[:5]:
                zh_value = get_nested_value(zh_cn, key)
                print(f"      + \"{key}\": \"{zh_value}\"")
            if len(missing_keys) > 5:
                print(f"      ... and {len(missing_keys) - 5} more")

        if extra_keys:
            print(f"   ➖ Extra: {len(extra_keys)}")
            for key in sorted(list(extra_keys))[:5]:
                print(f"      - \"{key}\"")
            if len(extra_keys) > 5:
                print(f"      ... and {len(extra_keys) - 5} more")

        if not missing_keys and not extra_keys:
            print("   ✅ Synced")

        # Apply changes
        if not dry_run and (missing_keys or extra_keys):
            for key in sorted(missing_keys):
                zh_value = get_nested_value(zh_cn, key)
                # Use zh-CN value as placeholder (actual translation done by Claude)
                set_nested_value(lang_data, key, zh_value)

            for key in sorted(extra_keys):
                delete_nested_key(lang_data, key)

            with open(lang_path, "w", encoding="utf-8") as f:
                json.dump(lang_data, f, ensure_ascii=False, indent=2)
            print("   ✅ File updated")

        # Cache updated lang_data for summary
        lang_data_cache[lang] = lang_data

        print()

    # Summary (using cached lang_data instead of re-reading files)
    print("📊 Summary")
    total_missing = 0
    total_extra = 0
    for lang in target_langs:
        if lang in lang_data_cache:
            lang_data = lang_data_cache[lang]
            lang_keys = get_all_keys(lang_data)
            missing = len(zh_cn_keys - lang_keys)
            extra = len(lang_keys - zh_cn_keys)
            total_missing += missing
            total_extra += extra
            status = "✅" if missing == 0 and extra == 0 else "⚠️"
            print(f"   {status} {lang}: missing {missing}, extra {extra}")

    print(f"\n   Total: missing {total_missing}, extra {total_extra}")

    if dry_run:
        print("\n💡 Use --apply to apply changes")


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    translate = "--translate" in sys.argv
    sync_locales(dry_run=dry_run, translate=translate)
