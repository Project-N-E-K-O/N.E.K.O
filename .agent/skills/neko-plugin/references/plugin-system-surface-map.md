# Plugin System API Index

Use this as the source-map for plugin-system APIs before inventing an abstraction. It indexes public and semi-public surfaces by layer, names the callable classes/functions/methods, and marks the recommendation level for plugin authors.

## Recommendation Levels

- S: default choice for ordinary plugin work.
- A: use when the capability is required.
- B: advanced surface for complex plugins; keep usage narrow and documented.
- C: compatibility/legacy surface, or public but currently without effect; avoid for new work unless preserving behavior.
- D: internal read-only platform surface; do not import from plugin workspaces.

## Calling Conventions

Getting `await` or error handling wrong fails silently, so check here first.

| API | Call | On failure |
|---|---|---|
| `self.config.*` | `await` | raises `ValidationError` / `TransportError` |
| `self.plugins.*`, `self.store.*`, `self.db.*`, `self.state.*` | `await` | returns `Err(...)` |
| `push_message` | plain call, **never** `await` | returns `{"submitted": False, "reason": ...}` |
| `run_update`, `export_push`, `finish`, `ctx.models.get_client()`, `ctx.images.upload()`, `create_card`, `create_view`, card `update` | `await` | |
| `report_status`, `tr`, `self.i18n.t`, `data_path`, `cache_path`, `include_router`, `get_card`, `get_view` | plain call | |
| `self.bus.<ns>.get(...)` | `await` inside the event loop (it returns a coroutine there); `conversations_v2.get` is always async | |

- Awaiting `push_message` raises `TypeError` **after** the message was queued; retrying it sends the message twice.
- `Ok` and `Err` have no truth value of their own: `if result:` is always true. Use `result.is_ok()`, `unwrap(result)`, or `unwrap_or(result, default)`.

## Import Layers

- S `plugin.sdk.plugin`: Standard plugin facade. Import `NekoPluginBase`, decorators, result helpers, config/runtime helpers, i18n, settings, UI helpers, and ordinary plugin APIs from here.
- `plugin.sdk.extension` and the extension package type were removed; see the [migration guide](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/migration-v0.9.md).
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
- S `metadata`: Dict copy of plugin metadata. Use for read-only inspection of manifest-derived data.
- A `bus`: Read/watch facade over host state. Use for observing host messages/events/memory; do not treat it as a publish bus.
- A `system_info`: `SystemInfo` facade for system/server config and Python environment inspection. Use sparingly and handle `Err`.

### Paths

Every path comes from the SDK because the plugin's location and the user's data directory differ per install (constraint 6 in `execution-boundary.md`). None of these calls creates directories; `mkdir(parents=True, exist_ok=True)` before writing.

| Member | Points at | Writable |
|---|---|---|
| S `data_path(*parts)` | persistent plugin data (`<storage_dir>/data/...`) | yes |
| S `cache_path(*parts)` | disposable cache (`<storage_dir>/cache/...`) | yes; may be cleared |
| A `runtime_config_path` (property) | the plugin's runtime config file | yes; prefer reading and writing it through `await self.config...` |
| B `storage_dir` (property) | the per-plugin user storage root holding the three above | through the helpers above |
| S `plugin_dir` (property) | the installed payload: `plugin.toml`, code, shipped assets | **no**: an upgrade replaces it, and in a project it is the Git working tree |
| C `config_dir` (property) | compatibility alias of `plugin_dir` | **no**; use `plugin_dir` in new code |
| A `shared_path(*parts)` | files this plugin publishes for other plugins (kept when the plugin is removed) | yes |
| A `shared_path_of(plugin_id, *parts)` | another plugin's published files; that id must be listed in `[plugin.shared] reads`, otherwise `ValidationError` | read only (by agreement, not enforced) |

Source: [base.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/sdk/plugin/base.py).

### `NekoPluginBase` Methods

- A `refresh_runtime_config(effective_config=None)`: Refreshes store/db/state runtime toggles after config changes. Call from config-change flows when you manage runtime helpers manually.
- A `get_input_schema()`: Returns class-level `input_schema` if present. Mostly useful for introspection and compatibility.
- A `run_update(**kwargs)`: Forwards a run update to the host. Use for long-running entry progress when the host expects run status.
- A `export_push(**kwargs)`: Pushes export artifacts through the host export channel. Prefer structured arguments; treat as async host integration.
- A `finish(**kwargs)`: Marks the current task complete and sends structured completion data. Use for entry flows that intentionally complete a host task.
- S `push_message(**kwargs)`: Sends plugin output into the conversation. See Push Message below.
- A `include_router(router, prefix="")`: Mounts a `PluginRouter`. Use only for large or feature-split plugins (see the Router Contract in `core-plugin-contract.md`); list the router classes in `__routers__` too.
- C `exclude_router(router_or_name)`: Unmounts a router by object or name. Rarely needed outside dynamic plugin composition.
- B `get_router(name)`: Looks up a mounted router. Use for diagnostics or dynamic router operations.
- B `list_routers()`: Lists mounted router names. Use for diagnostics, not normal business logic.
- B `register_static_ui(directory="static", index_file="index.html", cache_control=...)`: Registers legacy static iframe UI. Prefer `[plugin.ui]` Hosted TSX/Markdown for new surfaces.
- C `get_static_ui_config()`: Returns the registered static UI config. Use only for legacy static UI diagnostics.
- A `set_list_actions(actions)`: Replaces plugin-list actions shown by Plugin Manager. Prefer `ui.action` for Hosted UI action surfaces when possible.
- A `register_list_action(action)`: Adds or replaces a single plugin-list action. Use for manager-level actions that are not tied to a Hosted TSX surface.
- A `clear_list_actions()`: Clears plugin-list actions. Use during shutdown/reload cleanup when actions are dynamic.
- A `get_list_actions()`: Returns registered list actions. Use for diagnostics or UI context snapshots.
- B `register_dynamic_entry(entry_id, handler, ..., output_schema=None)`: Registers runtime-computed entries. Use for capabilities discovered after startup, such as adapter-discovered tools. Dynamic entries have no return annotation to infer from; an explicit `output_schema` is published only.
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
- S `@plugin_entry(id=None, name=None, description="", input_schema=None, params=None, kind="action", auto_start=False, persist=None, model_validate=True, timeout=None, llm_result_fields=None, llm_result_model=None, fields=None, metadata=None, quick_action=False, output_schema=None, strict_output=False)`: Registers a user/agent-callable entry. Runtime-triggered handlers must be `async def`; use `tr()` for names/descriptions. A Pydantic return annotation declares the return contract and is validated at runtime (`strict_output=True` makes violations fail the call); an explicit `output_schema` is published only.
- A `@lifecycle(id="startup|shutdown|reload|freeze|unfreeze|config_change", name=None, description="", metadata=None)`: Registers lifecycle handlers. Use for startup/shutdown/config behavior, not for user-callable work.
- C `@message(id, name=None, description="", input_schema=None, source=None, metadata=None)`: Registers a message handler, but the host does not dispatch host messages to it today; it runs only if another plugin calls `<plugin_id>:message:<id>` with `call_event`. Do not build features on it.
- S `@timer_interval(id, seconds, name=None, description="", auto_start=True, metadata=None)`: Periodic work (`runtime-model.md`). Bound your own network calls: ticks have no timeout.
- B `@custom_event(event_type, id, name=None, description="", input_schema=None, kind="custom", auto_start=False, trigger_method="message", metadata=None)`: Registers a non-standard event type. Use only when built-in entry/message/timer/lifecycle categories do not fit.
- B `@on_event(event_type, id=None, name=None, description="", input_schema=None, kind="action", auto_start=False, persist=None, mode=None, seconds=None, extra=None, metadata=None)`: Low-level event registration. Prefer specialized decorators unless you need exact event metadata control.
- C `@hook(...)`, `@before_entry(...)`, `@after_entry(...)`, `@around_entry(...)`, `@replace_entry(...)`: Record hook metadata only. No executor runs them today (`collect_entries` ignores `wrap_with_hooks`), so they have no effect; put the logic in the entry or a helper it calls.
- A `@quick_action(icon=None, priority=0)`: Marks an entry for quick-action presentation. Prefer explicit UX intent rather than blanket use.
-  `plugin.entry(...)`: Namespace alias for `plugin_entry`. Use when it improves readability in classes with many SDK decorators.
- A `plugin.lifecycle(...)`, `plugin.message(...)`, `plugin.timer(...)`: Namespace aliases for lifecycle/message/timer decorators. Equivalent to top-level decorators.
- B `plugin.event(...)`, `plugin.custom_event(...)`: Namespace aliases for low-level event/custom decorators. Prefer specialized top-level decorators first. (`plugin.hook(...)` is the hook alias and has no effect, like `@hook`.)
- S `EntryKind`: Literal kind vocabulary for entry presentation. Use for typing only; most entries can keep `kind="action"`.

### Hosted UI Python Helpers: `plugin.sdk.plugin.ui`

- S `ui.context(id="main", title=None)`: Marks a method (sync or async) as a Hosted UI context provider. Its return value **is** `props.state` (a Pydantic model is dumped and its schema sent as `stateSchema`; a non-dict/list value arrives as `{"value": ...}`). Only methods on the main plugin class are collected, not on routers.
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
- C `CallChain`, `AsyncCallChain`, `CallChainTooDeepError`, `get_call_chain()`, `get_call_depth()`, `is_in_call_chain(...)`: In-process context-variable helpers, separate from the host's `self.ctx.call_chain` (Cross-Plugin Calls). `self.plugins.*` calls neither read nor propagate them, so they see nothing across plugins. `CircularCallError` is an alias of `PluginCallError`.
- B `EventMeta`, `EventHandler`, `EVENT_META_ATTR`: Event metadata/handler surfaces. Use for tests, diagnostics, and advanced dynamic registration.
- B `HookMeta`, `HookHandler`, `HookTiming`, `HOOK_META_ATTR`, `HookExecutorMixin`: Hook metadata types; `HookExecutorMixin` raises `NotImplementedError`. Nothing executes hooks today.
- B `PluginContextProtocol`: Protocol for plugin host context. Use for type annotations in helper classes that receive `ctx`.
- B `EXTENDED_TYPES`: State persistence extended type vocabulary. Use when debugging persisted attribute serialization.

### Cross-Plugin Calls: `Plugins`

- A `Plugins.list(timeout=5.0, enabled=None)`: Lists discoverable plugins, optionally filtering enabled state. Use before cross-plugin calls when availability matters.
- A `Plugins.list_ids(timeout=5.0, enabled=None)`: Lists plugin ids. Use for quick existence checks or UI state.
- A `Plugins.get(plugin_id, timeout=5.0)`: Gets a plugin descriptor or `None`. Use when you need metadata before calling.
- A `Plugins.exists(plugin_id, timeout=5.0)`: Checks whether a plugin exists. Use for optional integrations.
- A `Plugins.require_enabled(plugin_id, timeout=5.0)`: Requires a plugin to exist and be enabled. Use for hard dependencies.
- A `Plugins.call_entry(entry_ref, params=None, timeout=10.0)`: Calls `<plugin_id>:<entry_id>`. Prefer this for normal plugin entry calls. The effective timeout is capped at 60 s. The host refuses an RPC depth above 32 (`CALL_DEPTH_EXCEEDED`), and each cross-plugin call adds two levels (plugin → host → plugin), so an entry the host delivered can nest at most 16 calls; the 17th fails. The depth only follows calls awaited inside the same invocation: a call made from a timer, `run_in_executor` or a bare thread, or a background task that outlives the entry starts again at depth 0, so recursion that hops through those is not bounded. Nothing detects a cycle: A → B → A is delivered normally, and a call made while holding a lock the callee needs, or two plugins waiting on each other, waits until it times out.
- A `Plugins.call_event(event_ref, params=None, timeout=10.0)`: Calls `<plugin_id>:<event_type>:<event_id>`. Use for non-entry event types.
- A `Plugins.call_entry_json(entry_ref, args=None, timeout=10.0)`: Calls an entry and requires object/None payload. Use when downstream code expects a JSON object.
- A `Plugins.call_event_json(event_ref, args=None, timeout=10.0)`: Calls an event and requires object/None payload. Use for structured event integration.
- B `self.ctx.call_chain`: Read-only view of how the current invocation was reached, built by the host from its own records ([sdk-reference](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/sdk-reference.md)). Fields: `callers` (plugin ids that led here, root first; `()` when the host called you directly), `origin.kind` (`run`, `chat_tool`, `chat_hook`, `ui_action`, `plugin_self`, `system`) and `origin.ref` (an id, never message text), `hops` (`plugin_id`, `event_type`, `event_id`), `depth`, `trace_id`, `call_id`, `rights`, `rights_known`. It is never `None`: in a timer, in a background task still running after the entry returned, in work handed to `run_in_executor` or a bare `threading.Thread` (`asyncio.to_thread` keeps it), or when the host policy is off, it holds only your plugin with `rights_known=False`. `rights_known` is also `False` in a normal host-delivered call when the host could not read some chain plugin's consent record (including a 5 s back-off after a failed read).
  - `rights` is the set of capabilities (`memory.read`, `conversations.read`, `messages.push`) approved for every plugin on the chain when the call was delivered. `allows(capability)` returns `False` whenever `rights_known` is `False`, so "not allowed" and "unknown" look the same; check `rights_known` to tell them apart. It is advisory: the host judges each checked use itself.
  - Today the host only records. The host setting `NEKO_PLUGIN_CAPABILITY_POLICY` is `observe` by default (`off` skips the records and the view; `enforce` currently behaves like `observe`). The checked uses are memory reads (`MemoryClient(self.ctx).search` / `get_persona`, `self.bus.memory.get`, and the deprecated `self.ctx.query_memory`), `conversations_v2.get_reply` / `get_interaction`, and `push_message`; one that some plugin on the chain was not approved for still succeeds and is written to the host's audit log (deduplicated; ids and capability names only). Conversation reads through `conversations_v2.get` / `get_message` / `watch` and `bus.conversations.*` travel over the message plane, are not checked, and leave no audit entry, so a missing entry does not mean a read was approved. Refusing such uses (`CAPABILITY_NOT_GRANTED`) and a `push_message` pre-check are planned for a later stage, not implemented; do not rely on another plugin to read memory or conversations, or push messages, on your behalf.
- B `parse_entry_ref(entry_ref)`, `parse_event_ref(event_ref)`: Parse reference strings into typed refs. Use in validation or tooling, not needed for ordinary calls.
- B `PluginDescriptor`, `PluginCallError`, `InvalidEntryRefError`, `InvalidEventRefError`: Cross-plugin typing/error surfaces. Use for precise error handling in integrations.

### Config: `PluginConfig`

- S `dump(timeout=5.0)`: Returns the current effective config. Use for full config snapshots.
- S `get(path, default=None, timeout=5.0)`: Reads a dotted path from effective config. Use for optional config values.
- S `require(path, timeout=5.0)`: Reads a required dotted path or raises. Use when missing config should fail fast.
- S `get_bool(path, default=None, timeout=5.0)`, `get_int(...)`, `get_str(...)`: Typed config reads. Use to catch type mismatches near the boundary.
- A `set(path, value, timeout=5.0)`: Writes a dotted path into the active profile overlay; a dict value **replaces** what was there. Use only for user-intended config writes.
- A `update(patch, timeout=5.0)`: Deep-merges a patch into the active profile overlay. Use for settings forms or controlled config updates.
- Writes into the `[plugin]` section have no effect: the effective `[plugin]` always comes from `plugin.toml`, and changing `plugin.id`/`plugin.entry` is rejected.
- `@lifecycle(id="config_change")` runs when the host hot-updates the config (not after your own `set`/`update`). It is called with keywords `old_config=`, `new_config=`, `mode=`; accept `**_`. Raising there rolls back only the plugin's in-memory copy; a permanent change is already on disk.
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
- B `prefix` property, `set_prefix(prefix)`: Controls id prefixing for router entries. Routers listed in `__routers__` should not use it: the host's static scan ignores the prefix (Router Contract in `core-plugin-contract.md`).
- B `tags` property: Returns router tags. Use for diagnostics or future grouping; do not rely on it for dispatch.
- B `is_bound()`: Reports whether the router is mounted to a plugin. Use for diagnostics.
- B `entry_ids()`: Lists resolved entry ids. Use in tests or UI diagnostics.
- B `ctx`, `config`, `plugins`, `logger`, `file_logger`, `store`, `db`, `plugin_id`, `main_plugin`: Bound accessors delegated from the main plugin. Use only after the router is included.
- B `name()`: Returns the router name. Use with `get_router`/`exclude_router`.
- B `iter_handlers()`, `collect_entries()`: Returns router entry handlers. Mostly host/test facing.
- B `get_plugin_attr(name, default=None)`, `has_plugin_attr(name)`, `get_dependency(name, default=None)`: Accesses main-plugin attributes from a router. Prefer explicit constructor dependencies when possible.
- B `report_status(status)`: Delegates status reporting to the main plugin. Use for router-local status updates.
- C `on_mount()`, `on_unmount()`: Never called by the host today; do not put setup or cleanup there.
- C `add_entry(...)`, `remove_entry(...)`, `list_entries()`: Async, return `Result`; entries added this way are missing from the host's static scan (timeouts, entry list). Use `@plugin_entry` on the router instead.
- B `RouteHandler`, `PluginRouterError`, `EntryConflictError`: Router typing/error surfaces. Use for precise router tests and dynamic entry errors.

### Store, Database, and State

- `self.store` is off unless `plugin.toml` has `[plugin.store] enabled = true`. While off, `set` returns `Ok(None)` and stores nothing, and `get` returns the default. Values must be JSON-compatible (`None`, `str`, numbers, `bool`, lists, dicts with `str` keys); anything else returns `Err(ValidationError)`.
- A `PluginStore.get(key, default=None)`: Reads JSON-compatible key-value data. Returns `Result`; handle `Err`.
- A `PluginStore.set(key, value)`: Writes JSON-compatible data. Use for small plugin-owned state.
- A `PluginStore.delete(key)`, `exists(key)`, `keys(prefix="")`, `clear()`, `count()`, `dump()`, `close()`: Store maintenance and inspection helpers. Use for settings/state UIs, tests, and shutdown cleanup.
- B `PluginDatabase.create_all()`, `drop_all()`, `session()`, `close()`, `kv`: SQLite database helper methods. Use for relational data; prefer migrations or explicit schema ownership.
- B `PluginKVStore.get/set/delete/exists/keys/clear/count`: KV store attached to `PluginDatabase.kv`. Use when you already own a plugin database and need KV data alongside tables.
- B `PluginStatePersistence.save(instance)`, `load(instance)`, `clear()`, `snapshot()`, `collect_attrs(instance)`, `restore_attrs(instance, snapshot)`, `has_saved_state()`, `get_state_info()`: Attribute persistence helper. Use for controlled state snapshots, not as a general config system.

### Memory and System Info

- B `MemoryClient(self.ctx)`: read-only memory access; `await search(target=MemoryTarget, query=None, time=None, ...)` and `await get_persona(target=...)` return `Result`. Memory content is the user's private data: never log it (constraint 5).
- B `SystemInfo.get_system_config(timeout=5.0)`: Reads host system config. Use only when plugin behavior genuinely depends on global config.
- B `SystemInfo.get_server_settings(timeout=5.0)`: Reads server settings subset. Use for diagnostics and compatibility behavior.
- B `SystemInfo.get_python_env()`: Returns Python/OS environment info. Use in diagnostics, not normal runtime logic.

### Model Gateway

Call models through the host so the user's model choice, keys, and quotas apply:

```toml
[plugin.models.summary]          # a usage name of your choice
label = "Summaries"
capabilities = ["text"]
```

```python
client = await self.ctx.models.get_client()           # an AsyncOpenAI client
reply = await client.chat.completions.create(
    model="summary",                                  # the usage name, not a provider model
    messages=[{"role": "user", "content": text}],
    max_completion_tokens=256,
)
```

The user binds each usage to a model in N.E.K.O. A model call easily exceeds the default 10 s entry timeout: set `@plugin_entry(timeout=...)`. Example: [model_api_example](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/examples/model_api_example/__init__.py).

### Bus Read/Watch Facade

- A `SdkBusContext.messages.get(...)`, `events.get(...)`, `lifecycle.get(...)`, `conversations.get(...)`, `memory.get(bucket_id, limit=20, timeout=5.0)`: Read host bus namespaces. Use for observation and context, not publishing.
- A `conversations.get_by_id(conversation_id, max_count=10, timeout=5.0)`: Reads a conversation by id. Use for context-aware plugins with explicit privacy boundaries.
- A `SdkBusList.count()`, `size()`, `dump()`, `dump_records()`, `explain()`, `trace_tree_dump()`: Inspect bus query results. Use in UI state and diagnostics.
- A `SdkBusList.filter(...)`, `where(predicate)`, `where_in(field, values)`, `limit(size)`, `watch(...)`: Local filtering and watch setup. Use for reactive plugins with bounded data volume.
- B `SdkBusWatcher.start()`, `stop()`, `subscribe(on=...)`: Watch bus deltas. Use only when the plugin needs live host-state observation.
- B `SdkBusMessageRecord`, `SdkBusEventRecord`, `SdkBusLifecycleRecord`, `SdkBusConversationRecord`, `SdkBusMemoryRecord`, `SdkBusDelta`: Typed bus record/delta shapes. Use for typing and tests.

### LLM Tool API

- A `@llm_tool(name, description="", parameters=None, timeout=30.0, role=None)`: Declares a model-callable tool backed by a plugin method. Use for stable schemas known at authoring time.
- A `LlmToolMeta.to_ipc_payload(plugin_id=...)`: Serializes tool registration for host IPC. Mostly internal; useful in tests.
- B `plugin.sdk.plugin.llm_tool.validate_tool_name(name)`, `entry_id_for_tool(tool_name)`, `collect_llm_tool_methods(instance)`: LLM tool submodule helpers. Use in tests/tooling, not normal plugin code.

### Activity

- A `get_os_activity_snapshot(include_window=True, include_privacy=True, timeout=...)`: Reads OS activity snapshot through system signals. Use for context-aware plugins that need foreground/idle/privacy state.
- A `OsActivitySnapshot`: Dataclass snapshot returned by activity helpers. Use for typed inspection of activity state.

## Push Message

`self.push_message(...)` is a plain **synchronous** method (do not `await` it). It returns a receipt dict at once: `submitted=True` only means the SDK accepted the payload locally, not that the AI replied or the user saw it. `submitted=False` carries `reason`: `backpressure`, `transport_error`, `transport_unavailable` (retry later is meaningful) or `payload_too_large` (an identical retry fails again; send less).

```python
result = self.push_message(
    visibility=["chat"],           # where the user sees the parts verbatim: "chat", "hud", both, or [] (default)
    ai_behavior="read",            # "respond" (default: AI replies now), "read" (AI sees it, no reply), "blind" (AI ignores it)
    parts=[                        # ordered; each part has a "type"
        {"type": "text", "text": "Today's chart"},
        {"type": "image", "data": png_bytes, "mime": "image/png"},   # inline bytes
    ],
)
if not result["submitted"]:
    self.logger.warning("push failed: %s", result.get("reason"))   # never log the parts
```

- Part types: `text` (`text`), `image` / `audio` (`data: bytes` + `mime`, or `url` + `mime`), `video` (`url` + `mime`), `ui_action` (frontend side effects such as `media_play_url`). Audio and video do not reach the model yet.
- The whole push is capped at 512 KiB after base64 (an inline image of roughly 380 KiB at most). For larger images, `part = await self.ctx.images.upload(image_bytes, mime="image/png")` (async; not available in lifecycle handlers) and put the returned part into `parts`.
- At most 8 images per push reach the model; the model path does not keep text/image order, so describe pairings in one text part. HUD shows text parts only.
- Text may use `{MASTER_NAME}` / `{LANLAN_NAME}` placeholders; the host fills in the right names per session.
- `message_type`, `content`, `binary_data`, `delivery`, `reply`, and other v1 keywords are deprecated (they emit warnings); use `parts`, `visibility`, `ai_behavior`.

Full rules and limits: [Message Delivery](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/push-message.md); schema: [push_message_schema.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/sdk/shared/core/push_message_schema.py).

## Hosted TSX Facade: `@neko/plugin-ui`

Components, hooks, and types for `hosted-tsx` surfaces are listed in full in `hosted-ui-api.md`; the Python side (`ui.context`, `ui.action`) is above.

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
