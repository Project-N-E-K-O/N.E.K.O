# Plugin Tests Detailed Classification

This file is the canonical taxonomy for `plugin/tests`.

## 1) By Test Level

- `unit/`: isolated logic tests, mocked dependencies, no real network/service boot.
- `integration/`: in-process ASGI integration (`httpx` + `ASGITransport`).
- `e2e/`: browser/UI smoke via Playwright (`--run-plugin-e2e` opt-in).

## 2) Current Unit Test Domains

### A. Server Application / Infrastructure

- `test_admin_query_service.py`: admin query service behavior.
- `test_config_command_service.py`: config command service mutation flows.
- `test_config_profiles_apply.py`: config profile apply fallback and overlay.
- `test_config_profiles_security.py`: profile path/boundary security checks.
- `test_config_profiles_write.py`: write/delete profile config behaviors.
- `test_config_query_effective.py`: effective config merge and overlay contract.
- `test_config_updates.py`: config update/replace/toml update boundaries.
- `test_config_validation.py`: config shape and protected-field validation.
- `test_logs_filter.py`: log filter utility behavior.
- `test_messages_query_service.py`: message query serialization/filter behavior.
- `test_metrics_query_service.py`: monitoring query service behavior.
- `test_plugins_lifecycle_service.py`: plugin lifecycle orchestration paths.

### B. Server Messaging / Handler Adapter Layer

- `test_server_request_handlers.py`: IPC handler registry and request handler core paths.
- `test_messaging_handler_common.py`: common normalization/payload helpers for handlers.
- `test_messaging_handlers_additional.py`: bus delete/subscribe + plugin config handler branches.
- `test_request_common.py`: timeout and request common coercion behavior.
- `test_request_router.py`: request router core handling and fallback send path.
- `test_request_router_additional.py`: request router queue/start-stop/zmq import-failure branches.

### C. SDK / Adapter

- `unit/sdk/adapter/test_sdk_adapter_gateway.py`: adapter gateway core/default contracts.
- `unit/sdk/adapter/test_sdk_neko_adapter_plugin.py`: neko adapter plugin behavior.

### D. SDK / Bus

- `unit/sdk/bus/test_sdk_bus_models_and_clients.py`: bus models and client contracts.

### E. SDK / Core Surface

- `unit/sdk/core/test_sdk_base_and_adapter.py`: sdk base/adaptation behavior.
- `unit/sdk/core/test_sdk_call_chain.py`: call chain behavior.
- `unit/sdk/core/test_sdk_decorators.py`: decorators metadata/schema behavior.
- `unit/sdk/core/test_sdk_hook_and_adapter_extras.py`: hook/adapter extra behavior.
- `unit/sdk/core/test_sdk_hook_executor.py`: hook execution contracts.
- `unit/sdk/core/test_sdk_memory_system_state.py`: memory/system state access behavior.
- `unit/sdk/core/test_sdk_message_plane_transport_client.py`: message-plane transport behavior.
- `unit/sdk/core/test_sdk_method_surface_complete.py`: sdk method surface completeness + callable contract checks.
- `unit/sdk/core/test_sdk_plugins_and_config.py`: sdk plugin/config usage paths.
- `unit/sdk/core/test_sdk_public_api_surface.py`: public API surface expectations.
- `unit/sdk/core/test_sdk_responses.py`: standard envelope response behavior.
- `unit/sdk/core/test_sdk_router.py`: sdk router behavior.
- `unit/sdk/core/test_sdk_store_database_logger_transport.py`: store/db/logger/transport behaviors.

## 3) Integration / E2E Classification

### Integration

- `integration/test_health_routes.py`: health endpoint contracts.
- `integration/test_metrics_routes.py`: metrics endpoint contracts.
- `integration/test_runs_upload_route.py`: run upload route behavior.

### E2E

- `e2e/test_plugin_ui_smoke.py`: plugin UI smoke.

## 4) Recommended Target Layout (Next Step)

Keep `test_*.py` naming; split by domain folder under each level:

- `unit/server/application/...`
- `unit/server/messaging/...`
- `unit/server/routes/...`
- `unit/server/infrastructure/...`
- `unit/sdk/adapter/...`
- `unit/sdk/bus/...`
- `unit/sdk/core/...`
- `integration/server/...`
- `e2e/plugin_ui/...`

This preserves pytest discovery while making ownership and review scope obvious.

## 5) File Placement Rules

- If subject module path starts with `plugin.server.application`, place under `unit/server/application/`.
- If subject module path starts with `plugin.server.messaging`, place under `unit/server/messaging/`.
- If a test covers multiple subsystems, place by primary entrypoint and add a header comment.
- SDK tests must remain under `unit/sdk/{adapter,bus,core}` and avoid server fixture coupling.
