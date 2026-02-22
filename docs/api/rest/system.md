# System API

**Prefix:** `/api`

Miscellaneous system endpoints for emotion analysis, file utilities, screenshots, and proactive chat.

## Emotion analysis

### `POST /api/analyze_emotion`

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

### `GET /api/proxy-image`

Proxy an image request to bypass CORS restrictions.

**Query:** `url` — Image URL to proxy.

## Steam achievements

### `POST /api/steam_achievement`

Unlock a Steam achievement.

**Body:**

```json
{ "achievement_id": "ACHIEVEMENT_NAME" }
```

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

::: info
Proactive messages are rate-limited: maximum 10 per character per hour.
:::

## Web screening

### `POST /api/web_screening`

Screen web content through AI review (for content filtering and relevance ranking).

**Body:** Web content data with screening mode.

## Screenshot analysis

### `POST /api/screenshot_analysis`

Analyze a screenshot using a vision model.

**Body:** Base64-encoded image data with optional context.
