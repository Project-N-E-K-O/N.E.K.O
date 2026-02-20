---
name: i18n
description: "i18n (internationalization) toolkit for projects using i18next. Provides three main functions: (1) i18n-check - Detect hardcoded Chinese text in HTML/JS files, (2) i18n-fix - Replace hardcoded text with i18n markers, (3) i18n-sync - Align translation keys across multiple languages (zh-CN, en, ja, ko, zh-TW). Use when working on internationalization tasks, detecting untranslated strings, or syncing locale files."
---

# i18n Toolkit

Complete i18n (internationalization) toolkit for projects using i18next.

## Architecture

- **i18n library**: i18next
- **Locale files**: `static/locales/` (zh-CN, zh-TW, en, ja, ko)
- **HTML attributes**: `data-i18n`, `data-i18n-placeholder`, `data-i18n-title`, `data-i18n-alt`
- **JS function**: `window.t()` or `i18next.t()`
- **Progress file**: `.claude/i18n-progress.json`

## Three Main Commands

### 1. i18n-check - Detect Issues

Check frontend files for hardcoded Chinese text.

```
/i18n-check <module> [options]
```

**Modules**: main, live2d, voice, steam, settings, chat, custom

**Options**: `--status`, `--reset`, `--files=<path>`, `--html`, `--js`, `--strict`

See [references/check-modules.md](references/check-modules.md) for module file mappings.

### 2. i18n-fix - Fix Issues

Replace hardcoded Chinese with i18n markers.

```
/i18n-fix <module> [--add-keys]
```

**Fix patterns**:
- HTML: Add `data-i18n="key"` attributes
- JS: Use `window.t('key')` with fallback

See [references/fix-patterns.md](references/fix-patterns.md) for examples.

### 3. i18n-sync - Sync Languages

Align translation keys across all languages.

```bash
python3 scripts/i18n_sync.py          # Check status
python3 scripts/i18n_sync.py --apply  # Apply changes
```

## Quick Workflow

1. **Check**: `/i18n-check steam` - Find hardcoded strings
2. **Fix**: `/i18n-fix steam` - Replace with i18n markers
3. **Verify**: `/i18n-check steam` - Confirm fixes
4. **Sync**: `/i18n-sync` - Sync to other languages

## Detection Rules

### Check for:
- HTML: Chinese text in elements or attributes
- JS: Chinese strings not wrapped in `window.t()`

### Skip:
- Already has `data-i18n*` or `window.t()` wrapper
- `console.log/error/warn` debug messages
- Third-party libs (`static/libs/`)
- Comments
- Internal logic strings (e.g., `includes('已离开')`)
- Data keys (e.g., `data['档案名']`)
