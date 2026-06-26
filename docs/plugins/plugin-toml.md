# Plugin Config (plugin.toml)

Every plugin has a `plugin.toml` in its root folder. It tells N.E.K.O what your plugin is, how to load it, and what capabilities it has.

Below is a complete config for a fictional "Smart Notes" plugin. This plugin can search and create notes, has its own UI panel, supports multiple languages, and can be called by the AI agent.

## Full example

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "笔记", "memo", "record", "メモ"]
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"

[plugin.author]
name = "Alice"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin.store]
enabled = true

[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "Smart Notes"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "User Guide"
entry = "docs/guide.md"
permissions = ["state:read"]

[plugin_runtime]
enabled = true
auto_start = true

[notes]
max_per_page = 20
auto_classify = true
```

## Section by section

### `[plugin]` — Who is this plugin

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

These three fields are **required**. `id` must match the folder name. `entry` tells the system where to find your Python class.

```toml
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "笔记", "memo", "record", "メモ"]
```

These three fields determine whether the **AI agent can find your plugin**:

- `short_description` — The AI uses this to judge "what can this plugin do". Keep it concise and accurate.
- `keywords` — The AI uses these to match user intent. If the user says "take a note" and your keywords include "note", it matches.
- `description` — Full description for humans, shown in Plugin Manager.

If your plugin doesn't need to be called by the AI (e.g. a pure listener), you can skip these and add `passive = true`.

```toml
version = "1.2.0"
```

Optional. Used for version management and marketplace publishing.

---

### `[plugin.author]` — Who wrote it

```toml
[plugin.author]
name = "Alice"
```

Optional. Shown in Plugin Manager.

---

### `[plugin.sdk]` — Which SDK version it's compatible with

```toml
[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

Tells the system which SDK version your plugin was written for. If the user's N.E.K.O is too old or too new, the system will warn or refuse to load.

- `supported` — Below this range: refused to load
- `recommended` — Best experience in this range
- `untested` — Allowed but shows "untested" warning
- `conflicts` — Explicitly incompatible versions

---

### `[plugin_runtime]` — How it runs

```toml
[plugin_runtime]
enabled = true
auto_start = true
timeout = 10
startup_failure = "warn"
```

- `timeout` - Number of seconds to wait for startup readiness; must satisfy `0 < timeout <= 300`. Omit it to use the system default.
- `startup_failure` - What to do if `lifecycle.startup` raises after the process is alive. Omit it to default to `warn`: `warn` keeps the plugin running and marks startup as degraded, `fail` aborts startup, and `ignore` only logs the error.
- `enabled` — Set to `false` to temporarily disable without deleting files
- `auto_start` — When `true`, starts automatically with N.E.K.O; otherwise start manually from the panel

---

### `[plugin.i18n]` — Multi-language support

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

If your plugin needs multiple languages, create an `i18n/` folder in your plugin directory with locale files:

```text
i18n/
├── en.json
└── zh-CN.json
```

Don't need i18n? Just don't include this section.

---

### `[plugin.store]` — Persistent storage

```toml
[plugin.store]
enabled = true
```

When enabled, you can use `self.store` in code to save and retrieve data (key-value pairs) that persists across restarts.

Don't need storage? Just don't include this section (disabled by default).

---

### `[plugin.ui]` — Custom UI

```toml
[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "Smart Notes"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "User Guide"
entry = "docs/guide.md"
permissions = ["state:read"]
```

If your plugin needs a custom interface in Plugin Manager:

- `panel` — Interactive panel (written in TSX, can have buttons, tables, forms)
- `guide` — Read-only documentation (written in Markdown)

The file extension determines rendering: `.tsx` = interactive panel, `.md` = documentation.

Don't need UI? Just don't include this section. See [Hosted UI](./hosted-ui) for details.

---

### Custom sections — Your business config

```toml
[notes]
max_per_page = 20
auto_classify = true
```

Any section the framework doesn't recognize becomes your business config. Read it in code:

```python
cfg = await self.config.dump()
notes_cfg = cfg.get("notes", {})
max_per_page = notes_cfg.get("max_per_page", 20)
```

You can define as many custom sections as you want, named however you like.

---

## Directory structure for this plugin

```text
plugin/plugins/smart_notes/
├── plugin.toml              ← the file above
├── __init__.py              ← plugin code
├── i18n/                    ← locale files (because [plugin.i18n] is configured)
│   ├── en.json
│   └── zh-CN.json
├── ui/                      ← interactive panel (because [[plugin.ui.panel]] is configured)
│   └── panel.tsx
├── docs/                    ← user guide (because [[plugin.ui.guide]] is configured)
│   └── guide.md
└── data/                    ← runtime data (auto-created, self.data_path() points here)
```

Only `plugin.toml` and `__init__.py` are required. Everything else is created as needed.
