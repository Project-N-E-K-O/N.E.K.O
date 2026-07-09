# Plugin System Surface Map

Use this map to avoid rebuilding capabilities that already exist. It is an index, not a full tutorial. Read the source and tests for details.

## Public SDK Surfaces

- Standard plugins: `plugin/sdk/plugin/`
  - base class, decorators, lifecycle, settings, UI helpers, activity, LLM tool helpers
- Extensions: `plugin/sdk/extension/`
  - host plugin extension model and decorators
- Adapters: `plugin/sdk/adapter/`
  - gateway contracts and adapter runtime
- Hosted UI types: `plugin/sdk/hosted-ui/`
  - TypeScript declarations for plugin UI surfaces

Avoid `plugin/sdk/shared/` for normal plugin code. Treat it as implementation detail.

## Package Types vs Capabilities

- Scaffolded package types: `plugin`, `extension`, `adapter`.
- UI surfaces, timers, message handlers, stores, settings, and external API/device clients are Plugin capabilities, not package types.
- Adapter means protocol gateway into N.E.K.O plugin calls, not merely "uses an external service".

## Runtime and Platform Surfaces

Read only unless an escalation is confirmed:

- Config schema and semantics: `plugin/config/schema.py`, `plugin/config/plugin_toml_semantics.py`
- Entry import normalization: `plugin/core/entry_points.py`
- Runtime loader and registry: `plugin/core/registry.py`
- Plugin child process and trigger handling: `plugin/core/host.py`
- Registry/lifecycle/query services: `plugin/server/application/plugins/`
- Config profile/read/write behavior: `plugin/server/infrastructure/config_profiles*.py`
- Config update validation and hot update: `plugin/server/application/config/validation.py`, `plugin/server/application/config/hot_update_service.py`
- Runtime overrides: `plugin/server/infrastructure/runtime_overrides.py`
- Trigger path: `plugin/server/runs/trigger_service.py`
- Plugin management wrapper: `plugin/server/management.py`

## Runtime Semantics Index

- Runtime entry handlers: `plugin/core/host.py` requires triggered entries to be async.
- Entry arguments and Pydantic params: `plugin/sdk/shared/core/entry_runtime.py`.
- Decorator metadata and mutual exclusions: `plugin/sdk/shared/core/decorators.py`.
- Push-message v2 schema: `plugin/sdk/shared/core/push_message_schema.py`.
- Hosted UI action authorization: `plugin/server/application/plugins/ui_query_service.py`.
- Plugin dependencies and topological load order: `plugin/core/dependency.py`.
- Python dependency packaging: `plugin/core/python_dependencies.py`, `plugin/neko_plugin_cli/core/dependencies.py`.
- Bus read/watch facade: `plugin/sdk/shared/core/bus_context.py`.

## Existing Plugin Examples

Use nearby examples before inventing a pattern:

- Passive/background listener: `plugin/plugins/bilibili_danmaku/`
- Adapter/gateway: `plugin/plugins/mcp_adapter/`
- Large stateful UI plugin: `plugin/plugins/study_companion/`
- Reminder/scheduled behavior: `plugin/plugins/memo_reminder/`
- External service/device integrations: `plugin/plugins/mijia/`, `plugin/plugins/qq_auto_reply/`

## Docs and Tests

- Human plugin config docs: `docs/plugins/plugin-toml.md`
- Development guide: `plugin/PLUGIN_DEVELOPMENT_GUIDE.md`
- Plugin lifecycle tests: `plugin/tests/unit/server/test_plugins_lifecycle_service.py`
- Config update tests: `plugin/tests/unit/server/test_config_updates.py`
- UI manifest tests: `plugin/tests/unit/server/test_plugin_ui_manifest.py`
- UI query/action tests: `plugin/tests/unit/server/test_plugin_ui_query_service.py`
- Trigger tests: `plugin/tests/unit/server/test_trigger_service.py`
- SDK/decorator tests: `plugin/tests/unit/sdk/`, `plugin/tests/unit/core/`
- CLI route/source tests: `plugin/tests/unit/server/test_plugin_cli_route.py`, `plugin/tests/unit/server/test_plugin_cli_source_resolver.py`

When behavior is unclear, search tests before changing platform code.
