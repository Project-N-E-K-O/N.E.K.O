# Execution Boundary

Use read context aggressively; keep writes local.

## Write Workspace

For normal plugin authoring, the only default editable workspace is:

```text
plugin/plugins/<plugin_id>/
```

Do not edit files outside that directory unless the user explicitly asks for it or confirms an escalation.

## Read Context

Read anything needed to understand the plugin contract:

- plugin docs
- plugin tests
- existing plugin examples
- plugin indexes
- repo skills and references
- CLI and debugging docs
- platform source that defines public behavior

Reading is encouraged. Writing outside the workspace is not.

## Escalation

If the plugin cannot be completed inside its workspace, stop before editing and report:

1. The plugin-level goal.
2. Why the plugin workspace cannot support it.
3. The smallest out-of-bound change required.
4. Compatibility and test impact.

Do not loosen schema, parser, lifecycle, registry, runtime host, or SDK internals to make one plugin work.
