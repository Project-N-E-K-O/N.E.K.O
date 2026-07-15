# Viewer And Security Stores

## Purpose

This store slice keeps viewer profiles, audit events, and encrypted platform credentials behind explicit single-writer boundaries. It adds safe profile projections and maintenance actions, bounded audit records with secret redaction, and isolated credential namespaces for multiple live providers.

## Ownership And Contracts

- `stores/viewer_store.py` is the only writer for `viewer_profiles.json`. It sanitizes loaded records, serializes writes with an async lock, uses atomic replacement, and exposes recent, reset, delete, and clear operations whose results report whether persistence was applied.
- `stores/audit_store.py` is the only audit-event sink. It bounds text, nesting, and list sizes and redacts sensitive text and structured sensitive keys.
- `stores/credential_store.py` owns Fernet-encrypted credential files. The default `bili` namespace preserves legacy filenames while other providers use strictly validated, isolated filenames and field allowlists. Audit identity uses `DedeUserID` for Bili fields and `uid` for provider field sets that expose it; missing identities are recorded explicitly as `unidentified`, never as an empty account id.
- `core/viewer_preferences.py` owns preference inference and the public profile projection returned by `recent_profiles()`.

Store callers receive plain public dictionaries or contract objects. Viewer mutations require a sanitized uid. Credential callers only receive the configured encrypted payload fields.

## Pipeline, Safety, And Data

Viewer data is derived from compact identity and interaction metadata after normal live-event handling. Store operations do not emit NEKO output. Any response using profile guidance still enters `core/pipeline.py`, passes `core/safety_guard.py`, and reaches NEKO only through `adapters/neko_dispatcher.py`.

Viewer profiles contain local nicknames, avatar URLs, counters, compact preference summaries, and recent output summaries. Audit records must never retain cookies, tokens, authorization values, or provider credentials. Credential plaintext exists only in memory during encrypted save/load and must never enter audit, logs, config, or UI.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_viewer_store.py plugin/plugins/neko_roast/tests/test_credential_store.py plugin/plugins/neko_roast/tests/test_audit_store.py -q
uv run pytest plugin/plugins/neko_roast/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

Tests cover JSON persistence and fallback, profile reset/delete/clear behavior, encrypted namespace isolation, and redaction of secrets in text and nested structured audit detail.

## Limitations And Rollback

- Viewer profiles are local plugin data and do not provide cross-device synchronization.
- Ordinary profile updates degrade to the plugin data directory when a configured viewer directory cannot be written. Destructive maintenance actions report `applied: false` instead of claiming fallback-only persistence while a stale configured source remains authoritative.
- Missing or undecryptable credential files degrade to a logged-out state.
- Rolling back the maintenance APIs leaves existing profile JSON compatible. Rolling back provider namespaces must retain the default `bili_credential.*` files; other namespace files can remain unused without exposing plaintext.
