# Memory API

**Prefix:** `/api/memory`

Manages conversation memory files and review configuration (proxied through the main server).

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
    { "role": "user", "text": "Hello!" },
    { "role": "assistant", "text": "Hi there!" }
  ]
}
```

::: info
Character names are validated with regex supporting CJK characters. Chat entries require `role` and `text` fields.
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

Get the memory review configuration (whether automatic review is enabled).

### `POST /api/memory/review_config`

Update memory review configuration.

**Body:**

```json
{ "enabled": true }
```
