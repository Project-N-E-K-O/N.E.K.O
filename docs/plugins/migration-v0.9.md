# Plugin SDK migration to v0.9

This historical path name is retained for inbound links. The page is a current migration checklist: some removals are already effective, while `push_message` v1 compatibility parameters still work with deprecation warnings. Do not infer an exact removal release from the filename.

## At a glance

| Previous surface | Status | Replacement |
|---|---|---|
| `type = "script"` / script plugin | Removed, no compatibility path | Use a normal `plugin` package and `NekoPluginBase` |
| `plugin._types.result` | Removed | Import `Result`, `Ok`, `Err`, `SdkError`, and helpers from `plugin.sdk.plugin` |
| Bus `where_*` helpers and list set operators | Removed | Compose `get()`, `filter()` / `where()`, `sort()`, `limit()`, and `watch()` |
| `get_message_plane_all` | Removed | Use bounded `await self.bus.messages.get(...)` queries |
| Bus incremental/local fast paths | Removed | Use the canonical bounded, replayable read/watch pipeline |
| High-level `self.memory` / SDK `MemoryClient` | Removed | Use `self.bus.memory.get(...)` only for recent in-memory user-context records; there is no plugin SDK replacement for persistent semantic recall |
| Extension package type, `[plugin.host]`, and `plugin.sdk.extension` | Removed, no compatibility path | Merge the Router into its owning normal Plugin, or convert the package into a standalone Plugin |
| `push_message` v1 fields | Deprecated but still translated in current source | Use `parts`, `visibility`, and `ai_behavior`; do not rely on an exact removal release |

## Package types

Script plugins have no compatibility shim. Change the manifest and entry class to the standard plugin contract:

```toml
[plugin]
type = "plugin"
```

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):
    @plugin_entry(id="run")
    async def run(self, **_):
        return Ok({"status": "done"})
```

Extension has no compatibility shim. Remove `type = "extension"` and `[plugin.host]`, then either move the Router module into its former host and call `self.include_router(router)`, or turn it into a normal `NekoPluginBase` package. Replace imports from `plugin.sdk.extension` with the corresponding public symbols from `plugin.sdk.plugin`. `PluginRouter` remains supported only for organizing code inside a normal Plugin.

## Result imports

There is one public Result stack. Replace internal or legacy imports:

```python
# Before
from plugin._types.result import Result, Ok, Err

# After
from plugin.sdk.plugin import Result, Ok, Err, SdkError
```

Do not add a local compatibility alias for the removed module.

## Bus queries and watchers

The bus is a read/watch facade over host state, not a publish/subscribe event bus. Query one namespace and keep the result bounded:

```python
events = await self.bus.events.get(plugin_id=self.plugin_id, max_count=50)
events = (
    events
    .filter(priority_min=1)
    .filter(type="TASK_FINISHED")
    .sort(by="timestamp", reverse=True)
    .limit(20)
)

watcher = events.watch(self.ctx)

@watcher.subscribe(on="add")  # only "add", "del", or "change"
def on_added(delta):
    for event in delta.added:
        self.logger.info(f"event: {event.type}")

watcher.start()
```

Callable `filter(predicate)`, `where(predicate)`, and `sort(key=callable)` remain available for local snapshot processing, but they are not replayable. Do not put them before `watch()`; use structured `filter(field=value, ...)` and `sort(by=...)` in watcher chains. Only `messages`, `events`, and `lifecycle` support `watch()`; `conversations` and `memory` are read-only snapshots.

Removed helpers include `where_in`, `where_eq`, `where_contains`, `where_regex`, `where_gt`, `where_ge`, `where_lt`, and `where_le`, together with BusList intersection/difference operators. Express those rules in `filter(...)` or `where(predicate)`; if two snapshots must be combined, do it explicitly with record keys in ordinary Python.

`get_message_plane_all` paged through the Message Plane `messages` store up to its `max_items` bound. It has no direct replacement because the incremental `after_seq` transport path was removed. Use bounded `await self.bus.messages.get(max_count=..., ...)` queries, then apply structured filters, `sort(by=...)`, and `limit()` as needed.

The removed bus fast paths were acceleration branches such as BusList `fast_mode`, incremental reload cursors, the local message cache, and revision/delta shortcuts. Replay plans and traces remain because `watch()` needs them; `get()` / structured `filter(field=value)` / `sort(by=...)` / `limit()` form the replayable chain. These paths are not the same as the legacy `push_message(fast_mode=...)` flag. That push flag belongs to the deprecated v1 compatibility surface; v2 uses the standard per-message host delivery path, so benchmark high-volume producers when removing the old batching/backpressure optimization.

## Memory

The old SDK `MemoryClient` mixed two different concepts. The supported replacement is the host's recent user-context snapshot:

```python
# Recent records from one bucket
records = await self.bus.memory.get(bucket_id="default", limit=20)
```

These records are bounded, in-memory user-utterance events with a one-hour TTL. They are not the character's persistent facts, reflections, or persona. Memory record reads and typed records remain available through `self.bus.memory`; the removed surfaces are the high-level `self.memory` property and the SDK/runtime `MemoryClient` facade. `ctx.query_memory(...)` still exists for compatibility, but it reaches a deprecated placeholder endpoint and does not provide semantic recall. There is currently no public plugin SDK operation for structured persistent-memory recall.

## `push_message` v2

Use only the canonical schema in new code:

```python
self.push_message(
    source="my_plugin",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "Task complete"}],
)
```

If a call cannot be migrated in the same change, avoid bulk-inserting comments into active plugin sources: that often conflicts with plugin-maintainer work. Track the warning in an issue or PR first. In a plugin source you own, a short local marker can use this form:

```python
# TODO(plugin-api-v0.9): replace push_message v1 fields before v0.9; tracked in <issue-or-PR>.
```

The v1 fields `message_type`, `description`, `content`, `binary_data`, `binary_url`, `mime`, `delivery`, `reply`, `unsafe`, and `fast_mode` are compatibility-only. Static checks and runtime warnings identify these calls; migrate them before v0.9. See the [full `push_message` v2 mapping](/changelog/plugin-push-message-v2).

## Verification

Run the plugin checker after migrating:

```bash
uv run neko-plugin check <plugin_id-or-path> --strict
```

Treat every legacy `push_message` warning as a required migration, not as a suppression candidate.
