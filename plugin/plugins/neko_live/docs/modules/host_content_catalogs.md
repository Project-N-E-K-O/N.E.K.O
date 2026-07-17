# Host Content Catalogs

## Purpose

The host content catalogs provide deterministic idle-hosting beats for quiet live rooms. They cover small choices, callbacks, teasing prompts, micro-challenges, and mood checks without claiming that a viewer or trend exists.

## Ownership And Contracts

- `core/live_content_host_catalog.py` owns catalog loading, validation, ordering, and the built-in fallback aggregate.
- `core/live_content_host_catalog_*.py` own the small thematic fallback pools.
- `core/live_content_host_materials.py` returns fresh dictionaries so hosting state cannot mutate static catalog entries.
- `core/live_content_materials.py` remains the shared compatibility facade for active and host content.
- `data/idle_hosting_beats.json` is the primary editable beat catalog; the Python pools are used when that file is missing or invalid.
- `data/meme_knowledge.json` supplies static public reference material to the existing meme-knowledge loader.

Callers receive dictionaries with stable keys and the fields required by the hosting picker: `live_column`, `shape`, `fun_axis`, `title`, `hint`, and `reply_affordance`. Optional `idle_stage` and `meme_query` fields refine selection without changing the contract.

## Pipeline, Safety, And Data

The hosting director selects a beat before building an `InteractionRequest`. Generated output still enters `core/pipeline.py`, passes `core/safety_guard.py`, and reaches NEKO only through `adapters/neko_dispatcher.py`.

The catalogs read plugin-owned JSON and static public strings. They do not read credentials, raw live payloads, viewer profiles, or stores, and they do not emit output or persist runtime state.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_active_topic_core.py plugin/plugins/neko_live/tests/test_live_hosting_flow.py -q
uv run pytest plugin/plugins/neko_live/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
```

Tests cover shared-facade availability, catalog key integrity, malformed-material rejection, and empty-runtime degradation.

## Limitations And Rollback

- Catalog beats are fallback prompts, not observations about current viewers or room state.
- A malformed entry, duplicate key, or catalog missing a required stage, shape, or fun axis invalidates the external JSON catalog so the complete built-in ordered pool is used instead of a partial catalog.
- Removing this slice restores the prior behavior where host material access returns an empty list. Hosting then skips idle beats while active-content fallback and the rest of the pipeline remain available.
