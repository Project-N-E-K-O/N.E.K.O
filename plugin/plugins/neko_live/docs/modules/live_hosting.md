# Live Hosting Flow

## Purpose

The live hosting flow selects and builds short solo-stream warmup, idle-hosting, and active-engagement beats without asking the human operator to rescue the room.

## Ownership And Contracts

- `core/live_hosting_director.py` is the runtime-facing facade.
- `core/live_hosting_gates.py` decides whether automatic hosting is eligible.
- `core/live_hosting_beat_picker.py`, `core/live_hosting_beat_state.py`, and `core/live_hosting_beat_rules.py` select non-repeating safe material.
- `core/live_material_rules.py` owns the shared safety and title-similarity rules used by hosting. Active-topic and live-content catalogs integrate through the current plugin-owned content interfaces rather than becoming hard dependencies of the hosting director.
- `modules/warmup_hosting/module.py` and `modules/active_engagement/module.py` build `InteractionRequest` objects. Idle hosting continues through the avatar-roast host path.

## Data Flow And Safety

The runtime director creates a synthetic public `ViewerEvent` only after live-mode, cooldown, queue, recent-interaction, and safety gates pass. The event enters the normal `core/pipeline.py` path, uses `core/safety_guard.py`, and reaches NEKO only through `adapters/neko_dispatcher.py`.

Hosting material is read from plugin-owned live content and recent plugin runtime state. This slice does not write viewer profiles, credentials, or long-term memory. Beat selection state is in-memory and is cleared with the live runtime.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_live/tests/test_live_hosting_flow.py -q
uv run pytest plugin/plugins/neko_live/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
```

The focused tests cover standalone module imports, material safety filtering, and recent-title similarity.

## Limitations And Rollback

- Hosting is intentionally low-frequency and may skip a beat when safety or queue pressure is uncertain.
- Topic discovery and broader active-topic catalogs are available through the current content interfaces, but hosting must still tolerate an unavailable, empty, or filtered source.
- If `live_content` cannot provide safe material, idle-hosting discovery degrades to an empty candidate list instead of blocking the live runtime.
- Output length remains governed by the prompt/metadata contract described in `output_contract.md`.

To roll back, remove the hosting director delegates and module registrations. The EventBus, pipeline, safety guard, and viewer stores remain unchanged.
