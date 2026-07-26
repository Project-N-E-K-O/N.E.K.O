# GalGame API

**Prefix:** `/api`

GalGame mode endpoint. Given the recent dialogue, it generates three branching reply candidates for the player to pick from, in the style of a visual-novel choice menu. The React chat window calls this after a completed catgirl turn when the GalGame mode toggle is on.

This is one first-party route, not a standalone public text-generation API. It sends recent conversation text to the configured summary-model provider, so callers must apply the same consent, privacy, and provider-quota expectations as the chat UI. The path has no trailing slash.

## Reply options

### `POST /api/galgame/options`

Generate three reply candidates for the player, one per style:

- **A** — Serious and grounded: stays on topic, answers factually, no roleplay.
- **B** — Warm and affectionate: soft, caring tone.
- **C** — Wild and imaginative: playful what-ifs, fantasy framing, quirky humor.

**Body:**

```json
{
  "messages": [
    { "role": "user", "text": "..." },
    { "role": "assistant", "text": "..." }
  ],
  "language": "en",
  "lanlan_name": "character_name",
  "master_name": "player_name"
}
```

- `messages` — Recent dialogue. Each entry has a `role` (`"assistant"` or `"user"`) and a `text` string. Only the most recent turns are kept, and the **last turn must be from the assistant** — otherwise the request is rejected.
- `language` — Optional. Short language code (e.g. `"en"`, `"zh"`, `"ja"`). When omitted, the language is detected from the latest message.
- `lanlan_name` — Optional. Character name. Falls back to the configured character, then a default placeholder.
- `master_name` — Optional. Player name. Falls back to the configured value, then a default placeholder.

**Response:**

```json
{
  "success": true,
  "options": [
    { "label": "A", "text": "..." },
    { "label": "B", "text": "..." },
    { "label": "C", "text": "..." }
  ]
}
```

::: info
This endpoint is designed to never hard-fail. On a generation timeout, model/parsing error, or an unconfigured summary model, it still returns `success: true` with pre-written fallback options and a `"fallback": true` flag. If the model returns only some of the A/B/C styles, the missing slots are filled from fallback and the response carries `"partial": true` alongside a `missing_labels` list.
:::

::: info
Reply generation runs on the **summary** model tier. If no summary model is configured, fallback options are returned immediately. When a session takeover is active (e.g. voice input routed into game logic), generation is skipped and fallback options are returned with `"reason": "session_takeover"`.
:::

A request is rejected with HTTP `400` when the body is not valid JSON (`"error": "invalid_json"`), is not an object (`"error": "invalid_payload"`), or contains no assistant turn to reply to (`"error": "no_assistant_turn"`).
