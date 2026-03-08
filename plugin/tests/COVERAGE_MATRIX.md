# Plugin Test Coverage Matrix

This matrix tracks `plugin/` test scope for SDK + server config scenarios.

## Layers

- `unit`: deterministic logic contracts (validation, merge, serialization, decorators/router behaviors)
- `integration`: FastAPI route behavior via in-process ASGI (`httpx` transport)
- `e2e`: browser smoke/regression via Playwright (opt-in)

## Covered Now

- `server.requests.common`
  - timeout coercion defaults, finite checks, max clamp
- `server.infrastructure.config_profiles`
  - profile path under base dir
  - path traversal rejection (`../`)
  - active profile application
  - env override profile selection
  - top-level `[plugin]` rejection in overlay profile
- `server.infrastructure.config_profiles_write`
  - deleting active profile clears `active` key (TOML-safe)
- `server.application.config.validation`
  - forbidden protected fields (`plugin.id`, `plugin.entry`)
  - plugin/author/sdk/dependency shape validation
  - email + sdk conflicts validation branches
- `server.application.config.query_service`
  - effective config merge behavior
  - invalid shape rejection (`INVALID_DATA_SHAPE`)
  - overlay `[plugin]` rejection
- `server.routes.health`
  - `/health` happy path
- `server.routes.runs`
  - upload session uses trusted/relative base url (Host header not trusted)
- `sdk.responses`
  - `ok/fail/is_envelope` canonical behavior
- `sdk.decorators`
  - schema inference + params model attachment + worker/persist metadata
- `sdk.router`
  - prefix behavior, bind constraints, dependency injection error path, metadata prefixing

## Next High-Value Additions

- `server.application.config.command_service`
  - HTTPException -> ServerDomainError mapping matrix
  - runtime exception mapping matrix
- `server.infrastructure.config_updates`
  - protected field immutability in replace/update/toml update
- `server.application.plugins.lifecycle_service`
  - plugin_id path safety (`_get_plugin_config_path`)
  - dependency precheck before host start
- `server.application.messages.query_service`
  - `max_count` upper clamp with `MESSAGE_QUEUE_MAX`
  - filtering by plugin + priority + binary serialization
- `server.application.admin.query_service`
  - alive-state derivation + sensitive key redaction
- `sdk.adapter.gateway_core`
  - normalize/policy/router/transport failure envelope contracts

## Execution

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/unit plugin/tests/integration -q
uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q
```
