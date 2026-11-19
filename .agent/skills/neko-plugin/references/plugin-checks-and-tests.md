# Plugin Checks and Tests

What each check covers, what it misses, and how to test a plugin. Command
syntax and order are in `plugin-cli-and-debugging.md`.

## What `check` Validates

Source: [validate_cmd.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/neko_plugin_cli/commands/validate_cmd.py).

- `plugin.toml` parses; `[plugin]` and its required fields are valid; unknown
  fields warn; `id`, `name`, `version`, `entry`, `type`, `sdk`, `store`,
  `i18n`, `safety`, `config_profiles`, dependencies, `plugin_runtime`,
  `plugin_state`, and adapter mode are checked.
- Folder/id mismatch (for plugins in the checkout's built-in plugin folder).
- The entry module and class exist, the class has `@neko_plugin`, and its base
  matches the type (`NekoPluginBase`, `NekoAdapterPlugin`; a warning).
- Other decorators, from the source text: `@lifecycle` ids must be one of
  `startup`, `shutdown`, `reload`, `freeze`, `unfreeze`, `config_change`;
  `@timer_interval` needs an `id` and a literal integer `seconds > 0`;
  `@message` needs an `id`.
- Python syntax of the plugin's `.py` files outside `vendor/`, virtualenvs,
  and caches.
- Best-effort `@plugin_entry` checks from the source text: entry ids
  (invalid characters warn, empty ids error), duplicate ids, shallow shape of
  literal `input_schema`/`output_schema` (an explicit `output_schema` also
  warns that it is published only), and the mutually exclusive keyword groups
  (`input_schema`/`params`; `llm_result_fields`/`llm_result_model`/`fields`).
- Deprecated `push_message` v1 keywords.
- Declared UI surface entries exist; UI permission strings are known.
- `requirements.txt` is rejected; external dependencies in `pyproject.toml`
  require a `vendor/` that satisfies them.
- Optional support files (`README.md`, `tests/test_smoke.py`, `.vscode/*`,
  `.github/workflows/verify.yml`, `.gitignore`); errors with `--strict`.

`check -r` adds the strict mode, the plugin's tests, the package build, and
hash verification; Market-specific rules are in `dependencies-and-market.md`.

## Blind Spots

- Entries must be `async def` at call time; `check` finds decorated entries
  but does not run them. Run a test that calls the entry.
- Beyond the ids and literals above, decorators are not analysed: hooks, UI
  decorators, adapter decorators, and handler bodies get no checks.
- `input_schema` is checked only as a literal dict, shallowly; runtime input
  validation needs `params=Model`.
- Mutual exclusion is checked by keyword presence, not by values.
- Top-level side effects run on import even with `auto_start = false`; code
  review or an import test catches them.
- Hosted TSX is not type-checked or rendered, Markdown is not rendered, and
  static pages, action permissions, and action exposure need the running UI
  (`hosted-ui-authoring.md`).
- `passive = true` is dispatch metadata, not an off switch.

## Plugin Tests

Tests live in `<plugin_dir>/tests/` (the scaffold creates
`tests/test_smoke.py`). `check -r` and `precheck` run them with pytest in
N.E.K.O's own environment, with `plugin.plugins.<plugin_id>` importable even
when the plugin lives outside the checkout. Running `pytest` directly in a
standalone plugin folder leaves N.E.K.O off the import path, and `uv run
pytest` there creates a fresh environment without pytest; use `check -r`
instead.

Write tests that need no running host: keep entry logic in plain helper
functions and test those (`from plugin.plugins.<plugin_id>.client import
parse_reply`); the plugin class needs a host context that tests do not have.
Never use real user data, real API keys, or live services; use temporary
directories and synthetic payloads.

## UI Checks

Mode first: kind (`panel`, `guide`, `docs`) is placement, mode
(`hosted-tsx`, `markdown`, `static`) is rendering. `check` only verifies that
the entry file exists. Hosted TSX has a dedicated checker that needs a source
checkout (`hosted-ui-authoring.md`, Validation); otherwise open the surface
after Start/Reload and read its warnings.
