# Plugin Config (plugin.toml)

Every plugin has a `plugin.toml` in its root folder. It tells N.E.K.O what the package is, which Python class the host must load, and which optional capabilities it exposes.

::: warning Two different kinds of entry
`[plugin].entry = "module.path:ClassName"` is a **host-loading entry point**. It imports one `NekoPluginBase` class when the plugin process starts. Runtime entry IDs such as `greet` come from `@plugin_entry(id="greet")` or `register_dynamic_entry(...)`; the Agent selects those IDs only after the plugin has loaded.
:::

Below is a complete config for a fictional "Smart Notes" plugin. This plugin can search and create notes, has its own UI panel, supports multiple languages, and can be called by the AI agent.

## Full example

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
type = "plugin"
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
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

These four fields are **required** by the supported check and release workflow. `id` must be unique. The host loads any `id` matching `^[A-Za-z0-9_-]+$`, but `neko-plugin init` and the release check (`neko-plugin check -r`) require a Market ID: a lowercase letter first, then only `a-z`, `0-9`, and `_` (`^[a-z][a-z0-9_]*$`); `check -r` also limits it to 100 characters. Use that stricter form for any plugin you plan to publish. Legacy source discovery can still load some incomplete manifests or folder/ID mismatches, but package build and production installation require the declared ID, archive directory, executable destination, and entry package to stay aligned; no suffixed executable copy is created. `entry` must use `module.path:ClassName` and resolve to a `NekoPluginBase` subclass; a `PluginRouter` cannot be launched directly.

For a normal plugin, `type = "plugin"` is optional because it is the default. Use `type = "adapter"` only for an Adapter package. The removed `extension` type and `[plugin.host]` table are rejected.

Keep `id` stable across releases. Upgrades, reinstalls, and downgrades replace executable code while preserving runtime `config`, `data`, and `cache`; changing `id` creates a different plugin identity. Optional `previous_ids` only prevents an old and new identity from being installed together. It is not a runtime alias and does not migrate or delete old data. Any replacement requires explicit user confirmation.

```toml
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "笔记", "memo", "record", "メモ"]
```

These fields decide how the Agent (NekoPower) selects the plugin once the host has loaded it:

- `description` — Full description used in plugin metadata and in the fine selection.
- `short_description` — Compact description read by the coarse screen, which uses at most 150 tokens of it. **Write one.** If it is absent and `description` is longer than those 150 tokens, the host has the small model write one from `description` in the background, and keeps it in `nekopower/plugin_short_descriptions.json` under the data directory: one model request per plugin and description when it succeeds. An answer longer than 150 tokens is kept cut to 150. A request that fails is not kept and is tried again after 10 minutes, each try another request, at most 3 times a day for one version of `description` (restarts included); a changed `description` starts again. Until one is kept, the start of `description` is used instead.
- `keywords` — Regular-expression patterns. A match puts the plugin among the coarse stage's candidates and marks it as a keyword match for the fine selection; it does not bypass the fine selection or guarantee execution.

Selection has two stages. When the descriptions of all selectable plugins and their visible entries together exceed 3000 tokens, a coarse stage keeps the union of three sets: plugins whose keywords match, the ten best BM25 text matches (over the plugin's ID, name, descriptions, and its entries' IDs and descriptions), and the plugins a small model picks from the short descriptions. The fine selection then chooses a `plugin_id` and a runtime `entry_id` and fills in the arguments. At or below 3000 tokens the coarse stage is skipped and every selectable plugin goes to the fine selection.

Selection runs only when the main dialogue model hands a task over: it called its `dispatch_task` tool, or it agreed out loud and the turn check dispatched for it. Finished turns are not analysed automatically, so a plugin that relied on being picked up from a passing remark of the user is selected less often. The text matched against keywords and BM25 is the user's own words together with the sentence the model dispatched. A keyword match in the user's words is also one of the signals that lets a turn through to the turn check.

Set `passive = true` for a listener/integration that must never participate in Agent dispatch. A non-passive plugin also needs at least one Agent-visible runtime entry before it can become a candidate.

The `plugin_id` and `entry_id` chosen by the fine selection are checked against the candidates it was shown. An invalid choice is not asked about again and no entry is called directly: the task goes to a task agent, or is reported as not doable when none is available. The fine selection can also answer that nothing can do the task, or that it is not a task at all (`route="none"`); nothing is started then. What happens when an entry is called is described under "How the Agent calls an entry" in [Entries](./entries).

```toml
version = "1.2.0"
```

Required by the check and release workflow. Used for version management and marketplace publishing.

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

Tells the host which plugin SDK versions this package supports. Values use Python packaging specifier syntax.

- `supported` — Versions accepted without an untested exception
- `recommended` — Best-tested range; being outside it produces a warning
- `untested` — Additional allowed range that produces a warning
- `conflicts` — Explicitly rejected version ranges, even if another range matches

If `supported` is present, a host outside both `supported` and `untested` is not loaded. Invalid specifiers are also rejected.

---

### `[plugin_runtime]` — How it runs

```toml
[plugin_runtime]
enabled = true
auto_start = true
priority = 0
timeout = 10
startup_failure = "warn"
```

- `timeout` - Number of seconds to wait for startup readiness; must satisfy `0 < timeout <= 300`. Omit it to use the system default.
- `startup_failure` - What to do if `lifecycle.startup` raises after the process is alive. Omit it to default to `warn`: `warn` keeps the plugin running and marks startup as degraded, `fail` aborts startup, and `ignore` only logs the error.
- `enabled` — Set to `false` to temporarily disable without deleting files
- `auto_start` — Defaults to `false` when omitted. A saved user preference overrides this manifest value; automatic startup also requires approval.
- `priority` — Optional integer runtime ordering hint

Plugin Manager's independent auto-start switch controls the saved preference.
A first local installation saves this preference as `false`, even if the package
declares `auto_start = true`, so the switch initially appears off. Normal upgrades
and reinstalls preserve the saved choice. Existing installations with an explicit
manifest value keep that value unless a saved preference overrides it.

By default, manual Start/Stop/Reload does not change the saved preference. Newly
installed plugins remain pending auto-start approval until the independent
switch is enabled; starting or reloading them manually never grants approval.

Setting `NEKO_PLUGIN_SYNC_AUTO_START_ON_TOGGLE=1` opts into legacy preference
synchronization: manual Start/Reload saves `auto_start=true`, and Stop saves
`auto_start=false`. The switch's "manual start/stop does not change this" hint
therefore describes the default mode. Even in legacy mode, a pending plugin
still requires explicit approval through the independent auto-start switch.

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

### `[plugin.database]` — SQLite database

```toml
[plugin.database]
enabled = true
name = "plugin.db"   # File name in the plugin's data directory (default: plugin.db)
```

When enabled, `self.db` uses a SQLite database file in the plugin's data directory: `async with unwrap(await self.db.session()) as session:` for SQL (`session()` returns a `Result`), or `self.db.kv` for key-value data. Disabled by default.

---

### `[plugin_state]` — Save and restore instance attributes

```toml
[plugin_state]
backend = "file"        # Where the state is kept
persist_mode = "auto"   # When it is saved
```

The runtime saves the plugin instance attributes named in the class's `__freezable__` list and restores them when the plugin next starts; the saved state is cleared after a successful restore. `backend` is the switch: `"file"` keeps the state in a file in the plugin's data directory, so only it survives a restart of the plugin process; `"memory"` keeps it only while the plugin process runs. Without `backend` (or without this table) nothing is saved, as with `"off"` (or an empty string); a value other than `file` / `memory` / `off` (for example `sqlite`) makes the plugin fail to start. What counts is the effective configuration: the manifest merged with the runtime copy (`config/plugin.toml` in the plugin's user data directory, created on first use from `config.example.toml`, or from the manifest if there is none) and the active profile, so if the runtime copy also sets `backend`, removing it from the manifest alone does not turn saving off. To turn saving off, set `backend = "off"` where it takes effect: the runtime copy and the active profile override the manifest. `persist_mode` decides which entry calls save it: `"auto"` after every entry call that returns normally (an `Err` result counts; an exception or a strict output-contract failure does not; only calls dispatched by the runtime count, so calling an entry method directly inside the plugin, for example from a timer, does not save), `"manual"` only after entries declared with `@plugin_entry(persist=True)`, and `"off"` not after entry calls; an entry's `persist=True` / `persist=False` overrides `"auto"` / `"manual"`. The mode is resolved in this order: the class attribute `__persist_mode__` (default `"off"`), then `[plugin_state].persist_mode` if it is one of `auto` / `manual` / `off` (the runtime ignores other values; `neko-plugin check` reports `always` / `disabled` as errors and suggests `auto` / `off`). If the result is `"off"`, including an explicit `persist_mode = "off"`, a legacy `[plugin_checkpoint] freeze_mode` of `"auto"` or `"manual"` still overrides it. Independently of `persist_mode`, the state is also saved when the plugin is frozen.

---

### `[plugin.shared]` — Reading another plugin's shared files

```toml
[plugin.shared]
reads = ["weather"]
```

Every plugin has a shared directory, `<user data root>/plugin_shared/<plugin id>/`, for files it wants other plugins to be able to read. The plugin that owns it writes there through `self.shared_path(...)`. To read what another plugin published, list that plugin's id under `reads` and call `self.shared_path_of("weather", ...)`; without the declaration the call raises `ValidationError`.

Three things to know:

- **It is an agreement, not a security boundary.** Plugins are not sandboxed from each other, so nothing stops a plugin from writing into another plugin's directory. The declaration only keeps the dependency visible in the manifest. Treat another plugin's files as read-only, and never put secrets in your shared directory.
- **It is not part of the plugin's private state.** The shared directory stays when the plugin that published it is removed, and it moves with the user's data when the storage location is changed.
- **Keep links out of it.** Because the directory moves with the user's data, a symbolic link or junction anywhere inside it (a virtual environment, a git checkout, `node_modules`) makes a change of the storage location fail as a whole. Publish plain files and directories only.

Publishing needs no declaration. If your plugin neither publishes nor reads shared files, leave this section out.

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

### `[permissions]` — What the plugin needs from the user

```toml
[permissions.require]
"system.network:connect" = { reason = "Look up the weather service", hosts = ["api.example.com"] }

[permissions.ask]
"plugin_sdk.main_agent.memory:read" = { reason = { en = "Use your home city for the forecast", zh-CN = "按你常住的城市给出天气" } }
"plugin_sdk.models:invoke" = { reason = "Turn the forecast into one sentence" }
```

> **Current status:** declarations are currently used only by `neko-plugin check` / `check -r`, which compare them with the code. Confirming `require` permissions at install time and asking for `ask` permissions on first use are planned for a later release and are not active yet.

- `require`: the plugin cannot work without it; the user will be asked to agree when installing.
- `ask`: the user will be asked the first time the plugin really uses it.
- Every entry needs a `reason`. Users see it next to the permission, so write it for them. It is either one string or a table of language codes; languages you leave out fall back to one you wrote.
- An `ask` entry may add `background = true` together with a `background_reason` when the plugin also needs it while the user is not using it.
- `hosts` (only for `system.network:connect`) is shown to the user for reference.

Permission ids:

| Id | Meaning |
|---|---|
| `plugin_sdk.main_agent.memory:read` | Read memory |
| `plugin_sdk.main_agent.conversations:read` | Read conversations |
| `plugin_sdk.main_agent.messages:push` | Send messages, cards or reply attachments to N.E.K.O |
| `plugin_sdk.models:invoke` | Call AI models |
| `system.network:connect` | Connect to the network |
| `system.network:listen` | Open a network port on this computer so other programs can connect to it |
| `system.fs:read` / `system.fs:write` | Read / write files outside the plugin's own data directory |
| `system.process:spawn` | Start other programs |
| `system.env:read` | Read environment variables |

`neko-plugin check` and `check -r` compare the declarations with a scan of the plugin's Python code:

- Code that clearly uses a permission the plugin does not declare is an error and fails `check -r` (and therefore Market CI). Add the declaration with a reason. For now, a plugin with no `[permissions]` table at all gets a warning instead; this will become an error in a later release.
- A possible use the scan cannot be sure about (an unresolved receiver, an import only, a file path that may be the plugin's own data) is a warning.
- A declaration the scan found no use for is a warning; it is fine when the use is dynamic.
- Code the scan cannot see through (dynamic imports, `getattr` with a computed name, `eval`/`exec`, `ctypes`, threads, `vendor/`, non-Python files) is listed as info. "No findings" never means the plugin was proven harmless.

This top-level table is unrelated to the `permissions` list of `[[plugin.ui.panel]]` / `[[plugin.ui.guide]]`.

---

### Custom sections — Your business config

```toml
[notes]
max_per_page = 20
auto_classify = true
```

Additional top-level sections are preserved as business config. Read them in code:

```python
cfg = await self.config.dump()
notes_cfg = cfg.get("notes", {})
max_per_page = notes_cfg.get("max_per_page", 20)
```

You can define as many custom sections as you want, named however you like, except `permissions`: the top-level `[permissions]` table is reserved for permission declarations (see above). A plugin that already uses `[permissions]` for its own settings should rename that section, otherwise `neko-plugin check` reports it as an invalid declaration.

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
```

Writable state is separate from the source or installed executable directory:

```text
<user data root>/plugins/smart_notes/
├── config/plugin.toml      ← runtime copy of the configuration
├── data/                   ← self.data_path()
└── cache/                  ← self.cache_path()

<user data root>/plugin_shared/smart_notes/   ← self.shared_path(), for plugins that declare they read it
```

Only `plugin.toml` and the importable Python module named by `[plugin].entry` are required. The module does not have to be `__init__.py`, although that is the common layout. Installed package code remains separate from this writable state.

## JSON Schema for the configuration panel

Place an optional `config.schema.json` beside the installed plugin's `plugin.toml` to describe field names, help text, and controls on the generic Configuration tab. No manifest setting or custom UI is required. Ship this file with the plugin, not in its writable runtime/profile directory. If your package uses an include allow-list, include this file.

For example, a schema for the `[notes]` section:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "notes": {
      "type": "object",
      "title": "Notes",
      "properties": {
        "max_per_page": {
          "type": "integer",
          "title": "Notes per page",
          "description": "Maximum number of notes shown on a page.",
          "x-title-i18n": { "en": "Notes per page", "zh-CN": "每页笔记数量" },
          "x-description-i18n": { "en": "Maximum number of notes shown on a page.", "zh-CN": "每页最多显示多少条笔记。" },
          "minimum": 1,
          "maximum": 100,
          "default": 20
        },
        "auto_classify": {
          "type": "boolean",
          "title": "Automatic classification",
          "description": "Automatically organize new notes."
        },
        "sort_order": {
          "type": "string",
          "title": "Sort order",
          "enum": ["newest", "oldest"]
        }
      }
    }
  }
}
```

| Keyword | Form behavior |
| --- | --- |
| `properties` | Describes object fields using the actual configuration structure. Existing undeclared fields remain editable. |
| `additionalProperties` | An object schema describes dynamic keys absent from `properties`, including password controls and preview masking. Named properties take precedence. Boolean values supply no field annotations; this editor does not enforce key admission. |
| `title` / `description` | Plain-text label and help text. The raw key remains visible as secondary information and is the fallback label. |
| `type` | A single `string`, `number`, `integer`, `boolean`, `object`, or `array` selects the corresponding control. Without it, the editor infers the type from the current value. |
| `items` | One child schema for array elements, including nested objects and arrays. |
| `enum` | A nonempty list of strings, numbers, or booleans produces a dropdown and preserves value types. |
| `minimum` / `maximum` | Numeric control bounds; `integer` controls accept only integers. |
| `maxLength` | Maximum text input length. |
| `readOnly` | Disables editing of the field and its child controls. |
| String fields with `writeOnly: true` | Uses a password input with a reveal toggle and masks non-empty values in baseline hints, change summaries, and JSON data views. Real values are retained for saving; this is display masking, not encryption or access control. |
| `default` | Initial value when explicitly adding a field or array item; not a runtime configuration default. |
| `x-title-i18n` / `x-description-i18n` | Optional locale-to-text maps. Standard `title` and `description` remain strings. |

Translation fallback follows the panel's existing order: exact locale, primary language, `en-US`, `en`, the first nonempty map value, then `title` / `description`. The example only shows two languages; provide every language supported by your plugin when publishing.

This is a form presentation contract, **not a complete JSON Schema validator or a server-side authorization/validation boundary**. `required`, `pattern`, composition, `$ref`, boolean schemas, union types, and `null` controls are not supported by this form. The host never fetches `$schema` or `$ref` URLs. Plugins remain responsible for runtime validation; keep runtime defaults in `plugin.toml` / `config.example.toml`.

Opening the page never inserts schema defaults or writes a profile. Schema-only fields are shown but are only written after editing. Object merging and whole-array replacement retain existing behavior. The top-level `plugin` section remains protected and excluded from profile editing.

The file must be UTF-8 JSON with an object root (`"type": "object"`), at most 256 KiB and at most 32 levels of `properties` / `items` / `additionalProperties` nesting. Missing files retain the old editor. Invalid files or malformed supported keywords produce a warning and fall back to generic editing. Configuration queries return metadata separately as `config_schema`; it never becomes part of `config` or the profile.
