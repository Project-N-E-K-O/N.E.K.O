# Characters API

**Prefix:** `/api/characters`

Manages AI characters (referred to as "catgirls" or "lanlan" internally), including CRUD operations, voice settings, and microphone configuration.

## Character management

### `GET /api/characters/`

List all characters with optional language localization.

**Query:** `language` (optional) — Locale code for translated field names.

---

### `POST /api/characters/catgirl`

Create a new character.

**Body:** Character data object with personality fields.

---

### `PUT /api/characters/catgirl/{name}`

Update an existing character's settings.

**Path:** `name` — Character identifier.

**Body:** Updated character data.

---

### `DELETE /api/characters/catgirl/{name}`

Delete a character.

---

### `POST /api/characters/catgirl/{old_name}/rename`

Rename a character. Updates all references including memory files.

**Body:**

```json
{ "new_name": "new_character_name" }
```

---

### `GET /api/characters/current_catgirl`

Get the currently active character.

### `POST /api/characters/current_catgirl`

Switch the active character.

**Body:**

```json
{ "catgirl_name": "character_name" }
```

---

### `POST /api/characters/reload`

Reload character configuration from disk.

### `POST /api/characters/master`

Update the master (owner/player) information.

## Live2D model binding

### `GET /api/characters/current_live2d_model`

Get the current character's Live2D model info.

**Query:** `catgirl_name` (optional), `item_id` (optional)

### `PUT /api/characters/catgirl/l2d/{name}`

Update a character's Live2D model binding.

**Body:**

```json
{
  "live2d": "model_directory_name",
  "live2d_item_id": "workshop_item_id"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

Update character's VRM lighting configuration.

**Body:**

```json
{ "brightness": 0.8 }
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

List available TTS voices.

**Query:** `voice_provider` (optional) — Filter by provider.

### `GET /api/characters/voice_preview`

Preview a voice (returns audio stream).

**Query:** `voice_id`, `text`, `provider`

### `POST /api/characters/voices`

Add a custom voice configuration.

### `DELETE /api/characters/voices/{voice_id}`

Delete a custom voice.

### `POST /api/characters/voice_clone`

Clone a voice from audio samples.

**Body:** `multipart/form-data` with audio file(s).

## Microphone

### `POST /api/characters/set_microphone`

Set the input microphone device.

**Body:**

```json
{
  "device_name": "Built-in Microphone",
  "device_id": "default"
}
```

### `GET /api/characters/get_microphone`

Get the current microphone settings.

## Character cards

### `GET /api/characters/character-card/list`

List character card files.

### `POST /api/characters/character-card/save`

Save a character card.

### `POST /api/characters/catgirl/save-to-model-folder`

Save character data to the model folder for Workshop publishing.
