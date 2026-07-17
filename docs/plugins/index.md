# Plugin System Overview

The N.E.K.O. plugin system is a Python-based plugin framework built on **process isolation** and **async IPC**. It has two package types: **Plugin** for product features and **Adapter** for external protocol bridges. The former **Extension** package type has been removed; `PluginRouter` remains available inside a normal Plugin.

## Architecture

```
┌────────────────────────────────────────────────────┐
│              Main Process (Host)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - Plugin lifecycle management              │  │
│  │   - Bus system (memory, events, messages)    │  │
│  │   - ZMQ IPC transport                        │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Server (server/)                    │  │
│  │   - HTTP API endpoints (FastAPI)             │  │
│  │   - Plugin registry                          │  │
│  │   - Message queue                            │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬───────────────────────────────┘
                     │ ZMQ IPC
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
  Plugin A       Plugin B       Adapter D
  (process)      (process)      (process)
```

## Package types

| Paradigm | Import from | Use case | How it runs |
|----------|------------|----------|-------------|
| **Plugin** | `plugin.sdk.plugin` | Independent features (search, reminders, etc.) | Separate process |
| **Adapter** | `plugin.sdk.adapter` | Bridge external protocols (MCP, NoneBot) to internal plugin calls | Separate process with gateway pipeline |

### When to use which?

- **"I want to add a new standalone feature"** → use **Plugin**
- **"I want to add commands around an existing feature"** → use a normal **Plugin**, or add a `PluginRouter` inside the existing host when you own it
- **"I want to accept MCP/NoneBot/external protocol calls and route them to plugins"** → use **Adapter**

> Start with **Plugin**. Migrate a former Extension by merging its Router into the owning Plugin or converting it into a standalone Plugin.

## Loading a plugin is not choosing an entry

Two identifiers named “entry” appear at different layers:

| Layer | Declaration | Purpose |
|---|---|---|
| Host loading | `[plugin].entry = "module.path:ClassName"` | Import one `NekoPluginBase` class and start its process |
| Runtime dispatch | `@plugin_entry(id="search")` | Identify one callable operation inside the loaded plugin |

For user-plugin Agent dispatch, runtime selection is two-stage. Stage 1 is skipped when the total plugin description is below the configured threshold; above it, BM25 and an LLM coarse screen run in parallel and are unioned with regex `keywords` hits. Stage 2 receives full descriptions for the remaining plugins and returns `plugin_id` plus runtime `entry_id`. The host validates both against the exact candidates shown, retries once with a correction hint, and rejects a still-invalid result.

`passive = true` plugins and plugins without Agent-visible entries do not participate in this selection. This routing path is separate from LLM tool registration with `@llm_tool`.

## Key Features

- **Process isolation** — Plugins and Adapters run in separate processes
- **Async support** — Both sync and async entry points
- **Result types** — `Ok`/`Err` for type-safe error handling (no exceptions in normal flow)
- **Hook system** — `@before_entry`, `@after_entry`, `@around_entry`, `@replace_entry` for AOP
- **Cross-plugin calls** — `self.plugins.call_entry("other_plugin:entry_id")` for inter-plugin communication
- **System info** — `self.system_info` for querying host system metadata
- **Plugin store** — `PluginStore` for persistent key-value storage
- **Bus system** — `self.bus` reads host state through `messages`, `events`, `lifecycle`, `conversations`, and `memory`. Only the first three support `watch()`; `conversations` and `memory` are read-only snapshots. Replayable watcher chains use `get()` → structured `filter(field=value, ...)` → `sort(by=...)` → `limit()` → `watch()` and subscribe only to `add`, `del`, or `change` deltas. There is no publish/emit API. `self.bus.memory.get(...)` reads a bounded, in-memory window of recent user-utterance events (one-hour TTL); it is not the character's persistent memory archive. `self.ctx.query_memory(...)` is a deprecated compatibility call and does not provide semantic recall.
- **Dynamic entries** — Register/unregister entry points at runtime
- **Hosted UI** — Build interactive TSX panels and Markdown guides in the Plugin Manager
- **Static UI** — Serve a legacy web UI from your plugin directory
- **Lifecycle hooks** — `startup`, `shutdown`, `reload`, `freeze`, `unfreeze`, `config_change`
- **Timer tasks** — Periodic execution with `@timer_interval`
- **Message handlers** — React to messages from the host system

## Plugin Directory Structure

```
plugin/plugins/
└── my_plugin/
    ├── __init__.py      # Plugin code (entry point)
    ├── plugin.toml      # Plugin configuration
    ├── config.json      # Optional: custom config
    ├── data/            # Optional: runtime data directory
    ├── ui/              # Optional: hosted TSX panels
    ├── docs/            # Optional: Markdown or TSX guide surfaces
    ├── i18n/            # Optional: plugin-local translations
    └── static/          # Optional: legacy web UI files
```

## Quick Links

- [Quick Start](./quick-start) — Create your first plugin in 5 minutes
- [v0.9 Migration](./migration-v0.9) — Removed surfaces and exact replacements
- [SDK Reference](./sdk-reference) — Base classes, context API, Result types
- [Decorators](./decorators) — All available decorators
- [Hosted UI](./hosted-ui) — Build TSX panels and Markdown guides
- [Examples](./examples) — Complete working examples
- [Advanced Topics](./advanced) — Router composition, Adapters, cross-plugin calls, hooks
- [LLM Tool Calling](./tool-calling) — Register plugin functions for the LLM to invoke during conversations
- [Best Practices](./best-practices) — Error handling, testing, code organization
