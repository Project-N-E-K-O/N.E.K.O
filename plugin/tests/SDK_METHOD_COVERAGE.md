# SDK Method Coverage Inventory

This file inventories public functions/methods under `plugin/sdk` and maps them to automated checks.

- Modules with public APIs: 34
- Public module functions: 29
- Public class methods: 347
- Surface validation tests:
  - `plugin/tests/unit/sdk/core/test_sdk_method_surface_complete.py`

## Module Inventory

### `plugin/sdk/adapter/base.py`
- `functions`: (none)
- `AdapterConfig`: from_dict
- `AdapterContext`: call_plugin, broadcast_event, register_event_handler, get_event_handlers
- `AdapterBase`: adapter_id, mode, on_startup, on_shutdown, on_message, register_tool, register_resource, get_tool, get_resource, list_tools, list_resources, forward_to_plugin, broadcast, add_route

### `plugin/sdk/adapter/decorators.py`
- `functions`: on_adapter_event, on_adapter_startup, on_adapter_shutdown, on_mcp_tool, on_mcp_resource, on_nonebot_message
- `AdapterEventMeta`: matches

### `plugin/sdk/adapter/gateway_contracts.py`
- `functions`: (none)
- `LoggerLike`: debug, info, warning, error, exception
- `TransportAdapter`: start, stop, recv, send
- `RequestNormalizer`: normalize
- `PolicyEngine`: authorize
- `RouteEngine`: decide
- `PluginInvoker`: invoke
- `ResponseSerializer`: ok, fail

### `plugin/sdk/adapter/gateway_core.py`
- `functions`: (none)
- `AdapterGatewayCore`: start, stop, run_once, handle_envelope

### `plugin/sdk/adapter/gateway_defaults.py`
- `functions`: (none)
- `DefaultRequestNormalizer`: normalize
- `DefaultPolicyEngine`: authorize
- `DefaultRouteEngine`: decide
- `DefaultResponseSerializer`: ok, fail
- `CallablePluginInvoker`: invoke

### `plugin/sdk/adapter/neko_adapter.py`
- `functions`: (none)
- `NekoAdapterPlugin`: adapter_config, adapter_context, adapter_mode, adapter_id, adapter_startup, adapter_shutdown, register_adapter_tool, register_adapter_tool_as_entry, unregister_adapter_tool_entry, register_adapter_resource, get_adapter_tool, get_adapter_resource, list_adapter_tools, list_adapter_resources, add_adapter_route, find_matching_route, forward_to_plugin, handle_adapter_message

### `plugin/sdk/adapter/types.py`
- `functions`: (none)
- `AdapterMessage`: reply, error
- `AdapterResponse`: to_dict
- `RouteRule`: matches

### `plugin/sdk/base.py`
- `functions`: (none)
- `NekoPluginBase`: get_input_schema, include_router, exclude_router, get_router, list_routers, register_static_ui, get_static_ui_config, register_dynamic_entry, unregister_dynamic_entry, enable_entry, disable_entry, is_entry_enabled, list_entries, collect_entries, report_status, enable_file_logging

### `plugin/sdk/bus/bus_list.py`
- `functions`: (none)
- `BusListCore`: reload_with, reload, reload_with_async
- `BusListWatcherCore`: subscribe, start, stop

### `plugin/sdk/bus/conversations.py`
- `functions`: (none)
- `ConversationRecord`: from_raw, from_index
- `ConversationClient`: get_sync, get_async, get, get_by_id

### `plugin/sdk/bus/events.py`
- `functions`: (none)
- `EventRecord`: from_raw, from_index, dump
- `EventList`: merge
- `EventClient`: get_sync, get_async, get, delete_sync, delete_async, delete

### `plugin/sdk/bus/lifecycle.py`
- `functions`: (none)
- `LifecycleRecord`: from_raw, from_index, dump
- `LifecycleList`: merge
- `LifecycleClient`: get_sync, get_async, get, delete_sync, delete_async, delete

### `plugin/sdk/bus/memory.py`
- `functions`: (none)
- `MemoryRecord`: from_raw, dump
- `MemoryList`: filter, where, limit
- `MemoryClient`: get_sync, get_async, get

### `plugin/sdk/bus/messages.py`
- `functions`: (none)
- `MessageRecord`: from_raw, from_index, dump
- `MessageList`: merge
- `MessageClient`: get_message_plane_all, get_sync, get_async, get, get_by_conversation

### `plugin/sdk/bus/records.py`
- `functions`: parse_iso_timestamp
- `BusRecord`: dump
- `TraceNode`: dump, explain
- `GetNode`: dump
- `UnaryNode`: dump, explain
- `BinaryNode`: dump, explain

### `plugin/sdk/bus/rev.py`
- `functions`: register_bus_change_listener, dispatch_bus_change
- `classes`: (none)

### `plugin/sdk/bus/types.py`
- `functions`: parse_iso_timestamp
- `BusHubProtocol`: messages, events, lifecycle, memory, conversations
- `BusRecord`: dump
- `TraceNode`: dump, explain
- `GetNode`: dump
- `UnaryNode`: dump, explain
- `BinaryNode`: dump, explain
- `BusList`: count, size, dump, dump_records, fast_mode, trace, trace_dump, trace_tree_dump, explain, merge, sort, sorted, intersection, intersect, difference, subtract, filter, where_in, where_eq, where_contains, where_regex, where_gt, where_ge, where_lt, where_le, try_filter, where, limit, reload, reload_with, reload_with_async, watch

### `plugin/sdk/bus/watchers.py`
- `functions`: list_subscription
- `classes`: (none)

### `plugin/sdk/call_chain.py`
- `functions`: get_call_chain, get_call_depth, is_in_call_chain
- `CallChain`: get_current_chain, get_depth, get_current_call, get_root_call, is_in_call, track, clear, format_chain
- `AsyncCallChain`: is_available, get_current_chain, get_depth, track, format_chain

### `plugin/sdk/config.py`
- `functions`: (none)
- `PluginConfig`: dump, dump_sync, dump_base, dump_base_sync, get_profiles_state, get_profiles_state_sync, get_profile, get_profile_sync, dump_effective, dump_effective_sync, get, get_sync, require, require_sync, update, update_sync, set, set_sync, get_section, get_section_sync

### `plugin/sdk/database.py`
- `functions`: (none)
- `PluginDatabase`: create_all_sync, create_all, drop_all_sync, drop_all, session, async_session, get_session, get_async_session, close, close_async, engine, async_engine, db_path, db_exists, kv
- `PluginKVStore`: get, set, delete, exists, keys, clear, count

### `plugin/sdk/decorators.py`
- `functions`: neko_plugin, on_event, worker, plugin_entry, lifecycle, message, timer_interval, custom_event, hook
- `PluginDecorators`: worker, entry

### `plugin/sdk/hook_executor.py`
- `functions`: (none)
- `HookExecutorMixin`: collect_hooks, get_hooks_for_entry, execute_before_hooks, execute_after_hooks, get_around_hooks, get_replace_hook

### `plugin/sdk/hooks.py`
- `functions`: (none)
- `HookMeta`: is_cross_plugin, target_plugin, target_entry

### `plugin/sdk/logger.py`
- `functions`: enable_plugin_file_logging, plugin_file_logger
- `PluginFileLogger`: setup, get_logger, get_log_file_path, get_log_directory, cleanup

### `plugin/sdk/memory.py`
- `functions`: (none)
- `MemoryClient`: get, query

### `plugin/sdk/message_plane_transport.py`
- `functions`: format_rpc_error
- `MessagePlaneRpcClient`: request_async, request_sync, request, batch_request_async

### `plugin/sdk/plugins.py`
- `functions`: (none)
- `Plugins`: list, call_entry, call_event, call, require

### `plugin/sdk/responses.py`
- `functions`: ok, fail, is_envelope
- `classes`: (none)

### `plugin/sdk/router.py`
- `functions`: (none)
- `PluginRouterError`: not_bound, already_bound, dependency_missing, prefix_change_after_bound
- `PluginRouter`: prefix, tags, name, is_bound, entry_ids, ctx, config, plugins, logger, file_logger, store, db, plugin_id, main_plugin, get_plugin_attr, has_plugin_attr, get_dependency, push_message, report_status, collect_entries, add_entry, remove_entry, on_mount, on_unmount

### `plugin/sdk/state.py`
- `functions`: (none)
- `PluginStatePersistence`: collect_attrs, restore_attrs, save, load, clear, has_saved_state, get_state_info

### `plugin/sdk/store.py`
- `functions`: (none)
- `PluginStore`: get, set, delete, exists, keys, clear, count, dump, close

### `plugin/sdk/system_info.py`
- `functions`: (none)
- `SystemInfo`: get_system_config, get_server_settings, get_python_env

### `plugin/sdk/types.py`
- `functions`: (none)
- `PluginContextProtocol`: run_id, require_run_id, get_own_config, get_own_base_config, get_own_profiles_state, get_own_profile_config, get_own_effective_config, update_own_config, get_system_config, trigger_plugin_event, query_plugins, update_status, export_push_text, export_push_text_async, export_push_text_sync, export_push_binary, export_push_binary_async, export_push_binary_sync, export_push_binary_url, export_push_binary_url_async, export_push_binary_url_sync, export_push_url, export_push_url_async, export_push_url_sync, run_update, run_update_async, run_update_sync, run_progress, run_progress_async, run_progress_sync, push_message, query_memory, bus
