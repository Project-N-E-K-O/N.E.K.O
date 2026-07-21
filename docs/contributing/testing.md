# Testing

## Setup

```bash
uv sync
uv run playwright install chromium
```

The root `tests/conftest.py` also attempts to install Chromium when unavailable, but explicit preparation is more predictable. Never commit `tests/api_keys.json`; only create it from the template for tests that genuinely call external services.

## Common commands

```bash
# Whole root suite
uv run pytest -q

# Focused paths/markers
uv run pytest tests/unit -q
uv run pytest tests/frontend -m frontend -q
uv run pytest tests/e2e -m e2e -q

# Manual tests: real APIs/screen/browser, supervised only
uv run pytest -m manual --run-manual -s

# Plugin suite uses its own pytest configuration
uv run pytest plugin/tests -q
```

There is no `--run-e2e` option in current `tests/conftest.py`. The only opt-in CLI flag is `--run-manual`. Performance tests use `RUN_PERF_TESTS=true` at the owning tests.

## Markers

Root `pytest.ini` registers `unit`, `frontend`, `e2e`, `performance`, `plugin_unit`, and `plugin_e2e`. `conftest.py` adds `manual` and skips manual tests unless explicitly enabled.

A directory is not proof of behavior. Inspect fixtures and test code to see whether it starts servers, uses Playwright, needs an external credential, or mutates local UI/OS state.

## CI coverage

- `.github/workflows/plugin-tests.yml`: plugin suite plus selected root contracts on Windows.
- `.github/workflows/analyze.yml`: Ruff and project-specific static contracts.
- `.github/workflows/docs.yml`: `npm ci` and VitePress build.
- Desktop/Docker workflows validate packaging paths separately.

The general GitHub workflows do not amount to a full cross-platform root `pytest` promise. State exactly what was run locally.

## Writing tests

- Put regression tests beside the owning test cohort.
- Use temporary paths and synthetic/private-safe content.
- Avoid real APIs unless the test is explicitly manual/integration-scoped.
- Keep async tests deterministic; do not replace lifecycle synchronization with arbitrary sleeps.
- Add or reuse a marker only when its execution contract is clear.
- For i18n and provider changes, cover all synchronized peers, not one locale/provider.
