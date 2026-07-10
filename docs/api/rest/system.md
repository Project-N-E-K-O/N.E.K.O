# System API

**Prefix:** `/api`

Miscellaneous system endpoints for emotion analysis, file utilities, screenshots, and proactive chat.

## Emotion analysis

### `POST /api/emotion/analysis`

Analyze the emotional tone of text.

**Body:**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name"
}
```

**Response:** Emotion label used for Live2D/VRM expression mapping.

## File utilities

### `GET /api/file-exists`

Check if a file exists at the given path.

**Query:** `path` — File path to check.

### `GET /api/find-first-image`

Find the first image file in a directory.

**Query:** `directory` — Directory path to search.

### `GET /api/meme/proxy-image`

Proxy a remote image (e.g. a meme) to bypass CORS restrictions, with SSRF protection and caching.

**Query:** `url` — Remote image URL to proxy (must be http/https).

### `GET /api/steam/proxy-image`

Proxy access to a local image file (notably the Steam Workshop directory), supporting absolute and relative paths.

**Query:** `image_path` — Local file path to the image.

## Steam achievements

### `POST /api/steam/set-achievement-status/{name}`

Unlock a Steam achievement. The achievement name is passed as a path parameter `{name}`.

**Path parameter:** `name` — The Steam achievement name (e.g. `ACH_FIRST_DIALOGUE`).

## Proactive chat

### `POST /api/proactive_chat`

Generate a proactive message from the character (used for idle conversation).

**Body:**

```json
{
  "lanlan_name": "character_name",
  "context": "optional context about what's happening"
}
```

**Response:** `action` is `chat` when a proactive line is delivered and `pass` when the
turn is skipped. Proactive `pass`/`chat`/error responses include a stable
machine-readable `reason_code`, such as `CHAT_DELIVERED`, `PASS_BUSY`,
`PASS_SOURCE_EMPTY`, `PASS_DUPLICATE`, `DELIVERY_PREEMPTED`, or `ERROR_TIMEOUT`.
They also include `stage`, a coarse process stage such as `entry_guard`,
`activity_gate`, `source_selection`, `model_decision`, `generation`, `dedup`,
`delivery`, or `runtime_error`.

::: info
Proactive messages are rate-limited: maximum 10 per character per hour.
:::

::: info
Internally, proactive chat runs a two-phase pipeline: a Phase 1 LLM call screens candidate web content (and extracts music/meme keywords) before the Phase 2 persona-aware reply is generated. There is no separately addressable web-screening endpoint.
:::

## Screenshots

### `POST /api/screenshot`

Backend screenshot fallback: when all frontend screen-capture APIs fail, the backend captures the local screen with pyautogui. Loopback-only; disabled when the backend is configured as remote.

**Response:** `{ "success": true, "data": "data:image/jpeg;base64,...", "size": <bytes> }`

### `POST /api/screenshot/interactive`

System-native interactive (region-select) screenshot, preferred by the chat screenshot button. macOS uses `screencapture` region selection; other platforms delegate to the frontend. Loopback-only.

**Response:** A JSON envelope (not a raw DataURL).

```json
{ "success": true, "data": "data:image/jpeg;base64,...", "size": <bytes> }
```

On a canceled selection: `{ "success": false, "canceled": true }`. On a non-localhost / remote-configured backend: `{ "success": false, "error": "..." }`.
