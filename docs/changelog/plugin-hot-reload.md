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
  the same operation lock; the auto reload waits up to the debounce window for
  the lock, and on `PluginOperationBusy` defers by one debounce window and
  retries.
- Changes must be quiet for `NEKO_PLUGIN_HOT_RELOAD_DEBOUNCE` seconds
  (default 1.5) before the reload fires, so multi-file saves and in-progress
  writes do not reload half-written code. The scan runs every
  `NEKO_PLUGIN_HOT_RELOAD_INTERVAL` seconds (default 1.0).
- Only **running** plugins are reloaded. A plugin the user stopped is never
  started by a file change; it picks up new code on its next manual start.
  Exception: if an automatic reload stopped the plugin and then failed to
  start it, the next source change retries the start. Any explicit
  start/stop/reload, uninstall, package replacement or development-association
  change revokes that retry.
- Automatic reloads never change the plugin's persisted enabled / auto-start
  intent; only the manual **Reload** button does.
- Before stopping a healthy process, the manifest, entry point and plugin
  dependencies are validated and plugin-owned `.py` files are compiled
  (without importing the plugin). A broken edit skips the reload and keeps
  the running instance; the next change retries. Development-mode plugins
  additionally keep their existing full preflight inside `reload_plugin`.
- Lifecycle events `plugin_hot_reload_triggered` / `_skipped` / `_failed` are
  emitted for observability.

## Implementation notes

- `plugin/server/application/plugins/hot_reload_service.py`: stdlib-only
  polling watcher (mtime_ns + size signatures). No new dependencies; works on
  Windows / macOS / Linux alike.
- Started at the end of `ServerLifecycleService.startup()` and stopped at the
  top of `_shutdown_internal()`, before any plugin host is torn down, so an
  auto reload cannot race the shutdown. A shutdown latch additionally rejects
  plugin starts once teardown begins, so an in-flight reload can no longer
  register a host nobody will stop.
- New settings (also exported through the admin API allowlist):
  `PLUGIN_HOT_RELOAD`, `PLUGIN_HOT_RELOAD_INTERVAL`,
  `PLUGIN_HOT_RELOAD_DEBOUNCE`.

## Testing

`plugin/tests/unit/server/test_plugin_hot_reload_service.py` covers: change
detection with debounce, first-scan baselining, syntax-error and
broken-manifest protection, no auto-start of stopped plugins (including the
in-lock recheck), busy retry under real lock contention, the shutdown latch,
idempotent stop, and restart rebaselining.
