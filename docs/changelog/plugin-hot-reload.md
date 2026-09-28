# Plugin hot reload on source change

**Current-source status (verified 2026-09-28)**: implemented in the plugin
server; off by default, enabled with `NEKO_PLUGIN_HOT_RELOAD=true`. This is a
capability note, not a promise about a particular release train.

## Summary

The plugin server can now watch plugin source directories and reload the
affected plugin automatically when its code changes, removing the
save → switch to plugin page → click **Reload** loop during development.

```bash
# PowerShell
$env:NEKO_PLUGIN_HOT_RELOAD = "true"; uv run python launcher.py
# bash
NEKO_PLUGIN_HOT_RELOAD=true uv run python launcher.py
```

Watched locations: every registered plugin's config directory under
`PLUGIN_CONFIG_ROOTS` (built-in `plugin/plugins/` and the user installation
root) plus every development-mode registration's `source_dir`. Watched files:
`*.py` and `plugin.toml`.

## Semantics

- A reload is the existing `reload_plugin` transaction (stop + start, i.e. the
  plugin subprocess is replaced). Auto reloads and manual button clicks take
  the same operation lock; on `PluginOperationBusy` the auto reload defers by
  one debounce window and retries.
- Changes must be quiet for `NEKO_PLUGIN_HOT_RELOAD_DEBOUNCE` seconds
  (default 1.5) before the reload fires, so multi-file saves and in-progress
  writes do not reload half-written code. The scan runs every
  `NEKO_PLUGIN_HOT_RELOAD_INTERVAL` seconds (default 1.0).
- Only **running** plugins are reloaded. A plugin the user stopped is never
  started by a file change; it picks up new code on its next manual start.
- Before stopping a healthy process, plugin-owned `.py` files are compiled and
  `plugin.toml` is parsed. A syntactically broken edit skips the reload and
  keeps the running instance; the next change retries. Development-mode
  plugins additionally keep their existing full preflight inside
  `reload_plugin`.
- Lifecycle events `plugin_hot_reload_triggered` / `_skipped` / `_failed` are
  emitted for observability.

## Implementation notes

- `plugin/server/application/plugins/hot_reload_service.py`: stdlib-only
  polling watcher (mtime_ns + size signatures). No new dependencies; works on
  Windows / macOS / Linux alike.
- Started at the end of `ServerLifecycleService.startup()` and stopped at the
  top of `_shutdown_internal()`, before any plugin host is torn down, so an
  auto reload cannot race the shutdown.
- New settings (also exported through the admin API allowlist):
  `PLUGIN_HOT_RELOAD`, `PLUGIN_HOT_RELOAD_INTERVAL`,
  `PLUGIN_HOT_RELOAD_DEBOUNCE`.

## Testing

`plugin/tests/unit/server/test_plugin_hot_reload_service.py` covers: change
detection with debounce, first-scan baselining, syntax-error and
broken-manifest protection, no auto-start of stopped plugins, busy retry,
idempotent stop, and restart rebaselining.
