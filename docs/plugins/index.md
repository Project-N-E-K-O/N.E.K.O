# Plugin System Overview

The N.E.K.O. plugin system allows developers to extend functionality with Python plugins. Each plugin runs in an **isolated process** and communicates with the main system through IPC (inter-process communication).

## Key features

- **Process isolation** — Plugins run in separate processes; crashes don't affect the main system
- **Async support** — Both sync and async plugin functions
- **Type safety** — Input validation via JSON Schema
- **Lifecycle management** — Startup, shutdown, and reload hooks
- **Message pushing** — Plugins can push messages to the main system
- **Scheduled tasks** — Timer-based periodic execution
- **Event-driven** — Subscribe to system events

## Architecture

```
┌────────────────────────────────────┐
│        Main Process                │
│  ┌──────────────────────────────┐  │
│  │   Plugin Server (FastAPI)    │  │
│  │   - HTTP API endpoints       │  │
│  │   - Plugin registry          │  │
│  │   - Message queue            │  │
│  └──────────────────────────────┘  │
└──────────────┬─────────────────────┘
               │ Queue (IPC)
    ┌──────────┼──────────┬──────────┐
    ▼          ▼          ▼          ▼
 Plugin 1   Plugin 2   Plugin 3   Plugin N
 (process)  (process)  (process)  (process)
```

## Plugin directory structure

```
plugin/plugins/
└── my_plugin/
    ├── __init__.py      # Plugin code
    └── plugin.toml      # Plugin configuration
```

## Quick links

- [Quick Start](./quick-start) — Create your first plugin
- [SDK Reference](./sdk-reference) — Base class and context API
- [Decorators](./decorators) — All available decorators
- [Examples](./examples) — Complete working examples
- [Advanced Topics](./advanced) — Async, threading, persistence
- [Best Practices](./best-practices) — Code organization and error handling
