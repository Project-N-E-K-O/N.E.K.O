# Plugin CLI and Debugging

This is the first-version debugging index. Prefer existing CLI and tests over ad hoc scripts.

For the complete check/test matrix, read `plugin-checks-and-tests.md`.

## Invocation

Use the project uv environment:

```bash
uv run neko-plugin --help
```

The CLI entrypoint is also exposed as `neko-plugin` by `pyproject.toml`, but repo-local agent work should prefer `uv run neko-plugin ...`.

Some CLI help output may include app initialization logs before the argparse output. Do not treat those logs as failure unless the command exits non-zero or prints `[FAIL]`.

## Standard Plugin Creation

Create standard plugins through the CLI. Do not hand-create the initial directory, `plugin.toml`, or entry class.

```bash
uv run neko-plugin init <plugin_id> --type plugin --name "<Plugin Name>" --no-interactive
```

Useful variants:

```bash
uv run neko-plugin init <plugin_id> --type adapter --name "<Plugin Name>" --no-interactive
```

`--no-interactive` is suitable for normal plugins and adapters. `extension` is rejected and has no compatibility window; migrate it into a normal plugin/router rather than regenerating it.

## CLI Source Map

CLI entrypoints and commands live under:

- `plugin/neko_plugin_cli/__main__.py`
- `plugin/neko_plugin_cli/cli.py`
- `plugin/neko_plugin_cli/commands/`

Public command modules include:

- `init_cmd.py`
- `check_cmd.py`
- `verify_cmd.py`
- `inspect_cmd.py`
- `analyze_cmd.py`
- `deps_cmd.py`
- `build_cmd.py`
- `install_cmd.py`

Registered command families include `init`, `init-repo`, `setup-repo`, `check`, `add`, `sync`, `build`, `inspect`, `verify`, `install`, and `analyze`.

Internal implementation helpers include `release_cmd.py` and `validate_cmd.py`. They are imported by public commands such as `check`, but they are not registered as direct CLI command families.

Before using a command, run CLI help or read the command module enough to confirm arguments.

## Debugging Order

1. Identify `plugin_id`.
2. Inspect `plugin/plugins/<plugin_id>/plugin.toml`.
3. Check Identity Lock and manifest fields.
4. Inspect the entry class and decorators.
5. Run `uv run neko-plugin check <plugin_id|plugin_path>` unless the symptom clearly points elsewhere.
6. Search tests for the failing surface.
7. Run the smallest CLI command, unit test, or trigger path that reproduces the issue.
8. Fix only inside `plugin/plugins/<plugin_id>/` unless escalation is confirmed.

## Useful Source Areas

- Trigger execution: `plugin/server/runs/trigger_service.py`
- Start/stop lifecycle: `plugin/server/application/plugins/lifecycle_service.py`
- Registry refresh: `plugin/server/application/plugins/registry_service.py`
- Config reads/writes: `plugin/server/application/config/`
- Runtime logs: `plugin/server/logs.py`

These are read context by default, not write workspace.

## Tests To Search

For the full test map, read `plugin-checks-and-tests.md`.

- lifecycle/load/start failures: `plugin/tests/unit/server/test_plugins_lifecycle_service.py`
- manifest/config edits: `plugin/tests/unit/server/test_config_updates.py`
- CLI routes and package operations: `plugin/tests/unit/server/test_plugin_cli_route.py`
- plugin source resolver: `plugin/tests/unit/server/test_plugin_cli_source_resolver.py`
- UI manifest behavior: `plugin/tests/unit/server/test_plugin_ui_manifest.py`

Use `rg` to locate examples for the specific entry, CLI command, or error message.
