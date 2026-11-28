# Plugin Test Framework

This directory contains the dedicated automated testing framework for the `plugin/` subsystem.

## Structure

- `unit/`: pure unit tests, no network/server startup
- `integration/`: FastAPI route/application integration tests via `httpx` + ASGI transport

## Run

Use the project venv with `uv run`.

Run plugin unit + integration:

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/unit plugin/tests/integration -q
```

Run the end-to-end packaging workflow regression only:

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/integration/test_neko_plugin_cli_workflow.py -q
```

CI runs the whole directory (`uv run pytest plugin/tests`): passing the
directory overrides the `testpaths` list in `pytest.ini`, so `unit/core`,
`unit/plugins`, the `unit/` root files and `property/` are collected too.

## Design Notes

- Integration tests use `httpx.AsyncClient` with `ASGITransport`; no external server process is required.
- `integration/test_neko_plugin_cli_workflow.py` uses fixture plugins under `fixtures/neko_plugin_cli/plugins/` to replay realistic pack/inspect/verify/unpack flows without manual testing.
- Admin dependency is overridden in test app fixture to isolate business behavior from auth setup.
- There are no browser E2E tests at present. Tests marked `plugin_e2e` are skipped unless `--run-plugin-e2e` is passed.


See also: `plugin/tests/COVERAGE_MATRIX.md` for SDK/config scenario coverage.
See also: `plugin/tests/TEST_CLASSIFICATION.md` for detailed test taxonomy and placement rules.
