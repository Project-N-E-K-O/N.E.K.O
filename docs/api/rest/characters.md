# Characters API

**Prefix:** `/api/characters`

This router owns character profiles, avatar bindings, persona onboarding, character cards, microphone selection, and voice lifecycle operations. The collection route is exactly `GET /api/characters` (no trailing slash).

## Character and avatar state

| Method and path | Purpose |
|---|---|
| `GET /api/characters` | List character profiles. The response can localize editable profile labels from the request language. |
| `POST /api/characters/catgirl` | Create a character from a JSON profile object. |
| `PUT /api/characters/catgirl/{name}` | Update mutable fields of an existing character. |
| `DELETE /api/characters/catgirl/{name}` | Delete a character by a safe path name. |
| `POST /api/characters/catgirl/delete` | Compatibility/rescue delete using a JSON body; supports historical names that cannot safely appear in a path. |
| `POST /api/characters/catgirl/{old_name}/rename` | Rename a character and migrate its associated state. Body: `{ "new_name": "..." }`. |
| `GET`, `POST /api/characters/current_catgirl` | Read or select the active character. POST body: `{ "catgirl_name": "..." }`. |
| `POST /api/characters/reload` | Reload character configuration from storage. |
| `POST /api/characters/master` | Update the master profile. |
| `POST /api/characters/master/{old_name}/rename` | Rename the master profile. |
| `POST`, `GET /api/characters/set_microphone`, `/get_microphone` | Save or read the selected microphone. POST expects `microphone_id`; `microphone_name` is optional. |

Character names are validated for path safety and bounded length. Most write endpoints use an application envelope such as `{ "success": true }`; malformed JSON, invalid names, missing characters, conflicts, or a storage write fence can produce `400`, `404`, `409`, or `503` responses depending on the operation.

### Avatar bindings

| Method and path | Purpose |
|---|---|
| `GET /api/characters/current_live2d_model` | Resolve the current or named character's active Live2D, VRM, MMD, or PNGTuber binding. Optional query: `catgirl_name`, `item_id`. |
| `PUT /api/characters/catgirl/l2d/{name}` | Update the character's avatar/model binding. Despite the historical `l2d` segment, the handler supports the current avatar families. |
| `PATCH /api/characters/catgirl/{name}/touch_set` | Replace the active model's touch-action set. |
| `PUT /api/characters/catgirl/{name}/lighting` | Update VRM lighting values. |
| `GET`, `PUT /api/characters/catgirl/{name}/mmd_settings` | Read or update MMD render, physics, lighting, and pointer-tracking settings. |

## Persona selection

| Method and path | Purpose |
|---|---|
| `GET /api/characters/persona-presets` | List localized built-in persona presets. |
| `GET`, `POST /api/characters/persona-onboarding-state` | Read or update the initial persona-onboarding state. |
| `POST`, `DELETE /api/characters/persona-reselect-current` | Request or clear manual reselection for the current character. |
| `GET`, `PUT`, `DELETE /api/characters/character/{name}/persona-selection` | Read, set, or clear the named character's persona selection. |

## Character cards and portraits

| Method and path | Purpose |
|---|---|
| `GET /api/characters/character-card/list` | List saved character cards. |
| `POST /api/characters/character-card/save` | Save imported card data into character configuration. |
| `POST /api/characters/catgirl/save-to-model-folder` | Write a character card into its model folder for packaging. |
| `GET /api/characters/catgirl/{name}/export` | Export a PNG character card with embedded profile/model archive data. |
| `GET /api/characters/catgirl/{name}/export-settings` | Export profile settings without model assets. |
| `POST /api/characters/import-card` | Import multipart `zip_file`; optional `card_image` supplies the face image. |
| `GET /api/characters/card-faces` | List available card-face metadata. |
| `GET /api/characters/card-metas` | List card origin/metadata records. |
| `GET`, `PUT /api/characters/catgirl/{name}/card-meta` | Read or update one card's metadata JSON. |
| `GET`, `PUT /api/characters/catgirl/{name}/card-face` | Read or upload (`image`) one card-face image. |
| `POST /api/characters/catgirl/{name}/export-with-portrait` | Export with multipart `portrait`; `include_model` defaults to true. |

Exports return files rather than JSON. Upload/import endpoints enforce filename, size, archive-entry, and path-containment checks; validation failures are normally `400` or `413`.

## Voice operations

| Method and path | Purpose |
|---|---|
| `GET /api/characters/voices` | List voices available for the active provider configuration. |
| `GET /api/characters/voice_preview` | Synthesize a localized preview for required `voice_id`; optional `language`/`i18n_language`. Returns base64 audio in JSON. |
| `PUT /api/characters/catgirl/voice_id/{name}` | Bind a voice ID to a character. |
| `GET /api/characters/catgirl/{name}/voice_mode_status` | Report the character's voice-mode state. |
| `POST /api/characters/catgirl/{name}/unregister_voice` | Remove the character's custom voice binding/registration. |
| `POST /api/characters/clear_voice_ids` | Clear stored local voice IDs from all characters. |
| `GET /api/characters/custom_tts_voices` | List custom TTS voices; optional `provider` filter. |
| `POST /api/characters/voices` | Register a custom voice from a JSON object. |
| `DELETE /api/characters/voices/{voice_id}` | Delete a registered custom voice. |
| `POST /api/characters/voice_clone` | Clone from multipart audio. Required fields include `file` and `prefix`; provider-specific fields are also accepted. |
| `POST /api/characters/voice_clone_direct` | Register/clone from a validated direct audio URL. Private-network and unsafe redirect targets are rejected. |
| `POST /api/characters/voice_design` | Create and save a reusable voice from `provider`, `prefix`, and `voice_prompt`. Supported providers and their constraints come from the TTS provider registry. |
| `POST /api/characters/voice_design_preview` | Ask ElevenLabs voice design for preview candidates. |
| `POST /api/characters/voice_design_create` | Persist a selected design preview as a reusable voice. |
| `POST /api/characters/audio/analyze_silence` | Analyze multipart audio field `file`. |
| `POST /api/characters/audio/trim_silence` | Trim multipart audio field `file`; optional `task_id`. |
| `GET /api/characters/audio/trim_progress/{task_id}` | Read trim progress. |
| `POST /api/characters/audio/trim_cancel/{task_id}` | Request trim cancellation. |

Voice provider errors are surfaced through the JSON envelope and, where the handler can classify them, HTTP `4xx`/`5xx` status codes. Treat provider-specific request fields and returned catalogs as runtime data, not a stable cross-provider schema.

## Implementation-verified route inventory

```text
GET    /api/characters
GET    /api/characters/character-card/list
POST   /api/characters/catgirl/save-to-model-folder
POST   /api/characters/character-card/save
GET    /api/characters/catgirl/{name}/export
GET    /api/characters/catgirl/{name}/export-settings
POST   /api/characters/import-card
GET    /api/characters/card-faces
GET    /api/characters/card-metas
GET    /api/characters/catgirl/{name}/card-meta
PUT    /api/characters/catgirl/{name}/card-meta
GET    /api/characters/catgirl/{name}/card-face
PUT    /api/characters/catgirl/{name}/card-face
POST   /api/characters/catgirl/{name}/export-with-portrait
POST   /api/characters/catgirl/{old_name}/rename
GET    /api/characters/current_catgirl
POST   /api/characters/current_catgirl
POST   /api/characters/reload
POST   /api/characters/master
POST   /api/characters/master/{old_name}/rename
POST   /api/characters/catgirl
PUT    /api/characters/catgirl/{name}
POST   /api/characters/catgirl/delete
DELETE /api/characters/catgirl/{name}
POST   /api/characters/set_microphone
GET    /api/characters/get_microphone
GET    /api/characters/current_live2d_model
PUT    /api/characters/catgirl/l2d/{name}
PATCH  /api/characters/catgirl/{name}/touch_set
PUT    /api/characters/catgirl/{name}/lighting
PUT    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/persona-presets
GET    /api/characters/persona-onboarding-state
POST   /api/characters/persona-onboarding-state
POST   /api/characters/persona-reselect-current
DELETE /api/characters/persona-reselect-current
GET    /api/characters/character/{name}/persona-selection
PUT    /api/characters/character/{name}/persona-selection
DELETE /api/characters/character/{name}/persona-selection
POST   /api/characters/audio/analyze_silence
POST   /api/characters/audio/trim_silence
GET    /api/characters/audio/trim_progress/{task_id}
POST   /api/characters/audio/trim_cancel/{task_id}
POST   /api/characters/voice_clone
POST   /api/characters/voice_design
POST   /api/characters/voice_design_preview
POST   /api/characters/voice_design_create
POST   /api/characters/voice_clone_direct
GET    /api/characters/voices
GET    /api/characters/voice_preview
PUT    /api/characters/catgirl/voice_id/{name}
GET    /api/characters/catgirl/{name}/voice_mode_status
POST   /api/characters/catgirl/{name}/unregister_voice
POST   /api/characters/clear_voice_ids
GET    /api/characters/custom_tts_voices
POST   /api/characters/voices
DELETE /api/characters/voices/{voice_id}
```
