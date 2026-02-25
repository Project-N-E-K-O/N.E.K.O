"""
Check for missing i18n translation keys.

Scans all .js and .html files for:
  - .t('key') / .t("key") calls (including window.t)
  - data-i18n="key", data-i18n-placeholder="key", data-i18n-alt="key" attributes

Then compares against all locale JSON files in static/locales/.
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "static" / "locales"
SCAN_DIRS = [REPO_ROOT / "static", REPO_ROOT / "templates"]
SCAN_EXTENSIONS = {".js", ".html"}

# Patterns that capture the key string from .t('key') or .t("key")
T_CALL_PATTERN = re.compile(r"""\.t\(\s*['"]([a-zA-Z0-9_.\-]+)['"]\s*[,)]""")

# data-i18n="key", data-i18n-placeholder="key", data-i18n-alt="key", etc.
DATA_I18N_PATTERN = re.compile(r"""data-i18n(?:-\w+)?=["']([a-zA-Z0-9_.\-]+)["']""")


def flatten_json(obj: dict, prefix: str = "") -> set[str]:
    """Flatten nested JSON dict into dot-separated keys."""
    keys = set()
    for k, v in obj.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.update(flatten_json(v, full_key))
        else:
            keys.add(full_key)
    return keys


def collect_used_keys() -> dict[str, list[tuple[str, int]]]:
    """Scan source files and return {key: [(file, line_number), ...]}."""
    used: dict[str, list[tuple[str, int]]] = {}

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for filepath in scan_dir.rglob("*"):
            if filepath.suffix not in SCAN_EXTENSIONS:
                continue
            try:
                text = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            rel = filepath.relative_to(REPO_ROOT)
            for i, line in enumerate(text.splitlines(), 1):
                for m in T_CALL_PATTERN.finditer(line):
                    key = m.group(1)
                    used.setdefault(key, []).append((str(rel), i))
                for m in DATA_I18N_PATTERN.finditer(line):
                    key = m.group(1)
                    used.setdefault(key, []).append((str(rel), i))

    return used


def load_locales() -> dict[str, set[str]]:
    """Load all locale JSON files and return {locale_name: set_of_flat_keys}."""
    locales = {}
    if not LOCALES_DIR.exists():
        print(f"[ERROR] Locales directory not found: {LOCALES_DIR}")
        sys.exit(1)

    for f in sorted(LOCALES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[WARNING] Failed to parse {f.name}: {e}")
            continue
        locales[f.stem] = flatten_json(data)

    return locales


def main():
    print("=" * 70)
    print("  i18n Missing Key Checker")
    print("=" * 70)

    used_keys = collect_used_keys()
    locales = load_locales()

    if not locales:
        print("[ERROR] No locale files found.")
        sys.exit(1)

    print(f"\nScanned keys used in code: {len(used_keys)}")
    print(f"Locale files found: {', '.join(sorted(locales.keys()))}\n")

    all_locale_keys = set()
    for keys in locales.values():
        all_locale_keys.update(keys)

    # --- Check 1: keys used in code but missing in locales ---
    total_missing = 0
    for locale_name in sorted(locales.keys()):
        locale_keys = locales[locale_name]
        missing = sorted(k for k in used_keys if k not in locale_keys)
        if missing:
            print(f"{'─' * 70}")
            print(f"  Missing in [{locale_name}]: {len(missing)} key(s)")
            print(f"{'─' * 70}")
            for key in missing:
                locations = used_keys[key]
                loc_str = ", ".join(f"{f}:{ln}" for f, ln in locations[:3])
                if len(locations) > 3:
                    loc_str += f" (+{len(locations) - 3} more)"
                print(f"  ✗ {key}")
                print(f"    used at: {loc_str}")
            total_missing += len(missing)
            print()

    # --- Check 2: keys in code not found in ANY locale (likely typos) ---
    orphan_keys = sorted(k for k in used_keys if k not in all_locale_keys)
    if orphan_keys:
        print(f"{'═' * 70}")
        print(f"  Keys not found in ANY locale ({len(orphan_keys)}) — possible typos")
        print(f"{'═' * 70}")
        for key in orphan_keys:
            locations = used_keys[key]
            loc_str = ", ".join(f"{f}:{ln}" for f, ln in locations[:3])
            if len(locations) > 3:
                loc_str += f" (+{len(locations) - 3} more)"
            print(f"  ✗ {key}")
            print(f"    used at: {loc_str}")
        print()

    # --- Check 3: keys defined in zh-CN but missing in other locales ---
    reference_locale = "zh-CN"
    if reference_locale in locales:
        ref_keys = locales[reference_locale]
        print(f"{'═' * 70}")
        print(f"  Cross-locale coverage (reference: {reference_locale})")
        print(f"{'═' * 70}")
        for locale_name in sorted(locales.keys()):
            if locale_name == reference_locale:
                continue
            locale_keys = locales[locale_name]
            missing_from_ref = sorted(ref_keys - locale_keys)
            extra = sorted(locale_keys - ref_keys)
            print(f"\n  [{locale_name}]  total: {len(locale_keys)}  "
                  f"missing: {len(missing_from_ref)}  extra: {len(extra)}")
            if missing_from_ref:
                for key in missing_from_ref[:20]:
                    print(f"    ✗ missing: {key}")
                if len(missing_from_ref) > 20:
                    print(f"    ... and {len(missing_from_ref) - 20} more")

    # --- Summary ---
    print(f"\n{'═' * 70}")
    print(f"  Summary")
    print(f"{'═' * 70}")
    print(f"  Total keys used in code : {len(used_keys)}")
    print(f"  Keys missing in locales : {total_missing} (across all locales)")
    print(f"  Keys not in ANY locale  : {len(orphan_keys)}")
    for locale_name in sorted(locales.keys()):
        print(f"  [{locale_name}] keys defined: {len(locales[locale_name])}")

    if orphan_keys:
        sys.exit(1)
    print("\n  ✓ All code keys have translations in at least one locale.")
    sys.exit(0)


if __name__ == "__main__":
    main()
