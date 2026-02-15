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
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def delete_nested_key(obj: Dict, key_path: str):
    """Delete nested key by dot-separated key path"""
    keys = key_path.split(".")
    current = obj
    for key in keys[:-1]:
        if key not in current:
            return
        current = current[key]
    if keys[-1] in current:
        del current[keys[-1]]


def sync_locales(dry_run: bool = True, translate: bool = False):
    """Sync all locale files with zh-CN.json as base"""
    print("=== i18n Locale Sync Report ===\n")

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
            for key in missing_keys:
                zh_value = get_nested_value(zh_cn, key)
                # Use zh-CN value as placeholder (actual translation done by Claude)
                set_nested_value(lang_data, key, zh_value)

            for key in extra_keys:
                delete_nested_key(lang_data, key)

            with open(lang_path, "w", encoding="utf-8") as f:
                json.dump(lang_data, f, ensure_ascii=False, indent=4)
            print("   ✅ File updated")

        print()

    # Summary
    print("📊 Summary")
    total_missing = 0
    total_extra = 0
    for lang in target_langs:
        lang_path = LOCALES_DIR / f"{lang}.json"
        if lang_path.exists():
            with open(lang_path, "r", encoding="utf-8") as f:
                lang_data = json.load(f)
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
