# System API

**Prefix:** `/api`

Miscellaneous system endpoints for emotion analysis, file utilities, Steam integration, proactive chat, and translation.

## Emotion analysis

### `POST /api/emotion/analysis`

Analyze the emotional tone of text.

**Body:**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name",
  "api_key": "optional",
  "model": "optional"
}
```

**Response:**

```json
{
  "emotion": "happy",
  "confidence": 0.95
}
```

## File utilities

### `GET /api/file-exists`

Check if a file exists at the given path.

**Query:** `path` — File path to check.

### `GET /api/find-first-image`

Find the first preview image file in a folder.

**Query:** `folder` — Folder path to search.

### `GET /api/steam/proxy-image`

Proxy an image request for local file access.

**Query:** `image_path` — Local image file path.

## Steam integration

### `POST /api/steam/set-achievement-status/{name}`

Set a Steam achievement's unlock status.

**Path:** `name` — Achievement identifier.

### `GET /api/steam/list-achievements`

List all Steam achievements and their status.

### `POST /api/steam/update-playtime`

Update the tracked game playtime.

**Body:**

```json
{ "seconds": 3600 }
```

## Proactive chat

### `POST /api/proactive_chat`

Generate a proactive message from the character (two-stage architecture).

**Body:**

```json
{
  "lanlan_name": "character_name",
  "enabled_modes": ["vision", "news", "video", "home", "window", "personal"],
  "screenshot_data": "optional base64",
  "language": "zh"
}
```

## Translation

### `POST /api/translate`

Translate text between languages.

**Body:**

```json
{
  "text": "Hello",
  "target_lang": "zh",
  "source_lang": "en",
  "skip_google": false
}
```

## Other endpoints

### `POST /api/personal_dynamics`

Get personalised content data.

**Body:**

```json
{ "limit": 10 }
```

### `GET /api/get_window_title`

Get the currently active window title (Windows only).
