# Plugin Test Framework

This directory contains the dedicated automated testing framework for the `plugin/` subsystem.

## Structure

- `unit/`: pure unit tests, no network/server startup
- `integration/`: FastAPI route/application integration tests via `httpx` + ASGI transport
- `e2e/`: browser E2E smoke/regression tests via Playwright (opt-in)

## Run

Use the project venv with `uv run`.

Run plugin unit + integration:

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/unit plugin/tests/integration -q
```

Run plugin e2e (opt-in):

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q
```

If running e2e, provide target URL:

```bash
PLUGIN_E2E_BASE_URL=http://127.0.0.1:48911/ui uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q
```

## Design Notes

- Integration tests use `httpx.AsyncClient` with `ASGITransport`; no external server process is required.
- Admin dependency is overridden in test app fixture to isolate business behavior from auth setup.
- E2E tests are gated by `--run-plugin-e2e` to keep CI stable and fast by default.


See also: `plugin/tests/COVERAGE_MATRIX.md` for SDK/config scenario coverage.
See also: `plugin/tests/SDK_METHOD_COVERAGE.md` for full SDK public method inventory and surface checks.
See also: `plugin/tests/TEST_CLASSIFICATION.md` for detailed test taxonomy and placement rules.
