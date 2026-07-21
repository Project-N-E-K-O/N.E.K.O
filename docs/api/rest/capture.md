# Capture Bridge API

The capture router is the HTTP side of N.E.K.O.'s Electron renderer capture bridge. The GalGame plugin uses it when native capture backends cannot read another application's window—for example, on Linux under pure Wayland.

> [!WARNING]
> This is a first-party, local bridge contract, not a general screenshot API. Both endpoints accept loopback clients only, and a connected N.E.K.O. Electron renderer must service the request through the main WebSocket. A non-loopback caller receives HTTP `403`.

## Route inventory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/capture/health` | Report whether a capture-capable renderer is connected |
| `POST` | `/api/capture/screenshot` | Ask that renderer to capture a selected desktop source |

## Health

When a renderer is registered:

```http
GET /api/capture/health
```

```json
{"success": true, "available": true}
```

If no renderer is registered, the endpoint returns HTTP `503`:

```json
{"success": false, "available": false, "error": "no_renderer"}
```

## Capture a source

```http
POST /api/capture/screenshot
Content-Type: application/json

{
  "target_id": "window:123456:0",
  "pid": 4242,
  "title": "Example Game"
}
```

| Field | Type | Requirements |
|---|---|---|
| `target_id` | integer or string | Required; normalized to a string; length 1 through the bridge limit |
| `pid` | integer | Required and greater than 0 |
| `title` | string | Optional, maximum 512 characters; defaults to `""` |

Unknown fields are rejected. `target_id` may be a native window handle from a plugin backend or an Electron `desktopCapturer` source ID. The renderer is responsible for resolving the source; the HTTP router does not capture the desktop itself.

Success returns an image data URL and optional renderer metadata:

```json
{
  "success": true,
  "image": "data:image/jpeg;base64,...",
  "width": 1920,
  "height": 1080,
  "source_id": "window:123456:0"
}
```

`width`, `height`, and `source_id` are included only when the renderer supplies valid values.

## Error responses

| Status | `error` | Meaning |
|---:|---|---|
| `400` | `invalid_json` | Request body is not valid JSON |
| `403` | `loopback_only` | Caller is not a loopback client |
| `422` | `validation_error` | Body fields or `target_id` length are invalid |
| `502` | `source_not_found` | Renderer could not find the selected desktop source |
| `502` | `bridge_error` | Capture bridge returned another upstream error |
| `502` | `empty_image` | Renderer responded without a usable image |
| `503` | `no_renderer` | No capture-capable renderer is connected |
| `504` | `renderer_timeout` | Renderer did not answer before the bridge timeout |
| `500` | `internal_error` | Unexpected server-side failure |

The bridge uses the WebSocket `capture_bridge_status`, `capture_bridge_response`, and server-side capture request messages described in [Message Types](/api/websocket/message-types). Treat the returned image as user-sensitive screen content and avoid logging it.
