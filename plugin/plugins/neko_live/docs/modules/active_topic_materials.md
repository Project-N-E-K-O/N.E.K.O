# Active Topic Materials

## Purpose

The active-topic material helpers classify safe topic candidates into stable families, prompt profiles, topic packs, and rotating interaction shapes. They keep selection and de-duplication policy separate from live content catalogs.

## Ownership And Contracts

- `core/active_topic_material_family.py` preserves an explicit `family` and otherwise infers a family from material fields.
- `core/active_topic_material_profile.py` maps recognizable titles to shape, axis, column, reply-affordance, and hint metadata.
- `core/active_topic_pack.py` maps explicit packs, families, and live-column markers to output packs.
- `core/active_topic_rotation.py` owns streak and similar-title checks.
- `core/active_topic_shapes.py` owns the interaction-shape order and recent-shape guard.
- `core/active_topic_materials.py` is the compatibility facade used by active-topic rules.

The helpers are pure: they accept material dictionaries, titles, or recent in-memory deques and return classification data. Explicit `family` and `topic_pack` values take precedence over heuristic inference.

## Pipeline, Safety, And Data

The active-topic selector calls these helpers before `modules/active_engagement` builds an `InteractionRequest`. The request still enters `core/pipeline.py`, passes `core/safety_guard.py`, and reaches NEKO only through `adapters/neko_dispatcher.py`.

These modules do not emit output, access credentials, write stores, or persist viewer data. They read plugin-owned material metadata and in-memory recent-selection state only.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_active_topic_core.py -q
uv run pytest plugin/plugins/neko_live/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
```

Focused coverage locks explicit-family precedence, A/B marker boundaries, optional split imports, fallback handling, rotation refresh, mention parsing, and anonymous-speaker guards.

## Limitations And Degrade Behavior

- Heuristic classification is intentionally conservative; unknown titles may return no profile and be skipped by topic sources.
- A literal `A/B` or `A|B` marker is recognized as a choice, while normal words containing `ab` are not.
- Before this materials slice is present, the active-topic core uses small safe defaults from `active_topic_core_fallbacks.py`.
- Removing this slice restores those defaults; the pipeline, safety guard, dispatcher, and stores remain unchanged.
