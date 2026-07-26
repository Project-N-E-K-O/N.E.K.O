# Runtime Tools API

The runtime-tools API lets a local plugin or companion process register HTTP callbacks as model-callable tools. The main server exposes it at `/api/tools`; registrations are applied to the current per-character session managers and synchronized to active model sessions before mutation calls return.

> [!IMPORTANT]
> This is a local integration contract, not a remote administration API. Every endpoint accepts loopback clients only. Callback URLs are also restricted to `localhost`, `127.0.0.0/8`, `::1`, or an IPv4-mapped loopback address over HTTP or HTTPS. Other callers receive HTTP `403`; non-loopback callback URLs fail validation with HTTP `422`.

Registrations are runtime state. They are not persisted across a server restart and a global registration applies only to the character session managers that exist when the request is handled.

## Route inventory

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/tools/register` | Register or replace a remote model-callable tool |
| `POST` | `/api/tools/unregister` | Remove a tool by name |
| `POST` | `/api/tools/clear` | Remove tools carrying a given `source` tag |
| `GET` | `/api/tools` | List current registrations |

## Register

```http
POST /api/tools/register
Content-Type: application/json

{
  "name": "get_weather",
  "description": "Get weather for a location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string"}
    },
    "required": ["location"]
  },
  "callback_url": "http://127.0.0.1:9333/tools/get_weather",
  "role": null,
  "source": "plugin:weather",
  "timeout_seconds": 30
}
```

| Field | Requirements | Default |
|---|---|---|
| `name` | String, 1–64 characters | required |
| `description` | Tool description shown to the model | `""` |
| `parameters` | JSON Schema object for tool arguments | empty object schema |
| `callback_url` | Loopback HTTP/HTTPS URL | required |
| `role` | Existing character name; `null` targets every current role | `null` |
| `source` | Lifecycle tag used by `clear`; plugins should use `plugin:<id>` | `"external"` |
| `timeout_seconds` | Greater than 0 and at most 300 | `30` |

Registration replaces a same-named tool in each target registry. An unknown non-null `role` returns HTTP `404`. Schema errors return HTTP `422`.

The response distinguishes complete, partial, and total failure:

```json
{
  "ok": true,
  "registered": "get_weather",
  "affected_roles": ["Lanlan"],
  "failed_roles": []
}
```

`ok` is false when no role accepted the registration. A partial success keeps `ok: true` and describes synchronization failures in `failed_roles`.

## Callback contract

When the model calls a remote tool, the main server sends:

```json
{
  "name": "get_weather",
  "arguments": {"location": "Shanghai"},
  "call_id": "call_123",
  "raw_arguments": "{\"location\":\"Shanghai\"}"
}
```

A successful callback can return any JSON value through `output`:

```json
{"output": {"temperature_c": 28}, "is_error": false}
```

To return a tool-level error without failing the HTTP exchange:

```json
{"error": "location not found", "is_error": true}
```

The dispatcher treats HTTP `4xx`/`5xx` as tool errors. A non-JSON response is passed through as text output. A request timeout or connection failure also becomes a model-visible tool error.

For sources beginning with `plugin:`, three consecutive connection-level failures to the same callback origin automatically evict only that origin's tools from all character registries. Read timeouts, HTTP error responses, and tool-level `is_error` responses do not count toward eviction.

Callback payloads may contain model-generated arguments derived from user conversation. Keep callbacks local and apply the same privacy handling as the plugin's other user-data paths.

## Unregister and clear

Remove one name from all roles:

```http
POST /api/tools/unregister
Content-Type: application/json

{"name": "get_weather", "role": null}
```

Remove every tool registered by one source:

```http
POST /api/tools/clear
Content-Type: application/json

{"source": "plugin:weather", "role": null}
```

Both endpoints can target one existing character with `role`. Their responses include `affected_roles` and `failed_roles`; `unregister` returns a boolean `removed`, while `clear` returns the number removed.

## List

`GET /api/tools` lists every current role. `GET /api/tools?role=Lanlan` selects one role and returns HTTP `404` if it does not exist.

```json
{
  "ok": true,
  "tools_by_role": {
    "Lanlan": [
      {
        "name": "get_weather",
        "description": "Get weather for a location.",
        "source": "plugin:weather",
        "callback_url": "http://127.0.0.1:9333/tools/get_weather",
        "is_remote": true
      }
    ]
  }
}
```
