# Characters API

**Prefix:** `/api/characters`

Manages AI characters (referred to as "catgirls" or "lanlan" internally), including CRUD operations, voice settings, and microphone configuration.

## Character management

### `GET /api/characters/`

List all characters with optional language localization.

**Query:** `language` (optional) — Locale code for translated field names. Also respects `Accept-Language` header.

---

### `POST /api/characters/catgirl`

Create a new character.

**Body:** Character data object with personality fields (档案名, 昵称, 性格, system_prompt, voice_id, etc.).

---

### `PUT /api/characters/catgirl/{name}`

Update an existing character's settings.

**Path:** `name` — Character identifier.

**Body:** Updated character data (昵称, 性别, voice_id, etc.).

---

### `DELETE /api/characters/catgirl/{name}`

Delete a character. Cannot delete the currently active character.

---

### `POST /api/characters/catgirl/{old_name}/rename`

Rename a character. Updates all references including memory files.

**Body:**

```json
{ "new_name": "new_character_name" }
```

---

### `GET /api/characters/current_catgirl`

Get the currently active character name.

### `POST /api/characters/current_catgirl`

Switch the active character.

**Body:**

```json
{ "catgirl_name": "character_name" }
```

---

### `POST /api/characters/reload`

Reload character configuration from disk (hot-reload).

### `POST /api/characters/master`

Update the master (owner/player) information.

**Body:** Master data object (档案名, 昵称, etc.).

## Model binding

### `GET /api/characters/current_live2d_model`

Get the current character's model info.

**Query:** `catgirl_name` (optional), `item_id` (optional Workshop item ID)

### `PUT /api/characters/catgirl/l2d/{name}`

Update a character's model binding (Live2D or VRM).

**Body:**

```json
{
  "live2d": "model_directory_name",
  "vrm": "vrm_model_name",
  "model_type": "live2d",
  "item_id": "workshop_item_id",
  "vrm_animation": "animation_name"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

Update character's VRM lighting configuration.

**Body:**

```json
{
  "lighting": { ... },
  "apply_runtime": true
}
```

## Voice settings

### `PUT /api/characters/catgirl/voice_id/{name}`

Set a character's TTS voice ID.

**Body:**

```json
{ "voice_id": "voice-tone-xxxxx" }
```

### `GET /api/characters/catgirl/{name}/voice_mode_status`

Check voice mode availability for a character.

### `POST /api/characters/catgirl/{name}/unregister_voice`

Remove the custom voice from a character.

### `GET /api/characters/voices`

List all registered voice tones for the current API key.

### `GET /api/characters/voice_preview`

Preview a voice (returns audio stream).

**Query:** `voice_id`

### `POST /api/characters/clear_voice_ids`

Clear all saved voice ID records across all characters.

## Microphone

### `POST /api/characters/set_microphone`

Set the input microphone device.

**Body:**

```json
{ "microphone_id": "device_id" }
```

### `GET /api/characters/get_microphone`

Get the current microphone selection.
