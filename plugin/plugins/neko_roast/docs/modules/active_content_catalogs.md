# Active Content Catalogs

## Purpose

The active content catalogs provide deterministic, plugin-owned fallback topics for solo-stream active engagement when recent live-room or trending material is unavailable.

## Ownership And Contracts

- `core/live_content_active_catalog.py` owns the ordered aggregate and stable candidate keys.
- `core/live_content_active_catalog_choice*.py`, `callback.py`, `tease.py`, `challenge.py`, and `mood.py` own small thematic pools.
- `core/live_content_active_materials.py` returns fresh dictionaries so selector bookkeeping cannot mutate the static catalog.
- `core/live_content_materials.py` is the compatibility facade shared with the later host-content slice.
- `core/live_content.py` is the public accessor used by active-topic and hosting runtime delegates.

Every active candidate supplies a stable `key`, `title`, and `hint`, plus shape, axis, column, and reply-affordance metadata consumed by the active-topic material helpers.

## Pipeline, Safety, And Data

Catalog entries are selected by the active-topic selector before `modules/active_engagement` builds an `InteractionRequest`. Output still enters `core/pipeline.py`, passes `core/safety_guard.py`, and reaches NEKO only through `adapters/neko_dispatcher.py`.

The catalogs contain static public prompt material. They do not read live payloads, credentials, viewer profiles, or stores, and they do not emit output or persist state.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_active_topic_core.py -q
uv run pytest plugin/plugins/neko_roast/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

Focused tests cover standalone import, non-empty active fallback material, missing host-catalog degradation, fallback validation, and selector refresh behavior.

## Limitations And Degrade Behavior

- These are fallback hooks, not claims about current viewers, trends, or room state.
- Before the later host-content slice lands, `idle_hosting_beat_candidates()` safely returns an empty list while active fallback topics remain available.
- If this slice is removed, the active-topic core falls back to its single conservative default candidate; the pipeline, safety guard, dispatcher, and stores remain unchanged.
