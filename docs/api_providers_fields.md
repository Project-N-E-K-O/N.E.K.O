# API Provider Configuration Field Reference

This page documents the schema consumed by the current repository. It intentionally does not freeze provider counts, model IDs, pricing, cache claims, or benchmark figures.

## Sources of truth

- `config/api_providers.json`: bundled provider data and feature catalogs
- `utils/api_config_loader.py`: profile conversion and feature accessors
- `config/api_profiles.py`: code fallbacks
- `utils/config_manager/core_config.py`: user selection and role resolution
- `main_routers/config_router/`: settings payloads, connectivity, and writes

## Top-level sections

| Section | Consumer |
| --- | --- |
| `core_api_providers` | Core loader and settings UI |
| `assist_api_providers` | Assist loader and settings UI |
| `keybook_api_providers` | Keybook/TTS/provider settings UI |
| `assist_api_key_fields` | Provider-key to runtime credential mapping |
| `api_key_registry` | Credential metadata and connectivity/UI handling |
| `default_models` | Model defaults read by feature accessors |
| `native_tts_voice_providers`, `free_voices` | Voice registry/configuration |
| `livestream_config` | Livestream endpoint/voice behavior |
| `meme_moderation_config` | Meme moderation |

Keys prefixed with `_comment_` are explanatory data.

## Core provider object

Common display metadata includes `key`, `name`, `description`, and feature flags such as `is_free_version`. The core loader converts only:

| JSON | Runtime |
| --- | --- |
| `core_url` | `CORE_URL` |
| `core_urls` | `CORE_URLS` |
| `core_model` | `CORE_MODEL` |
| `core_api_key` | `CORE_API_KEY` |

## Assist provider object

| JSON | Runtime |
| --- | --- |
| `openrouter_url`, `openrouter_urls` | `OPENROUTER_URL`, `OPENROUTER_URLS` |
| `token_plan_openrouter_url(s)` | MiMo token-plan URL field(s) |
| `conversation_model` | `CONVERSATION_MODEL` |
| `summary_model` | `SUMMARY_MODEL` |
| `correction_model` | `CORRECTION_MODEL` |
| `emotion_model` | `EMOTION_MODEL` |
| `vision_model` | `VISION_MODEL` |
| `agent_model` | `AGENT_MODEL` |
| `audio_api_key` | `AUDIO_API_KEY` |
| `openrouter_api_key` | `OPENROUTER_API_KEY` |
| `provider_type` | `PROVIDER_TYPE` |

Unknown fields are not automatically promoted into runtime config. Frontend core/assist lists expose only `key`, `name`, and `description`.

## Credentials and feature catalogs

`assist_api_key_fields` maps an assist-provider key to the uppercase field read by `core_config.py`. `api_key_registry` carries metadata used by settings/connectivity code. New credential fields require writable JSON handling, router/UI support, redaction, and tests.

Native TTS, keybook, voice, livestream, and moderation sections have feature-specific schemas. They may use inheritance, aliases, catalogs, visibility/default fields, or standalone local override files. Do not treat those as core/assist loader fields.

## Fallback behavior

- A non-empty core JSON set is converted as the core profile set; an empty/malformed source falls back to code defaults.
- Assist JSON profiles merge over same-key code defaults, then code-only defaults are appended.
- `config/livestream_config.json` and `config/meme_moderation_config.json`, when present, take precedence for their owning features.

## Review checklist

1. Keep identifiers stable and cross-references consistent.
2. Never add secrets to bundled JSON.
3. Verify candidate-URL and selected-provider behavior.
4. Update all eight locale files for visible labels.
5. Run targeted provider/config/connectivity tests and `uv run pytest` as appropriate.
6. Build the site with `npm ci && npm run build` in `docs/`.
