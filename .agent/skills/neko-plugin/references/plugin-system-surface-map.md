# Plugin System API Index

Use this as the source-map for plugin-system APIs before inventing an abstraction. It indexes public and semi-public surfaces by layer, names the callable classes/functions/methods, and marks the recommendation level for plugin authors.

## Recommendation Levels

- S: default choice for ordinary plugin work.
- A: use when the capability is required.
- B: advanced surface for complex plugins; keep usage narrow and documented.
- C: compatibility/legacy surface; avoid for new work unless preserving behavior.
- D: internal read-only platform surface; do not import from plugin workspaces.

## Import Layers

- S `plugin.sdk.plugin`: Standard plugin facade. Import `NekoPluginBase`, decorators, result helpers, config/runtime helpers, i18n, settings, UI helpers, and ordinary plugin APIs from here.
- B `plugin.sdk.adapter`: Adapter facade. Use only for protocol gateways that translate external requests into N.E.K.O plugin calls.
- A `@neko/plugin-ui`: Hosted TSX facade. Use inside `hosted-tsx` surfaces; full component signatures live in `hosted-ui-api.md`.
- D `plugin.sdk.shared.*`, `plugin.core.*`, `plugin.server.*`: Implementation details. Do not import these from plugin workspace code unless the user explicitly approves platform work.

## Standard Plugin Facade: `plugin.sdk.plugin`

### Base Classes and Metadata

- S `NekoPluginBase`: Base class for standard plugins. It wires `ctx`, `config`, `plugins`, `store`, `db`, `state`, `logger`, `sdk_logger`, `i18n`, plus convenience properties and runtime registration methods.
- A `PluginMeta`: Plugin-facing metadata model. Use mainly for typing or inspecting metadata shape; most plugin code reads `self.metadata`.
- D `NEKO_PLUGIN_META_ATTR`, `NEKO_PLUGIN_TAG`: Metadata markers used by scanning. Do not set these manually; use decorators and manifests.

### `NekoPluginBase` Properties and Attributes

- S `self.ctx`: Wrapped plugin host context. Use higher-level helpers first; reach into raw context only when no facade exists.
- S `self.config`: `PluginConfig` helper for effective/base/profile config reads and writes. Prefer this over ad hoc host-context calls.
- S `self.plugins`: `Plugins` helper for cross-plugin discovery and calls. Use for intentional plugin-to-plugin calls with explicit timeouts.
- A `self.store`: `PluginStore` key-value persistence. Requires `[plugin.store].enabled = true` or equivalent runtime config.
- A `self.db`: `PluginDatabase` SQLite helper. Use only when the plugin needs relational storage; otherwise prefer `self.store`.
- C `self.state`: `PluginStatePersistence` helper. Use for saving/restoring selected Python attributes, not as a general database.
- S `self.logger`, `self.sdk_logger`: Plugin logger. Use for plugin-local logs instead of `print`.
- S `self.i18n`: Loaded `PluginI18n` instance for the plugin's `[plugin.i18n]` files. Use `tr()` for registered strings and `self.i18n.t(...)` for runtime strings.
- S `plugin_id`: Stable plugin id property. Use in logs, state keys, and generated messages instead of retyping the id.
- S `config_dir`: Directory containing the plugin's `plugin.toml`. Use as the base for plugin-local files.
- S `metadata`: Dict copy of plugin metadata. Use for read-only inspection of manifest-derived data.
- A `bus`: Read/watch facade over host state. Use for observing host messages/events/memory; do not treat it as a publish bus.
- A `system_info`: `SystemInfo` facade for system/server config and Python environment inspection. Use sparingly and handle `Err`.

### `NekoPluginBase` Methods

- S `data_path(*parts)`: Returns the plugin data directory or a child path. Use it for plugin-owned generated files instead of writing beside code.
- A `refresh_runtime_config(effective_config=None)`: Refreshes store/db/state runtime toggles after config changes. Call from config-change flows when you manage runtime helpers manually.
- A `get_input_schema()`: Returns class-level `input_schema` if present. Mostly useful for introspection and compatibility.
- A `run_update(**kwargs)`: Forwards a run update to the host. Use for long-running entry progress when the host expects run status.
- A `export_push(**kwargs)`: Pushes export artifacts through the host export channel. Prefer structured arguments; treat as async host integration.
- A `finish(**kwargs)`: Marks the current task complete and sends structured completion data. Use for entry flows that intentionally complete a host task.
- S `push_message(**kwargs)`: Sends plugin output to the dialog/message channel. Use `parts`, `visibility`, and `ai_behavior`; legacy v1 fields are compatibility-only and scheduled for removal in v0.9.
- A `include_router(router, prefix="")`: Mounts a `PluginRouter`. Use only for large or feature-split plugins, usually when the main file grows beyond about 1k lines.
- C `exclude_router(router_or_name)`: Unmounts a router by object or name. Rarely needed outside dynamic plugin composition.
- B `get_router(name)`: Looks up a mounted router. Use for diagnostics or dynamic router operations.
- B `list_routers()`: Lists mounted router names. Use for diagnostics, not normal business logic.
- B `register_static_ui(directory="static", index_file="index.html", cache_control=...)`: Registers legacy static iframe UI. Prefer `[plugin.ui]` Hosted TSX/Markdown for new surfaces.
- C `get_static_ui_config()`: Returns the registered static UI config. Use only for legacy static UI diagnostics.
- A `set_list_actions(actions)`: Replaces plugin-list actions shown by Plugin Manager. Prefer `ui.action` for Hosted UI action surfaces when possible.
- A `register_list_action(action)`: Adds or replaces a single plugin-list action. Use for manager-level actions that are not tied to a Hosted TSX surface.
- A `clear_list_actions()`: Clears plugin-list actions. Use during shutdown/reload cleanup when actions are dynamic.
- A `get_list_actions()`: Returns registered list actions. Use for diagnostics or UI context snapshots.
- B `register_dynamic_entry(entry_id, handler, ...)`: Registers runtime-computed entries. Use for capabilities discovered after startup, such as adapter-discovered tools.
- B `unregister_dynamic_entry(entry_id)`: Removes a dynamic entry. Use when external capabilities disappear or config disables a runtime entry.
- B `enable_entry(entry_id)`: Enables a dynamic entry and notifies the host. Static decorator entries are not toggled through this method.
- B `disable_entry(entry_id)`: Disables a dynamic entry and notifies the host. Use instead of leaving dead dynamic handlers registered.
- B `is_entry_enabled(entry_id)`: Checks dynamic/static entry availability. Use for diagnostics or guardrails before UI exposure.
- A `list_entries(include_disabled=False)`: Lists decorator and dynamic entries with metadata. Useful for Hosted UI state, diagnostics, and tests.
- B `collect_entries(wrap_with_hooks=True)`: Collects executable entry handlers. Mostly platform-facing; plugin code usually should not call handlers directly.
- A `report_status(status)`: Sends plugin status to the host. Use for coarse runtime status snapshots.
- A `register_llm_tool(name, description, parameters, handler, timeout=30.0, role=None)`: Registers a model-callable tool dynamically. Prefer `@llm_tool` when the schema is known at authoring time.
- B `unregister_llm_tool(name)`: Removes a model-callable tool and its backing dynamic entry. Use during config changes or shutdown.
- A `list_llm_tools()`: Lists LLM tools registered by this plugin. Use for diagnostics and UI state.
- S `logger_component(suffix=None)`: Builds a logger component name. Use when creating plugin-local logger variants.
- S `get_logger(suffix=None)`: Returns a plugin logger. Prefer this over global logging setup.
- A `setup_logger(level=None, force=False, suffix=None)`: Configures plugin logger level/component. Use for explicit plugin logging behavior.
- A `enable_file_logging(log_dir=None, log_level="INFO", max_bytes=None, backup_count=None)`: Enables plugin-scoped file logging. Use for verbose diagnostics, not by default.

### Decorators

- S `@neko_plugin`: Marks a class as a plugin class. Use the CLI scaffold's pattern; do not hand-set plugin metadata attrs.
- S `@plugin_entry(id=None, name=None, description="", input_schema=None, params=None, kind="action", auto_start=False, persist=None, model_validate=True, timeout=None, llm_result_fields=None, llm_result_model=None, fields=None, metadata=None, quick_action=False)`: Registers a user/agent-callable entry. Runtime-triggered handlers must be `async def`; use `tr()` for names/descriptions.
- A `@lifecycle(id="startup|shutdown|reload|freeze|unfreeze|config_change", name=None, description="", metadata=None)`: Registers lifecycle handlers. Use for startup/shutdown/config behavior, not for user-callable work.
- A `@message(id, name=None, description="", input_schema=None, source=None, metadata=None)`: Registers a message reaction handler. Use for host-message driven behavior.
- C `@timer_interval(id, seconds, name=None, description="", auto_start=True, metadata=None)`: Registers a scheduled interval handler. Use only for lightweight recurring work with clear bounds.
- B `@custom_event(event_type, id, name=None, description="", input_schema=None, kind="custom", auto_start=False, trigger_method="message", metadata=None)`: Registers a non-standard event type. Use only when built-in entry/message/timer/lifecycle categories do not fit.
- B `@on_event(event_type, id=None, name=None, description="", input_schema=None, kind="action", auto_start=False, persist=None, mode=None, seconds=None, extra=None, metadata=None)`: Low-level event registration. Prefer specialized decorators unless you need exact event metadata control.
- B `@hook(target="*", timing="before|after|around|replace", priority=0, condition=None)`: Registers an entry interception hook. Use only for concrete interception needs such as validation, auditing, timing, or compatibility shims.
- B `@before_entry(target="*", priority=0, condition=None)`: Shortcut for before hooks. Use narrow targets instead of `*` whenever possible.
- B `@after_entry(target="*", priority=0, condition=None)`: Shortcut for after hooks. Use to observe or shape results after entry execution.
- B `@around_entry(target="*", priority=0, condition=None)`: Shortcut for around hooks. Use sparingly because it wraps execution flow.
- B `@replace_entry(target="*", priority=0, condition=None)`: Replaces target entry behavior. Use only with explicit design/review justification.
- A `@quick_action(icon=None, priority=0)`: Marks an entry for quick-action presentation. Prefer explicit UX intent rather than blanket use.
-  `plugin.entry(...)`: Namespace alias for `plugin_entry`. Use when it improves readability in classes with many SDK decorators.
- A `plugin.lifecycle(...)`, `plugin.message(...)`, `plugin.timer(...)`: Namespace aliases for lifecycle/message/timer decorators. Equivalent to top-level decorators.
- B `plugin.event(...)`, `plugin.custom_event(...)`, `plugin.hook(...)`: Namespace aliases for low-level event/custom/hook decorators. Prefer specialized top-level decorators first.
- S `EntryKind`: Literal kind vocabulary for entry presentation. Use for typing only; most entries can keep `kind="action"`.

### Hosted UI Python Helpers: `plugin.sdk.plugin.ui`

- S `ui.context(id="main", title=None)`: Marks an async method as a Hosted UI context provider. The method should return a mapping with `state`, optional `state_schema`, and optional `actions`.
- S `ui.action(id=None, label=None, icon=None, tone="default", group=None, order=0, confirm=False, refresh_context=True)`: Attaches UI action metadata to an existing `@plugin_entry`. Use `tr()` for labels/confirm text and keep action permissions explicit in `plugin.toml`.
- D `UI_CONTEXT_META_ATTR`, `UI_ACTION_META_ATTR`: Metadata attrs consumed by the host. Do not set manually.

### I18n

- S `tr(key, default="", **params)`: Declares a delayed plugin-local i18n reference. Use it for plugin metadata, entries, actions, and Python-registered UI labels.
- S `PluginI18n.t(key, locale=None, default="", **params)`: Resolves a plugin i18n key at runtime. Use for Python-generated user-facing strings when decorators are not involved.
- S `PluginI18n.resolve(value, locale=None)`: Resolves nested `$i18n` refs in dict/list structures. Use for plugin-owned data payloads that contain delayed translation refs.

### Result and Error Helpers

- S `Ok(value)`, `Err(error)`, `Result`: Standard result contract for normal plugin flow. Return `Ok(payload)` / `Err(SdkError(...))` from entries instead of throwing for expected failures.
- S `unwrap(result)`: Extracts `Ok` or raises on `Err`. Use in tests or narrow internal flows where raising is acceptable.
- S `unwrap_or(result, default)`: Extracts `Ok` or returns a default. Use for non-critical optional calls.
- A `SdkError`: Base SDK error. Use for plugin-domain errors returned through `Err`.
- A `TransportError`: Error for host/IO/transport failures. Use when the plugin cannot reach or complete an external/host operation.
- B `plugin.sdk.plugin.runtime` result helpers `is_ok`, `is_err`, `map_result`, `map_err_result`, `bind_result`, `match_result`, `raise_for_err`, `must`, `capture`: Full result toolkit exported by the runtime submodule. Use when composing multiple `Result` operations; ordinary entries rarely need all of them.
- B `InvalidArgumentError`, `CapabilityUnavailableError`, `AuthorizationError`, `ResultError`, `ErrorCode`: Runtime submodule error vocabulary. Use for typed error contracts when plugin behavior depends on error categories.
- B `PluginConfigError`, `ConfigPathError`, `ConfigProfileError`, `ConfigValidationError`: Config-specific runtime errors. Use when catching or returning precise config failures.
- B `PluginCallError`, `PluginResultError`: Cross-plugin call error vocabulary. Use for precise handling around `Plugins` calls.

### Runtime Submodule Utilities: `plugin.sdk.plugin.runtime`

- A `get_plugin_logger(plugin_id, suffix=None)`: Returns a plugin-scoped logger. Prefer `self.logger` inside plugin classes; use this for helpers outside the class.
- B `SDK_VERSION`: SDK version constant. Use for diagnostics or compatibility checks, not feature branching unless necessary.
- B `LogLevel`, `LoggerLike`: Logging enum/protocol. Use for typing logger setup and helper signatures.
- B `build_component_name(kind, *parts)`: Builds stable logger component names. Prefer `self.logger_component(...)` in plugin classes.
- B `get_sdk_logger`, `setup_sdk_logging`, `configure_sdk_default_logger`, `intercept_standard_logging`, `format_log_text`: SDK logging configuration helpers. Use in platform-adjacent tooling or tests; plugins usually use `self.logger`.
- B `CallChain.get_current_chain()`, `get_depth()`, `get_current_call()`, `get_root_call()`, `is_in_call(call_id)`, `clear()`, `format_chain()`, `track(...)`: Synchronous call-chain helper. Use for recursion/cycle diagnostics around nested plugin calls.
- B `AsyncCallChain.is_available()`, `get()`, `depth()`, `contains(plugin_id, event_id)`, `get_current_chain()`, `get_depth()`, `track(...)`, `format_chain()`: Async call-chain facade. Use when host-backed call-chain data is available.
- B `CircularCallError`, `CallChainTooDeepError`, `get_call_chain()`, `get_call_depth()`, `is_in_call_chain(...)`: Call-chain error and helper functions. Use to guard recursive or deeply nested cross-plugin calls.
- B `EventMeta`, `EventHandler`, `EVENT_META_ATTR`: Event metadata/handler surfaces. Use for tests, diagnostics, and advanced dynamic registration.
- B `HookMeta`, `HookHandler`, `HookTiming`, `HOOK_META_ATTR`, `HookExecutorMixin`: Hook metadata/execution surfaces. Use for hook implementation tests or platform-adjacent diagnostics.
- B `PluginContextProtocol`: Protocol for plugin host context. Use for type annotations in helper classes that receive `ctx`.
- B `EXTENDED_TYPES`: State persistence extended type vocabulary. Use when debugging persisted attribute serialization.

### Cross-Plugin Calls: `Plugins`

- A `Plugins.list(timeout=5.0, enabled=None)`: Lists discoverable plugins, optionally filtering enabled state. Use before cross-plugin calls when availability matters.
- A `Plugins.list_ids(timeout=5.0, enabled=None)`: Lists plugin ids. Use for quick existence checks or UI state.
- A `Plugins.get(plugin_id, timeout=5.0)`: Gets a plugin descriptor or `None`. Use when you need metadata before calling.
- A `Plugins.exists(plugin_id, timeout=5.0)`: Checks whether a plugin exists. Use for optional integrations.
- A `Plugins.require_enabled(plugin_id, timeout=5.0)`: Requires a plugin to exist and be enabled. Use for hard dependencies.
- A `Plugins.call_entry(entry_ref, params=None, timeout=10.0)`: Calls `<plugin_id>:<entry_id>`. Prefer this for normal plugin entry calls.
- A `Plugins.call_event(event_ref, params=None, timeout=10.0)`: Calls `<plugin_id>:<event_type>:<event_id>`. Use for non-entry event types.
- A `Plugins.call_entry_json(entry_ref, args=None, timeout=10.0)`: Calls an entry and requires object/None payload. Use when downstream code expects a JSON object.
- A `Plugins.call_event_json(event_ref, args=None, timeout=10.0)`: Calls an event and requires object/None payload. Use for structured event integration.
- B `parse_entry_ref(entry_ref)`, `parse_event_ref(event_ref)`: Parse reference strings into typed refs. Use in validation or tooling, not needed for ordinary calls.
- B `PluginDescriptor`, `PluginCallError`, `InvalidEntryRefError`, `InvalidEventRefError`: Cross-plugin typing/error surfaces. Use for precise error handling in integrations.

### Config: `PluginConfig`

- S `dump(timeout=5.0)`: Returns the current effective config. Use for full config snapshots.
- S `get(path, default=None, timeout=5.0)`: Reads a dotted path from effective config. Use for optional config values.
- S `require(path, timeout=5.0)`: Reads a required dotted path or raises. Use when missing config should fail fast.
- S `get_bool(path, default=None, timeout=5.0)`, `get_int(...)`, `get_str(...)`: Typed config reads. Use to catch type mismatches near the boundary.
- A `set(path, value, timeout=5.0)`: Writes a dotted path into the active profile overlay. Use only for user-intended config writes.
- A `update(patch, timeout=5.0)`: Deep-merges a patch into the active profile overlay. Use for settings forms or controlled config updates.
- A `base_dump(timeout=5.0)`, `base_get(path, default=None, timeout=5.0)`: Reads non-profile base config. Use when profile overlays must be ignored.
- A `profile_state(timeout=5.0)`: Reads config profile state. Use for profile-aware UI.
- A `profile_list(timeout=5.0)`: Lists profile names. Use in profile pickers or diagnostics.
- A `profile_active(timeout=5.0)`: Returns the active profile name. Use before profile-scoped writes.
- A `profile_get(profile_name, timeout=5.0)`: Reads one profile overlay. Use for profile editors.
- A `profile_effective(profile_name=None, timeout=5.0)`: Reads effective config for current or named profile. Use to preview profile behavior.
- A `profile_create(profile_name, initial=None, make_active=False, timeout=10.0)`: Creates/upserts a profile. Use for explicit user profile creation.
- A `profile_update(profile_name, patch, timeout=10.0)`: Deep-merges into a named profile. Use for profile-specific settings forms.
- A `profile_delete(profile_name, timeout=10.0)`: Deletes a profile. Use behind confirmation.
- A `profile_activate(profile_name, timeout=10.0)`: Activates a profile. Use from profile switchers.
- A `profile_ensure_active(profile_name, initial=None, timeout=10.0)`: Ensures some profile is active. Use in setup flows that need a writable active profile.
- B Config helpers `deep_merge_config`, `validate_profile_name`, `get_profile_names`, `get_active_profile_name`, `unwrap_config_payload`, `unwrap_profile_payload`, `unwrap_profiles_state`: Utility helpers exported by the config submodule. Use for tooling/tests; plugin code usually uses `PluginConfig`.
- B `PluginConfigBaseView`, `PluginConfigProfiles`: Backward-compatible aliases for `PluginConfig`. Do not introduce new code that depends on the alias names.

### Settings: `PluginSettings`

- S `PluginSettings`: Pydantic base for typed business settings. Inherit from it and set `model_config = ConfigDict(toml_section="...")` when you want typed config sections.
- S `SettingsField(default=..., hot=False, description="", **kwargs)`: Field helper that marks hot-updatable config fields. Use for documented settings schema.
- B `plugin.sdk.plugin.settings.get_hot_fields(settings_cls)`: Returns hot field names. Use in config-change logic or tests.
- B `plugin.sdk.plugin.settings.create_settings_safe(settings_cls, config_section)`: Validates settings with per-field fallback. Use when config should degrade to defaults rather than fail construction.

### Routers: `PluginRouter`

- A `PluginRouter(prefix="", tags=None, name=None)`: Groups entries for large plugins. Use when the main plugin module is too large or feature ownership is split.
- B `prefix` property, `set_prefix(prefix)`: Controls id prefixing for router entries. Prefer a stable prefix chosen at design time.
- B `tags` property: Returns router tags. Use for diagnostics or future grouping; do not rely on it for dispatch.
- B `is_bound()`: Reports whether the router is mounted to a plugin. Use for diagnostics.
- B `entry_ids()`: Lists resolved entry ids. Use in tests or UI diagnostics.
- B `ctx`, `config`, `plugins`, `logger`, `file_logger`, `store`, `db`, `plugin_id`, `main_plugin`: Bound accessors delegated from the main plugin. Use only after the router is included.
- B `name()`: Returns the router name. Use with `get_router`/`exclude_router`.
- B `iter_handlers()`, `collect_entries()`: Returns router entry handlers. Mostly host/test facing.
- B `get_plugin_attr(name, default=None)`, `has_plugin_attr(name)`, `get_dependency(name, default=None)`: Accesses main-plugin attributes from a router. Prefer explicit constructor dependencies when possible.
- B `report_status(status)`: Delegates status reporting to the main plugin. Use for router-local status updates.
- B `on_mount()`, `on_unmount()`: Lifecycle extension points for router mount/unmount. Override only when the router owns resources.
- B `add_entry(entry_id, handler, ...)`: Adds runtime router entries. Use for dynamic feature modules; static `@plugin_entry` is preferred.
- B `remove_entry(entry_id)`: Removes runtime router entries. Use when router-owned capabilities disappear.
- B `list_entries()`: Lists router entry metadata. Use for tests and diagnostics.
- B `RouteHandler`, `PluginRouterError`, `EntryConflictError`: Router typing/error surfaces. Use for precise router tests and dynamic entry errors.

### Store, Database, and State

- A `PluginStore.get(key, default=None)`: Reads JSON-compatible key-value data. Returns `Result`; handle `Err`.
- A `PluginStore.set(key, value)`: Writes JSON-compatible data. Use for small plugin-owned state.
- A `PluginStore.delete(key)`, `exists(key)`, `keys(prefix="")`, `clear()`, `count()`, `dump()`, `close()`: Store maintenance and inspection helpers. Use for settings/state UIs, tests, and shutdown cleanup.
- B `PluginDatabase.create_all()`, `drop_all()`, `session()`, `close()`, `kv`: SQLite database helper methods. Use for relational data; prefer migrations or explicit schema ownership.
- B `PluginKVStore.get/set/delete/exists/keys/clear/count`: KV store attached to `PluginDatabase.kv`. Use when you already own a plugin database and need KV data alongside tables.
- B `PluginStatePersistence.save(instance)`, `load(instance)`, `clear()`, `snapshot()`, `collect_attrs(instance)`, `restore_attrs(instance, snapshot)`, `has_saved_state()`, `get_state_info()`: Attribute persistence helper. Use for controlled state snapshots, not as a general config system.

### Memory and System Info

- B `await self.bus.memory.get(bucket_id="default", limit=20, timeout=5.0)`: Reads recent memory records from a bucket inside an async entry; `bucket_id` is keyword-only.
- B `await self.ctx.query_memory("default", query, timeout=5.0)`: Queries a memory bucket for semantic lookup workflows.
- B `SystemInfo.get_system_config(timeout=5.0)`: Reads host system config. Use only when plugin behavior genuinely depends on global config.
- B `SystemInfo.get_server_settings(timeout=5.0)`: Reads server settings subset. Use for diagnostics and compatibility behavior.
- B `SystemInfo.get_python_env()`: Returns Python/OS environment info. Use in diagnostics, not normal runtime logic.

### Bus Read/Watch Facade

- A `self.bus.messages.get(...)`, `events.get(...)`, `lifecycle.get(...)`, `conversations.get(...)`, `memory.get(bucket_id="default", limit=20, timeout=5.0)`: Read host bus namespaces through the object returned on `self.bus`. Use for observation and context, not publishing; memory `bucket_id` is keyword-only. Do not import the internal `SdkBusContext` class.
- A `conversations.get_by_id(conversation_id, max_count=10, timeout=5.0)`: Reads a conversation by id. Use for context-aware plugins with explicit privacy boundaries.
- A Bus lists returned by `get()` expose `count()`, `size()`, `dump()`, `dump_records()`, `explain()`, and `trace_tree_dump()` for result inspection; their concrete SDK classes are internal and must not be imported.
- A Returned bus lists expose `filter(...)`, `where(predicate)`, `sort(by=None, key=None, reverse=False)`, `limit(size)`, and `watch(...)`. Callable `filter(predicate)`, `where(predicate)`, and `sort(key=callable)` are local-only; replayable watcher chains must use structured `filter(field=value, ...)` and `sort(by=...)`. The old `where_*` helpers and set operators are removed.
- B Watchers returned by `watch()` expose `start()`, `stop()`, and `subscribe(on="add|del|change")` for `messages`, `events`, or `lifecycle` deltas. `conversations` and `memory` are read-only snapshots; arbitrary event names are not accepted.
- B Records and deltas returned by the bus provide typed attributes at runtime, but their concrete `SdkBus*` classes are internal. Use behavior/attribute assertions in plugin tests instead of importing from `plugin.sdk.shared`.

### LLM Tool API

- A `@llm_tool(name, description="", parameters=None, timeout=30.0, role=None)`: Declares a model-callable tool backed by a plugin method. Use for stable schemas known at authoring time.
- A `LlmToolMeta.to_ipc_payload(plugin_id=...)`: Serializes tool registration for host IPC. Mostly internal; useful in tests.
- B `plugin.sdk.plugin.llm_tool.validate_tool_name(name)`, `entry_id_for_tool(tool_name)`, `collect_llm_tool_methods(instance)`: LLM tool submodule helpers. Use in tests/tooling, not normal plugin code.

### Activity

- A `get_os_activity_snapshot(include_window=True, include_privacy=True, timeout=...)`: Reads OS activity snapshot through system signals. Use for context-aware plugins that need foreground/idle/privacy state.
- A `OsActivitySnapshot`: Dataclass snapshot returned by activity helpers. Use for typed inspection of activity state.

## Hosted TSX Facade: `@neko/plugin-ui`

Full prop and component signatures live in `hosted-ui-api.md`; this section is the name index.

- S `PluginSurfaceProps`: Props passed to hosted TSX default exports. Contains `plugin`, `surface`, `state`, `actions`, `entries`, `config`, `warnings`, `locale`, `t`, `i18n`, `api`, and `useLocalState`.
- S `HostedApi.call(actionId, args?, options?)`: Calls a UI-authorized plugin action. Requires `action:call` permission and a running plugin.
- S `HostedApi.refresh()`: Reloads Hosted UI context. Use after actions that mutate state.
- S `useI18n()`: Returns `{ t, locale }` backed by plugin i18n messages. Use for visible copy in TSX.
- S `h`, `Fragment`, `render`: Minimal JSX/runtime primitives. Use implicitly through TSX and imported components.
- S Layout/display components `Page`, `Card`, `Section`, `Heading`, `Stack`, `Grid`, `Text`, `Divider`, `Toolbar`, `ToolbarGroup`: Build hosted UI layout. Keep UIs practical and data-oriented.
- S Data/status components `StatusBadge`, `StatCard`, `KeyValue`, `DataTable`, `Progress`, `JsonView`, `CodeBlock`: Present status, tables, key-value data, progress, JSON, and code. Use instead of custom HTML for standard panels.
- S Feedback components `Alert`, `InlineError`, `ErrorBoundary`, `EmptyState`, `Tip`, `Warning`: Present empty, warning, and error states. Prefer these over handwritten fallback blocks.
- S Form/control components `Field`, `Input`, `Select`, `Textarea`, `Switch`, `Form`, `Button`, `ButtonGroup`: Build settings and action forms. Use controlled values and explicit validation.
- S Action components `ActionButton`, `RefreshButton`, `ActionForm`: Call plugin actions from context. Prefer these for common action flows.
- A Overlay/navigation components `Modal`, `ConfirmDialog`, `List`, `Steps`, `Step`, `Tabs`: Use for complex but bounded UI flows.
- S State hooks `useState`, `useReducer`, `useEffect`, `useLayoutEffect`, `useMemo`, `useCallback`, `useRef`: React-like local runtime hooks. Keep side effects idempotent because hosted payloads can refresh.
- S Hosted helpers `useLocalState`, `useDebounce`, `useDebouncedState`, `useForm`, `useAsync`: UI runtime helpers for persisted local widget state, debouncing, forms, and async loading.
- S Toast/confirm helpers `showToast`, `useToast`, `useConfirm`: User feedback and confirmation helpers. Use for action outcomes and destructive confirmations.
- A Types `Tone`, `JsonSchema`, `HostedAction`, `HostedI18n`, `DataTableColumn`, `FormState`, `AsyncState`, `RefObject`, `CommonProps`: Type hosted TSX surfaces. Import from `@neko/plugin-ui` rather than duplicating shapes.

## Adapter Facade: `plugin.sdk.adapter`

### Adapter Base and Context

- B `NekoAdapterPlugin`: Plugin-compatible adapter base that combines `NekoPluginBase` with adapter registries. Use for adapter packages that need N.E.K.O plugin entries plus protocol gateway behavior.
- B `NekoAdapterPlugin.adapter_config`, `adapter_context`, `adapter_mode`, `adapter_id`: Adapter properties. Use to inspect adapter configuration and identity.
- B `NekoAdapterPlugin.adapter_startup()`, `adapter_shutdown()`: Adapter lifecycle hooks returning `Result`. Override or call when adapter resources need explicit startup/shutdown.
- B `NekoAdapterPlugin.register_adapter_tool/resource`, `get_adapter_tool/resource`, `list_adapter_tools/resources`: Runtime adapter tool/resource registry. Use for protocol-exposed capabilities.
- B `NekoAdapterPlugin.add_adapter_route(rule)`, `find_matching_route(protocol, action)`, `list_adapter_routes()`: Adapter route management. Use for protocol routing tables.
- B `NekoAdapterPlugin.forward_to_plugin(plugin_id, entry_id, payload, timeout=30.0)`: Forwards a protocol payload to a plugin entry. Use when an adapter delegates to existing plugins.
- B `NekoAdapterPlugin.handle_adapter_message(protocol, action, payload)`: Handles a normalized adapter message. Use as a high-level dispatch point for adapter transports.
- B `NekoAdapterPlugin.register_adapter_tool_as_entry(name, handler, display_name="", description="")`: Publishes an adapter tool as a plugin entry. Use for discovered external tools.
- B `NekoAdapterPlugin.unregister_adapter_tool_entry(name)`: Removes a tool-backed dynamic entry. Use when external tools disappear.
- B `AdapterBase`: Lower-level adapter base with tool/resource/route registries and lifecycle stubs. Use for pure adapter components.
- B `AdapterBase.adapter_id`, `mode`: Adapter identity and mode properties. Use for routing and diagnostics.
- B `AdapterBase.register_tool`, `unregister_tool`, `get_tool`, `list_tools`: Tool registry methods. Use for external protocol tools.
- B `AdapterBase.register_resource`, `unregister_resource`, `get_resource`, `list_resources`: Resource registry methods. Use for external resource read APIs.
- B `AdapterBase.add_route(rule)`, `list_routes()`: Adds/lists route rules. Use for adapter routing configuration.
- B `AdapterBase.forward_to_plugin(plugin_id, entry_id, payload, timeout=30.0)`: Delegates to plugin entries through `AdapterContext`.
- B `AdapterBase.broadcast(event_type, payload, protocol=None)`: Broadcasts adapter events to registered handlers. Use for protocol fan-out.
- B `AdapterBase.on_message(message)`, `on_startup()`, `on_shutdown()`: Override lifecycle/message hooks. Defaults are no-op `Ok`.
- B `AdapterConfig`, `AdapterConfig.from_dict(raw)`: Adapter configuration dataclass and parser. Use for manifest/config-derived adapter settings.
- B `AdapterContext.register_event_handler(...)`, `get_event_handlers(...)`: Registers and queries adapter event handlers. Usually populated by decorators.
- B `AdapterContext.call_plugin(...)`, `broadcast_event(...)`: Context-level plugin delegation and event fan-out. Use from adapter internals.
- B `AdapterMode`: Enum `gateway`, `router`, `bridge`, `hybrid`. Use to classify adapter behavior.

### Adapter Decorators and Types

- B `@on_adapter_event(protocol="*", action="*", pattern=None, priority=0)`: Registers adapter event handlers. Use to bind protocol/action patterns.
- B `@on_adapter_startup(priority=0)`, `@on_adapter_shutdown(priority=0)`: Registers adapter lifecycle callbacks. Use for transport setup/teardown.
- B `@on_mcp_tool(pattern="*", priority=0)`, `@on_mcp_resource(pattern="*", priority=0)`: MCP-specific adapter handler shortcuts. Use only for MCP adapters.
- B `@on_nonebot_message(message_type="*", priority=0)`: NoneBot message shortcut. Use only for NoneBot adapters.
- B `AdapterEventMeta.matches(protocol, action)`: Tests whether event metadata matches a protocol/action. Use for adapter tests and routing.
- B `Protocol`, `RouteTarget`, `AdapterMessage`, `AdapterResponse`, `RouteRule`: Adapter type/dataclass vocabulary. Use for typed routing and protocol payloads.

### Gateway Core

- B `ExternalRequest`: Transport-facing request dataclass. Use at adapter transport boundaries.
- B `GatewayRequest`: Normalized request dataclass. Use after request normalization.
- B `GatewayAction`: Enum `tool_call`, `resource_read`, `event_push`. Use to classify normalized external requests.
- B `RouteMode`: Enum `self`, `plugin`, `broadcast`, `drop`. Use for gateway routing decisions.
- B `RouteDecision`: Routing decision dataclass. Use between route engine and invoker.
- B `GatewayResponse`: Transport-facing response dataclass. Use after serialization.
- B `GatewayError`, `GatewayErrorException`: Gateway error payload/exception. Use for structured transport errors.
- B `TransportAdapter.start/stop/recv/send`: Protocol interface for external transports. Implement for custom gateway transports.
- B `RequestNormalizer.normalize(incoming)`: Protocol interface for request normalization. Implement to map transport payloads into `GatewayRequest`.
- B `PolicyEngine.authorize(request)`: Protocol interface for authorization. Implement to enforce adapter policy.
- B `RouteEngine.decide(request)`: Protocol interface for routing. Implement to choose self/plugin/broadcast/drop.
- B `PluginInvoker.invoke(request, decision)`: Protocol interface for invocation. Implement to call plugin or adapter logic.
- B `ResponseSerializer.build_success_response(...)`, `build_error_response(...)`: Protocol interface for response serialization. Implement to map results/errors to transport responses.
- B `AdapterGatewayCore.start()`, `stop()`, `run_once()`, `handle_request(incoming)`: Gateway orchestrator. Use when building a full adapter pipeline.
- B `DefaultRequestNormalizer.normalize(...)`: Default conversion from `ExternalRequest` to `GatewayRequest`. Use for simple JSON-ish protocols.
- B `DefaultPolicyEngine.authorize(...)`: Default payload-size and allowed-plugin policy. Use as a baseline, not a security review substitute.
- B `DefaultRouteEngine.decide(...)`: Default explicit-target/self routing. Use for simple adapters.
- B `DefaultResponseSerializer.build_success_response(...)`, `build_error_response(...)`: Default JSON response builder. Use unless the transport needs a custom shape.
- B `CallablePluginInvoker.invoke(...)`: Adapter invoker around a plain callable. Use in tests or simple adapters.

## Platform and Verification Surfaces

- D `plugin/config/schema.py`, `plugin/config/plugin_toml_semantics.py`: Manifest schema and semantic rules. Read before changing manifest support.
- D `plugin/core/registry.py`, `plugin/core/host.py`, `plugin/core/entry_points.py`: Runtime registration, child process, and entry import logic. Read to debug platform behavior; do not depend on it from plugins.
- D `plugin/server/application/plugins/`, `plugin/server/routes/plugin_ui.py`: Server query/UI/action endpoints. Read to understand Plugin Manager behavior.
- D `plugin/server/infrastructure/config_profiles*.py`, `plugin/server/application/config/*`: Config profile and hot-update implementation. Read when debugging config semantics.
- D `plugin/tests/unit/server/test_plugin_ui_manifest.py`, `test_plugin_ui_query_service.py`, `test_trigger_service.py`, `test_config_updates.py`: Behavior tests. Search tests before platform edits.
- D `frontend/plugin-manager/src/components/plugin/hosted/`, `plugin/sdk/hosted-ui/index.d.ts`: Hosted TSX runtime and type source of truth. Edit only when the user explicitly requests platform UI API changes.
