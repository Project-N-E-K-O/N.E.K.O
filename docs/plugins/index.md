# Plugin System Overview

The N.E.K.O. plugin system is a Python-based plugin framework built on **process isolation** and **async IPC**. It supports three development paradigms — **Plugin**, **Extension**, and **Adapter** — to cover different use cases from simple features to complex protocol bridging.

## Architecture

```
┌────────────────────────────────────────────────────┐
│              Main Process (Host)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - Plugin lifecycle management              │  │
│  │   - Bus system (memory, events, messages)    │  │
│  │   - Extension injection                      │  │
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
      ┌──────────────┼──────────────┬────────────────┐
      ▼              ▼              ▼                ▼
  Plugin A       Plugin B      Extension C      Adapter D
  (process)      (process)     (injected)       (process)
```

## Three Development Paradigms

| Paradigm | Import from | Use case | How it runs |
|----------|------------|----------|-------------|
| **Plugin** | `plugin.sdk.plugin` | Independent features (search, reminders, etc.) | Separate process |
| **Extension** | `plugin.sdk.extension` | Add routes/hooks to an existing plugin | Injected into host plugin process |
| **Adapter** | `plugin.sdk.adapter` | Bridge external protocols (MCP, NoneBot) to internal plugin calls | Separate process with gateway pipeline |

### When to use which?

- **"I want to add a new standalone feature"** → use **Plugin**
- **"I want to extend an existing plugin with extra commands"** → use **Extension**
- **"I want to accept MCP/NoneBot/external protocol calls and route them to plugins"** → use **Adapter**

> 99% of developers only need **Plugin**. Start there.

## Key Features

- **Process isolation** — Each plugin runs in a separate process; crashes don't affect the host
- **Async support** — Both sync and async entry points
- **Result types** — `Ok`/`Err` for type-safe error handling (no exceptions in normal flow)
- **Hook system** — `@before_entry`, `@after_entry`, `@around_entry`, `@replace_entry` for AOP
- **Cross-plugin calls** — `self.plugins.call_entry("other_plugin:entry_id")` for inter-plugin communication
- **Memory client** — `self.memory` for accessing the host memory system
- **System info** — `self.system_info` for querying host system metadata
- **Plugin store** — `PluginStore` for persistent key-value storage
- **Bus system** — `self.bus` for event pub/sub
- **Dynamic entries** — Register/unregister entry points at runtime
- **Static UI** — Serve a web UI from your plugin directory
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
    └── static/          # Optional: web UI files
```

## Quick Links

- [Quick Start](./quick-start) — Create your first plugin in 5 minutes
- [SDK Reference](./sdk-reference) — Base classes, context API, Result types
- [Decorators](./decorators) — All available decorators
- [Examples](./examples) — Complete working examples
- [Advanced Topics](./advanced) — Extensions, Adapters, cross-plugin calls, hooks
- [Best Practices](./best-practices) — Error handling, testing, code organization
