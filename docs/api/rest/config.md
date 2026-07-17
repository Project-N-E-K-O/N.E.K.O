# Config API

**Prefix:** `/api/config`

This router exposes the configuration used by the first-party frontend: provider setup, connectivity probes, model display preferences, locale hints, conversation settings, GPT-SoVITS discovery, and runtime proxy selection.

::: warning Internal configuration surface
These routes are designed for the bundled UI, not as a versioned public configuration SDK. JSON field sets can grow with provider and frontend features. Keep the service loopback-only unless you add your own authentication and authorization layer.
:::

## Provider configuration and probes

### `POST /api/config/test_connectivity`

Runs the same class of provider probe used by the setup UI. The Pydantic body supports two modes:

```json
{
  "provider_key": "openai",
  "provider_scope": "core",
  "api_key": "..."
}
```

or a custom endpoint:

```json
{
  "url": "https://example.test/v1",
  "api_key": "...",
  "model": "model-name",
  "provider_type": "openai_compatible",
  "sub_type": "",
  "voice_id": "",
  "is_free": false
}
```

Response fields are `success`, and when relevant `error`, `error_code`, and `resolved_url`. Pydantic type errors return `422`; network/auth/model failures normally return `200` with `success: false` so the setup UI can display the classified error.

### Core provider endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/config/core_api` | Read the effective core/assist/audio provider configuration. Stored secrets are masked before they are returned. |
| `POST /api/config/core_api` | Merge validated provider settings into the core configuration and notify/restart affected sessions. A masked secret sent back by the UI is not treated as a replacement key. |
| `GET /api/config/api_providers` | Return the provider catalog and frontend metadata loaded from the runtime provider configuration. |

The POST body follows the field names returned to the first-party setup UI (for example `coreApi`, `coreApiKey`, `assistApi`, and provider-specific fields). This is a flexible JSON object rather than a fixed Pydantic schema.

### GPT-SoVITS

| Method and path | Purpose |
|---|---|
| `POST /api/config/gptsovits/list_voices` | Validate the supplied HTTP base URL and proxy the service's voice-list request. |
| `POST /api/config/gptsovits/test_connectivity` | Exercise the GPT-SoVITS WebSocket init/ready/synthesis flow without playing the result. |

Both take the connection settings used by the setup UI and return a `success` envelope. Upstream validation or connection failures may use `400`, `502`, or `504` depending on the failure stage.

## Preferences and conversation settings

| Method and path | Purpose |
|---|---|
| `GET /api/config/preferences` | Read per-model display preferences. |
| `POST /api/config/preferences` | Save one model's `model_path`, `position`, and `scale`, plus optional `parameters`, `display`, `rotation`, `viewport`, and `camera_position`. |
| `POST /api/config/preferences/set-preferred` | Move the required `model_path` to the front of the preference order. |
| `GET /api/config/conversation-settings` | Read global conversation settings and the telemetry branch used for first-launch defaults. |
| `POST /api/config/conversation-settings` | Save global conversation settings. Updating `noiseReductionEnabled` is applied to active compatible sessions. |

Preference validation failures are usually represented as `{ "success": false, "error": "..." }`. Storage maintenance mode can instead surface an HTTP service-unavailable response.

## Page and language data

| Method and path | Purpose |
|---|---|
| `GET /api/config/page_config` | Resolve the named/current character and model paths for Live2D, VRM, MMD, or PNGTuber. Optional query: `lanlan_name`. Response is `no-store`. |
| `GET /api/config/character_reserved_fields` | Return the reserved character-profile field configuration shared by frontend and backend validation. |
| `GET /api/config/steam_language` | Return the Steam locale plus GeoIP-derived locale hints when available. |
| `GET /api/config/user_language` | Return the user's configured language for frontend/subtitle use. |

## Proxy mode

### `POST /api/config/set_proxy_mode`

Hot-switches process proxy environment variables:

```json
{ "direct": true }
```

`direct: true` snapshots and removes proxy variables and sets `NO_PROXY=*`; `false` restores the snapshot. The response includes sanitized `proxies_after` values with credentials removed. This changes the running process only.

## Implementation-verified route inventory

```text
POST /api/config/test_connectivity
GET  /api/config/core_api
POST /api/config/core_api
GET  /api/config/api_providers
POST /api/config/gptsovits/list_voices
POST /api/config/gptsovits/test_connectivity
GET  /api/config/steam_language
GET  /api/config/user_language
GET  /api/config/character_reserved_fields
GET  /api/config/page_config
GET  /api/config/preferences
POST /api/config/preferences
POST /api/config/preferences/set-preferred
GET  /api/config/conversation-settings
POST /api/config/conversation-settings
POST /api/config/set_proxy_mode
```
