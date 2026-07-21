# API Providers

Provider behavior is data-driven. Shipped definitions live in `config/api_providers.json`; Python fallbacks live in `config/api_profiles.py`. The Web UI obtains provider labels from the loader.

Do not depend on a provider count or model-ID snapshot in documentation. Both change independently of this page.

## Core and assist profiles

- **Core** drives the primary conversation/realtime path. Loader fields are `core_url` or `core_urls`, `core_model`, and `core_api_key`.
- **Assist** supports role-specific text/vision work. Loader fields include `openrouter_url(s)`, optional token-plan URLs, conversation/summary/correction/emotion/vision/agent models, credential placeholders, and `provider_type`.

Not every provider implements every role.

## Resolution

1. `utils/api_config_loader.py` reads and caches the bundled JSON.
2. Core/assist fields are converted to uppercase runtime names.
3. Assist JSON fields merge over same-key code defaults.
4. Missing/malformed provider data falls back to code definitions.
5. `assist_api_key_fields` maps provider keys to runtime credential fields.

The JSON also contains keybook/provider metadata, an API-key registry, default-model metadata, native TTS/voice catalogs, livestream settings, and moderation settings. These have owning consumers and are not generic core/assist fields.

## Editing

- Keep provider keys stable and references consistent.
- Add only fields consumed by the loader or owning feature.
- Never commit real credentials.
- Update all eight locale files for user-visible text.
- Run provider/config tests and the documentation build.

See [API provider field reference](/api_providers_fields) for the schema contract.
