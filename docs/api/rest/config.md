# Config API

**Prefix:** `/api/config`

Manages API provider configuration, user preferences, and page settings.

## Endpoints

### `GET /api/config/page_config`

Get page configuration (model path, model type).

**Query parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `lanlan_name` | string | No | Character name |

**Response:** Page configuration including Live2D/VRM model path and type.

---

### `GET /api/config/preferences`

Get user preferences (model choices, display settings).

---

### `POST /api/config/preferences`

Update user preferences.

**Body:** JSON object with preference key-value pairs.

---

### `POST /api/config/preferences/set-preferred`

Set the preferred model for a character.

**Body:**

```json
{
  "model_name": "model_name_here",
  "model_path": "/path/to/model"
}
```

---

### `GET /api/config/steam_language`

Get the Steam client's language setting. Used for automatic locale detection.

---

### `GET /api/config/user_language`

Get the user's configured language preference.

---

### `GET /api/config/core_api`

Get the current core API configuration (provider, model, endpoints).

::: warning
This endpoint does not expose raw API keys. Keys are returned in masked form.
:::

---

### `POST /api/config/core_api`

Update core API configuration.

**Body:**

```json
{
  "coreApiKey": "sk-xxxxx",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "sk-xxxxx"
}
```

See [API Providers](/config/api-providers) for available provider values.

---

### `GET /api/config/api_providers`

Get the list of all available API providers and their configurations.

---

### `POST /api/config/gptsovits/list_voices`

List available GPT-SoVITS voices from a local service.

**Body:** Voice service connection settings.
