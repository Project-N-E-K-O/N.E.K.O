# Execution Boundary

What a plugin may touch, and why. `SKILL.md` and the plugin's `AGENTS.md`
carry the one-line version of each rule; this page has the full reasons.

## Write Workspace

Write only inside the plugin directory: the folder holding `plugin.toml`. In a
N.E.K.O source checkout that is a folder under the checkout's built-in plugin
root; a project created elsewhere (Plugin studio, `init --output`) is the
folder you are in. Reading anything else for context is fine and encouraged. Outputs the
CLI itself produces (packages in the CLI's own `target/` directory from
`build`, `check -r`, or `precheck`; `vendor/` from `sync`) are expected and
fine.

## Hard Constraints

### 1. Do not edit N.E.K.O itself

A plugin ships as its own package. Changes to the host (SDK, runtime, server,
frontend) do not travel with it: users of the desktop build and the Market
never get them, and the next N.E.K.O update overwrites them. If the plugin
cannot work without a host change, stop and escalate (below).

### 2. Do not import host internals

Import only:

```python
# Standard library
import asyncio
from pathlib import Path

# The public SDK facades
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, Ok, Err
from plugin.sdk.plugin import ui            # Hosted UI helpers
# from plugin.sdk.adapter import ...        # adapter packages only

# This plugin's own modules
from .client import ApiClient

# Dependencies declared in pyproject.toml and synced into vendor/
import httpx
```

Avoid as far as possible: top-level host packages such as `utils.*`,
`main_logic.*`, `memory.*`, `config.*`, and the SDK/runtime internals
`plugin.sdk.shared.*`, `plugin.core.*`, `plugin.server.*`. Reasons:

- **Consent disclosure.** Before a plugin runs, the host statically analyses
  its source to disclose what it reads (conversations, memory) and whether it
  pushes messages. That analysis follows SDK references imported from
  `plugin.`; the same data reached through a host-internal module is not
  disclosed, so the user's consent prompt would be wrong. Three capabilities
  are analysed: `conversations.read`, `memory.read`, `messages.push`. The
  Plugin Manager asks before a manual start when a detected capability is not
  approved yet (for example new in an update), or when the analysis is
  incomplete or failed for a plugin that was never approved. For an approved
  plugin, only a newly detected capability triggers the question: a failed
  analysis detects none and is not asked again. Automatic start skips only
  plugins the user denied, so a plugin can run before any prompt.
- **No compatibility promise.** Only the public facades are kept stable. Host
  internals are renamed and refactored without notice; a plugin importing
  them breaks on an ordinary update.
- **"It imports" is not "it is allowed".** The plugin process shares the
  host's Python environment and has the N.E.K.O root on `sys.path`, so almost
  any host module imports. That says nothing about whether it is supported.

Some built-in plugins in the N.E.K.O repository still use `from utils ...`.
That is a historical precedent, not an example: built-in plugins ship and are
updated together with the host. A third-party plugin is not.

If the SDK lacks something: implement it inside the plugin (standard library
or a declared dependency), or escalate. Do not reach into internals.

### 3. Do not call N.E.K.O's local services directly

Do not send HTTP/WebSocket requests to the host's own servers on localhost.
Ports and addresses differ per installation and per dev instance, those
endpoints are internal and change without notice, and they bypass the SDK's
permission and consent checks. Use the SDK: `self.bus` for reading host state,
`self.push_message(...)` for output, `self.plugins` for calling other plugins.

### 4. Do not use `print`

The plugin's log, shown on its Logs tab and attached to bug reports, is
written by the logger. `print` goes to the process's standard output, which
the user never sees. Use `self.logger`.

### 5. Do not log conversation text, secrets, or tokens

Plugin logs are files on the user's disk that get exported and pasted into
issues. Never log raw user messages, model replies, memory contents, API
keys, or access tokens. Log lengths, IDs, counts, and error types:

```python
self.logger.info("reply ready: chars=%d", len(text))
self.logger.warning("upstream failed: %s", type(exc).__name__)
```

### 6. Do not hard-code paths

Where the plugin lives differs between a source checkout, a studio project,
and an installed copy, and the user's data directory differs per machine.
Resolve every path through the SDK:

| Need | Use | Writable |
|---|---|---|
| Persistent files | `self.data_path("name.json")` | yes |
| Disposable cache | `self.cache_path(...)` | yes |
| Runtime config | `await self.config.get/set/update(...)` (async; file: `self.runtime_config_path`) | yes |
| Files shipped with the plugin | `self.plugin_dir / "assets" / ...` | **no** |

`plugin_dir` is the installed payload: an upgrade replaces it, and in a
project it is your Git working tree. Never write there.

### 7. Do not block the event loop

All entries share one event loop; a blocking call stalls every entry, UI
panel, and the stop request. Handlers are `async def`, and blocking work goes
through an async library or `await asyncio.to_thread(...)`. See
`runtime-model.md` for the loops, timers, and lifecycle.

### 8. Do not use `requirements.txt`

Declare dependencies in `pyproject.toml` `[project].dependencies`. The host
loads them only from the plugin's own `vendor/` directory and never installs
anything itself; `neko-plugin sync <plugin> --clean` fills `vendor/` from
`pyproject.toml`. `requirements.txt` is only read as a legacy fallback.

### 9. Do not commit `vendor/`, binaries, or files over 2 MiB

`vendor/` is generated by `sync --clean` (locally and in the release
workflow) and is ignored by Git. The Market rejects repositories and packages
containing binary files (`.so`, `.pyd`, `.dll`, `.exe`, archives, wheels, ...)
or files over 2 MiB; common media, font, and model assets (images, audio,
`.onnx`, ...) are exempt from the size limit only. Prefer pure-Python dependencies: the release
workflow builds `vendor/` on Linux, so compiled modules would not load on
Windows or macOS, and binaries are on the Market's reject list (its scan is
paused today but can resume; `dependencies-and-market.md`).

### 10. Do not use `eval`/`exec`/`pickle`/`shell=True`/`verify=False`

Plugins run with the user's privileges on their machine. `eval`/`exec` and
unpickling run arbitrary code from data, `shell=True` turns arguments into
shell injection, and `verify=False` disables TLS certificate checks. Use
`json`, `ast.literal_eval`, argument lists for `subprocess`, and default TLS
verification.

## Escalation

If the goal cannot be reached inside the plugin directory, stop before
editing anything else and report:

1. The plugin-level goal.
2. Why the plugin cannot do it with the public SDK.
3. The smallest host change that would make it possible.
4. Compatibility and test impact.

When the user is not a developer, say it plainly: what cannot be done, why,
and what they can choose instead (a smaller version of the feature, waiting
for a N.E.K.O update, or asking the N.E.K.O maintainers on GitHub). Do not
ask them to decide whether the platform should be changed.


## Glossary

Shared vocabulary for plugin work.

### Plugin Boundary

The write boundary for creating or changing a plugin: `<plugin_dir>` (the plugin directory holding `plugin.toml`; by default in a source checkout, a folder under the checkout's built-in plugin root, or the exact directory given to `init --output`) is the only default editable workspace. Reading docs, tests, indexes, and skills outside this directory is encouraged when it helps the agent follow the plugin contract.

### Read Context

The project materials an agent may inspect to understand plugin contracts and examples, including docs, tests, indexes, existing plugins, and skills.

### Write Workspace

The files an agent may edit by default for a plugin task. For normal plugin authoring, this is only `<plugin_dir>` (the folder holding `plugin.toml`; by default under the built-in plugin root in a source checkout, or the exact directory given to `init --output`).

### Platform Layer

The plugin framework area that owns plugin loading, lifecycle, registry, configuration parsing, SDK internals, and shared runtime behavior. Plugin authoring work must not modify this layer unless explicitly escalated.

### Manifest Contract

The strict runtime contract expressed by `plugin.toml`. It defines plugin identity, entrypoint, runtime behavior, UI surfaces, permissions, and supported configuration shape.

### Core Plugin Contract Reference

The hard, concise agent reference for plugin fundamentals: valid `plugin.toml` fields, entry declaration and usage, plugin abstraction levels, and required runtime constraints. This layer should be specific and non-optional.

### Plugin System Surface Map

The broad agent reference that lists what the plugin system can already do across SDK APIs, runtime features, CLI tools, UI surfaces, config, state, messaging, lifecycle, and adapters. It should orient the agent to existing capabilities without re-documenting every implementation detail; the agent should inspect source for specifics.

### Plugin Shape

The high-level architecture of a plugin as understood by both user need and the plugin system: whether it is user-invoked, background/listening, scheduled, UI-first, adapter-like, stateful, or externally integrated.

### Plugin Identity

The single identity anchor for a plugin. `plugin_id` must be settled before implementation, and folder name, `[plugin].id`, `[plugin].name`, `[plugin].entry`, and main entry class must be derived from the same concept.

### Escalation

The required stop-and-confirm step when a plugin-level request appears to require Platform Layer changes.

### Minimum Question Set

The short required interview used when creating a plugin. It gathers only enough information to determine plugin purpose, shape, boundaries, and required capabilities.

### Guidance Follow-up

An optional clarifying question used when the user's intent is underspecified and the agent should help them choose a reasonable plugin direction.

### Risk Follow-up

A required follow-up question triggered by architectural or operational risk, such as external protocols, background work, persistence, UI permissions, host capability gaps, or out-of-bound platform needs.

### Plugin Design Brief

The Markdown confirmation artifact produced before implementation. It records the settled plugin identity, purpose, shape, first-version scope, out-of-scope items, inferred architecture, risks, and read/write boundaries.
