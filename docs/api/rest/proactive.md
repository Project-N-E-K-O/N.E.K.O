# Proactive Chat API

**Prefix:** `/api/proactive`

Endpoints for reading and changing the proactive-chat **mode** and the underlying proactive-chat **settings** fields. All writes go through `utils.preferences.save_global_conversation_settings`, so the field whitelist, type validation, and atomic-write logic live in one place.

::: info
This is distinct from `POST /api/proactive_chat` (see the [System API](./system.md)), which *generates* a proactive message. The endpoints here only read and update the proactive-chat configuration.
:::

## Mode

A mode is a server-defined preset of proactive-chat fields. The available presets are `off`, `normal`, `focus`, and `frequent`. When the persisted fields do not match any preset, the mode is reported as `custom`.

### `GET /api/proactive/mode`

Read the current mode together with the current proactive-chat fields.

**Response:**

```json
{
  "success": true,
  "mode": "normal",
  "available_modes": ["off", "normal", "focus", "frequent"],
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

`mode` is inferred from the persisted fields and may be `custom` if they don't match any preset. `settings` contains only the proactive-chat-related fields.

### `POST /api/proactive/mode`

Apply a preset mode.

**Body:**

```json
{ "mode": "focus" }
```

`mode` must be one of `off`, `normal`, `focus`, `frequent`. An unknown value is rejected.

**Response:**

```json
{
  "success": true,
  "mode": "focus",
  "applied": { "proactiveChatEnabled": true }
}
```

`applied` is read back from disk after saving (a strict by-value and by-type comparison). If any preset field fails to persist, a `rejected` array of field names is also returned.

::: info
Switching mode never changes `proactiveVisionEnabled` (the privacy-mode switch). Presets deliberately omit that field — whether the character may look at the screen is the user's own choice.
:::

## Settings

The settings endpoints read and partially update the proactive-chat fields directly, without going through a preset.

### `GET /api/proactive/settings`

Read the current proactive-chat fields (whitelisted subset of the conversation settings).

**Response:**

```json
{
  "success": true,
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

### `POST /api/proactive/settings`

Partially update proactive-chat fields. The body accepts only writable proactive-chat fields (for example `proactiveChatEnabled`, `proactiveChatInterval`, `proactiveVisionInterval`). Unrecognized fields are silently ignored, and `save_global_conversation_settings` performs another round of type/range validation.

**Body:**

```json
{ "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
```

**Response:**

```json
{
  "success": true,
  "applied": { "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
}
```

`applied` is read back from disk after saving. Fields whose value/type fail validation are listed in a `rejected` array. If the body includes `proactiveVisionEnabled`, that field is refused and reported under `rejected_user_owned`.

::: info
`proactiveVisionEnabled` is a user-owned field (the inverse of the privacy-mode switch, which governs screen-content capture). The **proactive-chat** endpoints never change it — they report it under `rejected_user_owned`; it is set through the main conversation-settings save path (the privacy-mode toggle in the UI), which is the user's own choice. Sending it here returns it under `rejected_user_owned` instead of applying it.
:::
