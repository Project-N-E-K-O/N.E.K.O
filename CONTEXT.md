# Context

## Glossary

### Plugin Boundary

The write boundary for creating or changing a plugin: `plugin/plugins/<plugin_id>/` is the only default editable workspace. Reading docs, tests, indexes, and skills outside this directory is encouraged when it helps the agent follow the plugin contract.

### Read Context

The project materials an agent may inspect to understand plugin contracts and examples, including docs, tests, indexes, existing plugins, and skills.

### Write Workspace

The files an agent may edit by default for a plugin task. For normal plugin authoring, this is only `plugin/plugins/<plugin_id>/`.

### Platform Layer

The plugin framework area that owns plugin loading, lifecycle, registry, configuration parsing, SDK internals, and shared runtime behavior. Plugin authoring work must not modify this layer unless explicitly escalated.

### Manifest Contract

The strict runtime contract expressed by `plugin.toml`. It defines plugin identity, entrypoint, runtime behavior, UI surfaces, permissions, and supported configuration shape.

### Core Plugin Contract Reference

The hard, concise agent reference for plugin fundamentals: valid `plugin.toml` fields, entry declaration and usage, plugin abstraction levels, and required runtime constraints. This layer should be specific and non-optional.

### Plugin System Surface Map

The broad agent reference that lists what the plugin system can already do across SDK APIs, runtime features, CLI tools, UI surfaces, config, state, messaging, lifecycle, adapters, and extensions. It should orient the agent to existing capabilities without re-documenting every implementation detail; the agent should inspect source for specifics.

### Plugin Shape

The high-level architecture of a plugin as understood by both user need and the plugin system: whether it is user-invoked, background/listening, scheduled, UI-first, adapter-like, extension-like, stateful, or externally integrated.

### Plugin Identity

The single identity anchor for a plugin. `plugin_id` must be settled before implementation, and folder name, `[plugin].id`, `[plugin].name`, `[plugin].entry`, and main entry class must be derived from the same concept.

### Escalation

The required stop-and-confirm step when a plugin-level request appears to require Platform Layer changes.

### Minimum Question Set

The short required interview used when creating a plugin. It gathers only enough information to determine plugin purpose, shape, boundaries, and required capabilities.

### Guidance Follow-up

An optional clarifying question used when the user's intent is underspecified and the agent should help them choose a reasonable plugin direction.

### Risk Follow-up

A required follow-up question triggered by architectural or operational risk, such as external protocols, background work, persistence, UI permissions, host extensions, or out-of-bound platform needs.

### Plugin Design Brief

The Markdown confirmation artifact produced before implementation. It records the settled plugin identity, purpose, shape, first-version scope, out-of-scope items, inferred architecture, risks, and read/write boundaries.
