# Config Integration Matrix (ID Conflict / Ordering)

## Scope
- `plugin.server.infrastructure.config_updates`
- `plugin.server.infrastructure.config_profiles`
- `plugin.server.infrastructure.config_profiles_write`
- `plugin.server.application.config.query_service`

## ID Conflict Cases
1. Protected ID mutation
- Step: call update/replace with `plugin.id != current plugin.id`.
- Expected: reject with `400`; config file unchanged.

2. Entry mutation
- Step: update `plugin.entry` to a different value.
- Expected: reject with `400`; config file unchanged.

3. Profile overlay injects `[plugin]`
- Step: activate profile containing top-level `[plugin]`.
- Expected: reject with `400`.

4. Concurrent profile writes to same profile id
- Step: parallel `upsert_profile_config` on same `profile_name`.
- Expected: serialized by lock; final file not corrupted (valid TOML).

## Ordering / Sorting Cases
1. Deterministic effective config
- Step: base config keys + overlay keys in different insertion orders.
- Expected: merged semantic result identical (dict equality).

2. Numeric-like profile IDs
- Step: active profile `"01"` and `files = {"1": "...", "01": "..."}`.
- Expected: path resolution behavior consistent with current fallback rule.

3. Profiles state listing
- Step: multiple `files` entries, mixed existing/missing paths.
- Expected: each entry has stable `path/resolved_path/exists`; no nondeterministic omission.

4. Repeated update sequence
- Step: `replace -> update -> update_toml -> read`.
- Expected: read result equals last write semantics; no stale ordering artifacts.

## Recommended Integration Test Setup
1. Use temporary plugin workspace (`tmp_path`) with real files.
2. Use API layer (route/service) instead of mocking internals.
3. Run conflict tests under concurrent execution (`asyncio.gather` or thread pool).
4. Add snapshot checks for effective payload after each step.
