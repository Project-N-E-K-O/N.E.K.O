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

## Start developing

### Capability disclosure and start confirmation

When capability analysis completes and finds nothing sensitive, a plugin can start directly and the Permissions tab shows that no calls reading conversations or memory, or sending content to N.E.K.O, were found. When a plugin has unconfirmed capabilities, a manual start (start button, context menu, batch start, or the Permissions tab) opens a confirmation dialog listing exactly the unconfirmed capabilities; the plugin starts only after approval. The decision — approve or deny — is saved in the user data directory and reused for later starts, reloads, and normal upgrades.

Autostart, upgrade restarts, install-source-switch restarts, uninstall-rollback restarts, hot reload, and reload/reloadAll are never blocked by confirmation. The only exception: plugins the user explicitly denied are skipped by autostart until the user starts them manually and approves. The autostart switch itself is unaffected.

Each time the plugin manager is opened, a notice lists running plugins that still have unconfirmed capabilities (for example autostarted, bypass-started, or upgraded plugins that gained new capabilities). Each row shows the plugin name and its unconfirmed capabilities — marked "Added after upgrade" for previously approved plugins — with Approve and Deny buttons. Denying stops the plugin and persists the denial. Ignoring or closing the notice decides nothing; it reappears on the next open.

If a manual start is clicked while analysis is still unfinished (queued, running, or unavailable), the click is warned and blocked; a second click on the same plugin within 30 seconds starts it directly without recording a decision. If analysis finishes meanwhile and finds unconfirmed capabilities, the confirmation dialog is shown instead. When analysis finished but failed or was partial and the plugin is unconfirmed, the dialog shows an "analysis incomplete" warning; approving still starts it.

After a successful install, the platform analyzes the plugin's own Python sources on a dedicated single worker thread, detecting conversation reads, memory reads, and content pushes, and shows capabilities with code locations under the Permissions tab. The scan neither imports nor executes plugin code. Installation and startup proceed without waiting for the analysis to finish. Existing plugins are analyzed in the background at server startup or when their Permissions tab is first opened. A successful reload of an individual plugin schedules another scan if the Python source revision changed. You can also reanalyze from the Permissions tab.

Static analysis reports possible use without determining whether a code path actually runs. Calls made through objects imported from another plugin-owned module may be missed. A failed scan neither disables the plugin nor revokes a saved decision. Decisions and analysis reports are stored separately; the analysis service is only notified after an install transaction commits, so a rollback never publishes a new report. The Permissions tab can revoke a saved decision at any time, returning the plugin to unconfirmed.

Read [Getting Started with Plugin Development](./plugin-development) for the package structure and complete workflow. Then follow the [Quick Start](./quick-start) and use the [Plugin Capabilities and SDK Reference](./sdk-reference) while implementing features.

## Quick Links

- [Getting Started with Plugin Development](./plugin-development) — Understand what a plugin contains and how to build one
- [Quick Start](./quick-start) — Create your first plugin step by step
- [Publish a Plugin](/plugins/cli) — Upload to GitHub, submit for review, and publish new versions
- [v0.9 Migration](./migration-v0.9) — Removed surfaces and exact replacements
- [SDK Reference](./sdk-reference) — Base classes, context API, Result types
- [Message Delivery](./push-message) — `push_message` / `finish` delivery semantics, size limits, and role placeholders
- [Decorators](./decorators) — All available decorators
- [Hosted UI](./hosted-ui) — Build TSX panels and Markdown guides
- [Examples](./examples) — Complete working examples
- [Adapters & Concurrency](./advanced) — Adapter gateways, async entries, and thread safety
- [LLM Tool Calling](./tool-calling) — Register plugin functions for the LLM to invoke during conversations
- [Best Practices](./best-practices) — Error handling, testing, code organization
