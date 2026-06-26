# Memory API

**Prefix:** `/api/memory`

Manages conversation memory files and review configuration.

## Recent memory files

### `GET /api/memory/recent_files`

List all `recent_*.json` files in the memory store.

### `GET /api/memory/recent_file`

Get the content of a specific memory file.

**Query:** `filename` — Name of the memory file.

### `POST /api/memory/recent_file/save`

Save an updated memory file.

**Body:**

```json
{
  "filename": "recent_character_name.json",
  "chat": [
    { "role": "哥哥", "text": "Hello!" },
    { "role": "小天", "text": "Hi there!" }
  ]
}
```

The character name is derived from `filename` (via `extract_catgirl_name_from_recent_filename`), not read from the body. Each chat entry requires a `role` string; the message text is read from the `text` field.

Note: `role` is the speaker's name as stored in `recent.json` — the master's configured name for the human's turns and the character's name for the AI's turns — NOT `"user"`/`"assistant"`. The handler writes each entry's `role` verbatim.

::: info
Character names are validated with regex supporting CJK characters. Chat history entries are validated for required fields.
:::

## Name management

### `POST /api/memory/update_catgirl_name`

Update a character's name across all memory files.

**Body:**

```json
{
  "old_name": "old_character_name",
  "new_name": "new_character_name"
}
```

## Review configuration

### `GET /api/memory/review_config`

Get the memory review configuration (compression schedule, retention settings).

### `POST /api/memory/review_config`

Update memory review configuration.
