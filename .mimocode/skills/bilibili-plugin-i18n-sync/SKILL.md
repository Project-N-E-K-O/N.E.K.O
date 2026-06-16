---
name: bilibili-plugin-i18n-sync
description: "Sync i18n translation keys across the bilibili_danmaku plugin's 8 language files. Use when adding new UI strings, plugin entry descriptions, event labels, or any user-facing text that needs multi-language support. Covers the plugin's own i18n system (i18n/*.json + static/i18n.js), NOT the main project's i18n."
---

# Bilibili Plugin i18n Sync

Workflow for adding and syncing translation keys across the bilibili_danmaku plugin's 8 language files.

**Scope**: Only the plugin's i18n system at `plugin/plugins/bilibili_danmaku/i18n/`. For the main project's i18n (`static/locales/`, `data-i18n`), use the separate `i18n` skill.

## Architecture

- **Source of truth**: `i18n/zh-CN.json` (Chinese Simplified)
- **Target languages**: en, ja, ko, zh-TW, ru, es, pt (7 files)
- **Frontend loader**: `static/i18n.js` — `I18n.t('key', 'fallback')` in JS, `data-i18n="key"` in HTML
- **Backend route**: `i18n_routes.py` — serves JSON to frontend, handles locale negotiation
- **Frontend key audit**: `i18n/_frontend_keys_zh.json` — tracks which keys are actually used in `static/index.html`

## Language Files

```
plugin/plugins/bilibili_danmaku/i18n/
├── zh-CN.json          # Source of truth (466 keys)
├── en.json             # English
├── ja.json             # Japanese
├── ko.json             # Korean
├── zh-TW.json          # Chinese Traditional
├── ru.json             # Russian
├── es.json             # Spanish
├── pt.json             # Portuguese
└── _frontend_keys_zh.json  # Audit: keys referenced in index.html
```

## Key Naming Conventions

| Prefix | Purpose | Example |
|--------|---------|---------|
| `plugin.*` | Plugin name & description | `plugin.name`, `plugin.description` |
| `account.*` | Login/credential UI | `account.logged_in`, `account.qr_login` |
| `bgllm.*` | Background LLM panel | `bgllm.enabled`, `bgllm.test_ok` |
| `bgllm.event_*` | Event notification labels | `bgllm.event_follow`, `bgllm.event_entry` |
| `entries.*` | Plugin tool names & descriptions | `entries.set_room_id.name` |
| `common.*` | Shared UI labels | `common.save`, `common.cancel` |
| `error.*` | Error messages | `error.connection_failed` |

## Workflow: Adding New i18n Keys

### Step 1: Add to zh-CN.json (source)

```python
import json
from pathlib import Path

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")
zh = json.loads((i18n_dir / "zh-CN.json").read_text(encoding="utf-8"))

# Add new keys
zh["bgllm.event_new_key"] = "新事件标签"
zh["entries.new_tool.name"] = "新工具名称"
zh["entries.new_tool.description"] = "新工具的描述"

# Write back (sorted keys, ensure_ascii=False for readable Chinese)
(i18n_dir / "zh-CN.json").write_text(
    json.dumps(zh, indent=2, ensure_ascii=False, sort_keys=True),
    encoding="utf-8"
)
```

### Step 2: Translate to all 7 languages

For each target language, add translations with the same keys. Use `en.json` as the primary translation reference for non-CJK languages.

```python
import json
from pathlib import Path

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")

# Translations for the new keys (example)
translations = {
    "en": {
        "bgllm.event_new_key": "New Event Label",
        "entries.new_tool.name": "New Tool Name",
        "entries.new_tool.description": "Description of the new tool",
    },
    "ja": {
        "bgllm.event_new_key": "新しいイベントラベル",
        "entries.new_tool.name": "新しいツール名",
        "entries.new_tool.description": "新しいツールの説明",
    },
    "ko": {
        "bgllm.event_new_key": "새 이벤트 레이블",
        "entries.new_tool.name": "새 도구 이름",
        "entries.new_tool.description": "새 도구 설명",
    },
    "zh-TW": {
        "bgllm.event_new_key": "新事件標籤",
        "entries.new_tool.name": "新工具名稱",
        "entries.new_tool.description": "新工具的描述",
    },
    "ru": {
        "bgllm.event_new_key": "Новая метка события",
        "entries.new_tool.name": "Имя нового инструмента",
        "entries.new_tool.description": "Описание нового инструмента",
    },
    "es": {
        "bgllm.event_new_key": "Nueva etiqueta de evento",
        "entries.new_tool.name": "Nombre de nueva herramienta",
        "entries.new_tool.description": "Descripción de la nueva herramienta",
    },
    "pt": {
        "bgllm.event_new_key": "Nova etiqueta de evento",
        "entries.new_tool.name": "Nome da nova ferramenta",
        "entries.new_tool.description": "Descrição da nova ferramenta",
    },
}

for lang, trans in translations.items():
    fp = i18n_dir / f"{lang}.json"
    data = json.loads(fp.read_text(encoding="utf-8"))
    data.update(trans)
    fp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8"
    )
```

### Step 3: Verify key consistency

```python
import json
from pathlib import Path

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")
ref = json.loads((i18n_dir / "zh-CN.json").read_text(encoding="utf-8"))
ref_keys = set(ref.keys())

all_ok = True
for f in sorted(i18n_dir.glob("*.json")):
    if f.name.startswith("_"):
        continue
    data = json.loads(f.read_text(encoding="utf-8"))
    file_keys = set(data.keys())
    missing = ref_keys - file_keys
    extra = file_keys - ref_keys
    if missing:
        print(f"❌ {f.name}: missing {len(missing)} keys: {sorted(missing)[:5]}...")
        all_ok = False
    if extra:
        print(f"⚠️  {f.name}: {len(extra)} extra keys (not in zh-CN)")
    else:
        print(f"✅ {f.name}: OK ({len(file_keys)} keys)")

if all_ok:
    print("\n✅ All languages in sync!")
```

### Step 4: Update frontend key audit (if needed)

If the new keys are used in `static/index.html`, update `_frontend_keys_zh.json`:

```python
import re, json
from pathlib import Path

html_path = Path("plugin/plugins/bilibili_danmaku/static/index.html")
html = html_path.read_text(encoding="utf-8")

# Extract all i18n keys used in HTML
data_i18n = set(re.findall(r'data-i18n(?:-title|-placeholder|-aria-label)?="([^"]+)"', html))
js_keys = set(re.findall(r"I18n\.t\('([^']+)'", html))
all_keys = sorted(data_i18n | js_keys)

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")
zh = json.loads((i18n_dir / "zh-CN.json").read_text(encoding="utf-8"))

frontend_zh = {k: zh.get(k, "???") for k in all_keys}
(i18n_dir / "_frontend_keys_zh.json").write_text(
    json.dumps(frontend_zh, indent=2, ensure_ascii=False, sort_keys=True),
    encoding="utf-8"
)
```

## Workflow: Adding Plugin Entry Descriptions

Plugin entries defined with `@plugin_entry()` in `__init__.py` need i18n keys for their `name` and `description`:

```python
# In __init__.py
@plugin_entry(
    id="new_feature",
    name="新功能",                    # ← needs i18n key
    description="描述这个新功能",      # ← needs i18n key
)
```

Add corresponding keys:
- `entries.new_feature.name` — display name
- `entries.new_feature.description` — tooltip/description

Use this pattern to extract all entry IDs and verify i18n coverage:

```python
import re, json
from pathlib import Path

content = Path("plugin/plugins/bilibili_danmaku/__init__.py").read_text(encoding="utf-8")
pattern = r'@plugin_entry\((.+?)\)(?=\s*\n\s*(?:async def|@))'
blocks = re.findall(pattern, content, re.DOTALL)

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")
zh = json.loads((i18n_dir / "zh-CN.json").read_text(encoding="utf-8"))

for block in blocks:
    entry_id = re.search(r'id="([^"]+)"', block)
    if entry_id:
        eid = entry_id.group(1)
        name_key = f"entries.{eid}.name"
        desc_key = f"entries.{eid}.description"
        name_ok = "✅" if name_key in zh else "❌"
        desc_ok = "✅" if desc_key in zh else "❌"
        print(f"  {name_ok} {desc_ok} entries.{eid}")
```

## Workflow: Checking for Chinese in Non-Chinese Files

When English or other language files accidentally contain Chinese text:

```python
import json
from pathlib import Path

i18n_dir = Path("plugin/plugins/bilibili_danmaku/i18n")

for fname in ["en.json", "ja.json", "ko.json", "ru.json", "es.json", "pt.json"]:
    fp = i18n_dir / fname
    data = json.loads(fp.read_text(encoding="utf-8"))
    chinese_keys = []
    for k, v in data.items():
        if isinstance(v, str) and any('\u4e00' <= c <= '\u9fff' for c in v):
            chinese_keys.append(k)
    if chinese_keys:
        print(f"⚠️  {fname}: {len(chinese_keys)} Chinese entries found:")
        for k in chinese_keys[:5]:
            print(f"    {k}: {data[k][:60]}")
    else:
        print(f"✅ {fname}: no Chinese text")
```

## Common Pitfalls

1. **JSON sorting**: Always write with `sort_keys=True` to keep files comparable via diff
2. **ensure_ascii=False**: Required for readable Chinese/CJK characters in JSON
3. **`_frontend_keys_zh.json`**: This is an audit file, not a language file. Don't sync it.
4. **Fallback in HTML**: Use `I18n.t('key', 'fallback text')` — the fallback is the Chinese default
5. **Route registration**: If adding new API routes, update `i18n_routes.py` `_ALLOWED_LOCALES` set
6. **CRLF line endings**: `index.html` uses CRLF. When editing with Python, read as binary to preserve

## Quick Verification Checklist

After syncing, run:
```bash
cd "E:\NEKOdm\N.E.K.O"
python -c "
import json
from pathlib import Path
d = Path('plugin/plugins/bilibili_danmaku/i18n')
ref = json.loads((d / 'zh-CN.json').read_text(encoding='utf-8'))
for f in sorted(d.glob('*.json')):
    if f.name.startswith('_'): continue
    data = json.loads(f.read_text(encoding='utf-8'))
    missing = set(ref) - set(data)
    print(f'{f.name}: {len(data)} keys, missing={len(missing)}')
"
```
