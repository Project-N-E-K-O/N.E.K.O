# Plugin Checks and Tests

Use this map before inventing debugging commands. Prefer plugin-facing checks first; use platform tests as read context or targeted regression tests.

## Plugin-Facing Checks

### Readiness check

```bash
uv run neko-plugin check <plugin_id|plugin_path>
```

Use first for load/manifest/entry/decorator/dependency issues. The argument may be a plugin directory name under `plugin/plugins/` or an explicit path to a directory containing `plugin.toml`. It calls the internal `validate_plugin_dir()` helper and reports actionable errors/warnings.

Useful variants:

```bash
uv run neko-plugin check <plugin_id|plugin_path> --strict
uv run neko-plugin check <plugin_id|plugin_path> --release
uv run neko-plugin check <plugin_id|plugin_path> --release --skip-tests
```

What `check` validates:

- `plugin.toml` can be read as TOML.
- `[plugin]` exists and required fields are valid.
- recognized manifest fields only; unknown fields warn.
- `id`, `name`, `version`, `entry`, `type`, `host`, `sdk`, `store`, `i18n`, `safety`, `config_profiles`, dependencies, `plugin_runtime`, `plugin_state`, and adapter mode.
- folder/id mismatch.
- entry module path, entry file existence, entry class existence.
- entry class has `@neko_plugin`.
- entry class base matches the runtime shape (`NekoPluginBase` or `NekoAdapterPlugin`) as a warning.
- startup/shutdown lifecycle hooks as warnings.
- Python AST syntax for plugin `.py` files outside `vendor/`, venv, and caches.
- best-effort static warnings for active `push_message` v1 fields, including legacy positional slots; the warning identifies the file, line, v0.9 removal target, and replacement.
- a v1 warning is diagnostic evidence, not permission to bulk-edit plugin sources or insert TODO-only comments. Report or track it first; change a plugin call only when that plugin is explicitly in scope, because marker-only edits commonly conflict with maintainer work.
- best-effort `@plugin_entry` AST checks:
  - entry id from literal `id` or the function name; invalid characters warn, empty ids error.
  - duplicate `@plugin_entry` ids warn across scanned plugin files.
  - literal `input_schema` gets shallow shape checks.
  - mutually exclusive keyword groups are checked by keyword presence: `input_schema`/`params`, and `llm_result_fields`/`llm_result_model`/`fields`.
- UI surface entries exist when declared.
- UI permission strings are recognized.
- `requirements.txt` is rejected for packages.
- `pyproject.toml [project].dependencies` with external packages requires `vendor/`.
- optional support files: `README.md`, `tests/test_smoke.py`, `.vscode/*`, `.github/workflows/verify.yml`, `.gitignore`.

`--strict` turns missing support files into errors. `--release` runs strict validation, plugin-local tests, package build, package inspection, and payload hash verification.

Known blind spots:

- `check` can discover decorated entries, but runtime-triggered `@plugin_entry` handlers must still be `async def`; add or run a trigger test when changing entries.
- `check` does not cover the full decorator surface. Do not infer coverage for lifecycle, timer, message, custom event, hook, UI, or adapter decorators from the `@plugin_entry` checks.
- `input_schema` shape is checked only when it is a literal dict, and the check is shallow; runtime parameter validation requires `params` or an inferred single model parameter.
- Mutual-exclusion checks are static keyword-presence checks, not runtime value checks.
- `push_message` v1 scanning is best-effort: it checks direct `.push_message(...)` calls and legacy positional slots, skips literal inactive values, and cannot prove behavior hidden behind aliases, `**kwargs`, or dynamic forwarding; an unrelated object with the same method name can still warn.
- `auto_start=false` does not prevent parent-process import/static scanning; top-level side effects need code review or an import smoke test.
- UI action permissions and action exposure require hosted UI/runtime checks, not only manifest validation.
- `passive=true` is discovery/dispatch metadata, not a runtime disable switch.

### Dependency management

Use the CLI for plugin runtime dependencies:

```bash
uv run neko-plugin add <plugin_id> 'httpx>=0.27'
uv run neko-plugin sync <plugin_id> --clean
```

Do not add `requirements.txt`. Python runtime packages belong in plugin-local `pyproject.toml [project].dependencies` and must be vendored into `vendor/`.

### Packaging checks

```bash
uv run neko-plugin build <plugin_id>
uv run neko-plugin inspect <package.neko-plugin>
uv run neko-plugin verify <package.neko-plugin>
```

Notes:

- `build` accepts plugin names under the default plugin root or explicit plugin paths.
- `build` has no `--plugins-root` option.
- single-plugin `build` only supports `[plugin].type = "plugin"`.
- build validates source dependency layout and payload dependency layout.
- `inspect` validates package layout, plugin payload layout, dependency layout, package type, and payload hash metadata.
- `verify` is the focused payload-hash check.

Use `install` only with explicit temporary roots unless the user asked to install:

```bash
uv run neko-plugin install <package.neko-plugin> --plugins-root /tmp/plugins --profiles-root /tmp/profiles --on-conflict fail
```

### Bundle analysis

```bash
uv run neko-plugin analyze <plugin_id> <other_plugin_id>
```

Use for bundle candidates. It reports plugin count, ids, SDK version overlap, and shared dependencies.

## UI Surface Checks

First identify the surface mode. Surface kind (`panel`, `guide`, `docs`) is placement; mode (`hosted-tsx`, `markdown`, `static`) is rendering.

Mode policy:

- Prefer Hosted TSX for new interactive plugin UI.
- Use Markdown for read-only guides/docs.
- Treat Static as legacy; use it only for existing standalone pages or cases Hosted TSX cannot support.

For Hosted TSX (`.tsx`/`.jsx`, or `mode = "hosted-tsx"`), prefer the targeted frontend check:

```bash
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/<plugin_id>
```

Use runtime hosted UI checks when the surface uses state, actions, permissions, i18n, or the hosted UI runtime:

```bash
cd frontend/plugin-manager
npm run test:hosted
npm run test:hosted:e2e
```

For Markdown (`.md`/`.mdx`, or `mode = "markdown"`), `neko-plugin check` verifies the entry path exists, but it does not render the Markdown. Review the document manually when layout, links, or localized docs matter.

For Static UI (`.html`/`.htm`, or `mode = "static"`), `neko-plugin check` verifies the entry path exists, but it does not validate iframe behavior, scripts, asset paths, or postMessage contracts. Use browser/runtime checks when the static page is user-facing.

Full hosted UI gate currently targets MCP Adapter fixtures:

```bash
scripts/check-hosted-ui.sh
```

Use it when touching hosted UI runtime or MCP Adapter surfaces. For another plugin, use the mode-specific checks above first.

## Plugin Test Suite

Plugin tests use the project uv environment.

Whole plugin subsystem:

```bash
uv run pytest plugin/tests -q
```

Plugin unit + integration using the plugin pytest config:

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/unit plugin/tests/integration -q
```

E2E is opt-in:

```bash
uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q
PLUGIN_E2E_BASE_URL=http://127.0.0.1:48911/ui uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q
```

Generated plugin-local tests:

```bash
cd plugin/plugins/<plugin_id>
uv run python -m pytest tests -q
```

`neko-plugin check <plugin_id|plugin_path> --release` also runs plugin-local `tests/` when present.

## Targeted Test Files

Search before editing platform code:

- CLI package workflow: `plugin/tests/integration/test_neko_plugin_cli_workflow.py`
- repo plugin packaging: `plugin/tests/integration/test_neko_plugin_cli_repo_plugins.py`
- CLI command contracts: `plugin/tests/unit/test_neko_plugin_cli_cli.py`, `plugin/tests/unit/test_neko_plugin_cli_deps.py`, `plugin/tests/unit/test_neko_plugin_cli_public.py`
- lifecycle/load/start failures: `plugin/tests/unit/server/test_plugins_lifecycle_service.py`
- registry/load behavior: `plugin/tests/unit/server/test_plugin_registry_service.py`
- trigger execution: `plugin/tests/unit/server/test_trigger_service.py`
- entry runtime kwargs/timeouts: `plugin/tests/unit/core/`, `plugin/tests/unit/sdk/`
- manifest/config edits: `plugin/tests/unit/server/test_config_updates.py`, `plugin/tests/unit/server/test_config_validation.py`
- UI manifest/query behavior: `plugin/tests/unit/server/test_plugin_ui_manifest.py`, `plugin/tests/unit/server/test_plugin_ui_query_service.py`
- SDK/decorator/router surfaces: `plugin/tests/unit/core/`, `plugin/tests/unit/sdk/`
- plugin-specific logic: `plugin/tests/unit/plugins/test_<plugin_name>*.py`

## Repo-Level Static Checks

These are not the first-line plugin-authoring checks, but they may catch plugin code issues when paths are passed explicitly:

```bash
uv run ruff check plugin/plugins/<plugin_id>
uv run python scripts/check_async_blocking.py plugin/plugins/<plugin_id>
```

Many repository convention scripts intentionally exclude `plugin/plugins` by default because plugin payloads may be third-party. Run them against a plugin only when the rule is relevant to first-party plugin code.

## Plugin-Facing vs Platform-Facing

Plugin-facing by default:

- `uv run neko-plugin check <plugin_id|plugin_path>`
- `uv run neko-plugin check <plugin_id|plugin_path> --release`
- `uv run neko-plugin add/sync <plugin_id> ...`
- `uv run neko-plugin build/inspect/verify ...`
- plugin-local tests under `plugin/plugins/<plugin_id>/tests`
- targeted hosted TSX checks for `plugin/plugins/<plugin_id>`
- targeted `plugin/tests/unit/plugins/test_<plugin_name>*.py`

Platform-facing by default:

- editing `plugin/neko_plugin_cli/**`
- editing `plugin/config/**`
- editing `plugin/server/**`
- editing `plugin/sdk/shared/**`
- broad plugin subsystem tests after platform changes
- hosted UI runtime tests after frontend runtime changes

Use platform-facing tests as evidence, but do not modify platform code without escalation.
