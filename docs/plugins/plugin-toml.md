# Plugin Config (plugin.toml)

Every plugin has a `plugin.toml` file in its root folder. This page explains all available fields.

## Minimal example

```toml
[plugin]
id = "my_plugin"
name = "My Plugin"
version = "0.1.0"
entry = "plugin.plugins.my_plugin:MyPlugin"

[plugin.sdk]
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
```

## Full example (real plugin)

```toml
[plugin]
id = "web_search"
name = "网络搜索"
description = "联网搜索。自动根据用户 IP 选择搜索引擎。"
short_description = "Web search via Baidu (CN) or DuckDuckGo (intl)."
keywords = ["搜索", "search", "百度", "duckduckgo", "google"]
version = "0.1.0"
entry = "plugin.plugins.web_search:WebSearchPlugin"

[plugin.author]
name = "N.E.K.O Team"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin.store]
enabled = false

[plugin_runtime]
enabled = true
auto_start = true

# Custom config section — read via self.config.dump()
[search]
max_results = 8
timeout_seconds = 15
```

## Field reference

### `[plugin]` — Plugin identity

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier. Must match the folder name. |
| `name` | Yes | Display name shown in Plugin Manager. |
| `entry` | Yes | Python entry point: `plugin.plugins.<id>:<ClassName>` |
| `description` | No | Full description of what the plugin does. |
| `short_description` | No | Brief description (< 300 chars) used by the AI agent to decide whether to call this plugin. |
| `keywords` | No | List of regex patterns. The AI agent uses these to match user intent to your plugin. |
| `version` | No | Semver version string. |
| `type` | No | `"plugin"` (default), `"extension"`, or `"adapter"`. |
| `passive` | No | If `true`, the AI agent won't proactively dispatch tasks to this plugin. Used for listeners (e.g. danmaku, QQ auto-reply). |

### `[plugin.author]`

| Field | Description |
|-------|-------------|
| `name` | Author name |
| `email` | Author email (optional) |

### `[plugin.sdk]` — SDK version constraints

| Field | Description |
|-------|-------------|
| `recommended` | Recommended SDK version range. |
| `supported` | Minimum supported range. Plugin is rejected if not met. |
| `untested` | Allowed but shows a warning on load. |
| `conflicts` | Version ranges that are rejected. |

### `[plugin_runtime]` — Runtime behavior

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `true` | Whether the plugin can be loaded at all. |
| `auto_start` | `false` | Start automatically when N.E.K.O launches. |

### `[plugin.i18n]` — Internationalization

| Field | Description |
|-------|-------------|
| `default_locale` | Fallback locale (e.g. `"zh-CN"`, `"en"`). |
| `locales_dir` | Directory containing locale JSON files, relative to plugin root. |

### `[plugin.store]` — Persistent storage

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable `self.store` key-value persistence. |

### `[plugin.database]` — SQLite database

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable `self.db` SQLite access. |

### `[plugin_state]` — State persistence

| Field | Default | Description |
|-------|---------|-------------|
| `backend` | `"off"` | `"off"`, `"memory"`, or `"file"`. |

### `[plugin.ui]` — Hosted UI surfaces

```toml
[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "My Plugin"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "Quickstart"
entry = "docs/quickstart.md"
permissions = ["state:read"]
```

See [Hosted UI](./hosted-ui) for full documentation.

### Custom config sections

Any section not recognized by the framework is treated as custom config. Read it in your plugin:

```python
cfg = await self.config.dump()
my_settings = cfg.get("search", {})
```

## File structure with all optional files

```
plugin/plugins/my_plugin/
├── plugin.toml          # Required: plugin config
├── __init__.py          # Required: plugin code
├── config.json          # Optional: additional config
├── data/                # Optional: runtime data (self.data_path())
├── i18n/                # Optional: locale files
│   ├── en.json
│   └── zh-CN.json
├── ui/                  # Optional: Hosted TSX panels
│   └── panel.tsx
├── docs/                # Optional: Markdown/TSX guides
│   └── quickstart.md
└── static/              # Optional: legacy web UI
    └── index.html
```
