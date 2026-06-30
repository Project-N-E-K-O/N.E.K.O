# Icebreaker API

**Prefix:** `/api/icebreaker`

New-user onboarding ("icebreaker") endpoints. The icebreaker is an onboarding conversation, not a mini-game: it keeps its own lifecycle state, separate from the game-route lifecycle. It can append context and speak fixed onboarding lines, but it never makes `/api/game/route/active` report an open mini-game window.

::: info
All mutating endpoints (`/route/start`, `/route/end`, `/context`, `/choice`, `/speak`) are local-mutation endpoints guarded by the same CSRF / local-request validation as the rest of the backend. A failed check returns `{ "ok": false, "reason": "csrf_validation_failed" }`.
:::

::: info
`lanlan_name` identifies the active character and is **required** on the mutating POST endpoints. `/route/start`, `/route/end`, and `/speak` resolve it from the body and otherwise fall back to the currently selected character (`当前猫娘`); if no character can be resolved they return `{ "ok": false, "reason": "missing_lanlan_name" }`. `/context` is stricter: it requires a non-empty `lanlan_name` in the body (no fallback) and returns `{ "ok": false, "reason": "missing_lanlan_name" }` when it is missing or empty.
:::

## Route lifecycle

### `POST /api/icebreaker/route/start`

Activate the icebreaker route for a character and bind it to a session.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid"
}
```

`session_id` is required; a missing value returns `{ "ok": false, "reason": "missing_session_id" }`.

**Response:**

```json
{
  "ok": true,
  "state": {
    "icebreaker_active": true,
    "lanlan_name": "character_name",
    "session_id": "session-uuid",
    "started_at": 0.0,
    "last_activity": 0.0,
    "source": "new_user_icebreaker"
  }
}
```

### `POST /api/icebreaker/route/end`

Finalize the active icebreaker route.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "reason": "icebreaker_end"
}
```

`reason` is optional and defaults to `icebreaker_end`.

**Response:** `{ "ok": true, "state": <route state> }`.

::: info
If `session_id` is supplied but does not match the active route's session (e.g. a second tab opened a newer session), the call is rejected without ending the route:

```json
{
  "ok": false,
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "route_end",
  "state": "<route state>"
}
```
:::

### `GET /api/icebreaker/route/state`

Read the current icebreaker route state for a character.

**Query:** `lanlan_name` — Character name (optional; falls back to the selected character).

**Response:** The public route state. When no route is active, `state` is `{ "icebreaker_active": false }`.

```json
{ "ok": true, "state": { "icebreaker_active": false } }
```

## Onboarding interactions

### `POST /api/icebreaker/context`

Append a line of onboarding context (user or assistant) to the project session history. The text is also cached for the memory system. Requires an active route bound to the same session.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "role": "assistant",
  "text": "Welcome! Let's get you set up."
}
```

- `role` must be `assistant` or `user`; otherwise `{ "ok": false, "reason": "invalid_role" }`.
- `text` is required (`missing_text`) and capped at 2000 characters (`invalid_text_length`).
- An optional `request_id` (also accepted as `event.request_id`) is used for deduplication.

**Response:**

```json
{
  "ok": true,
  "method": "project_session_history",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "session_id": "session-uuid",
  "memory_cached": true
}
```

A duplicate append returns the same envelope with `"deduped": true`.

If no route is active, the call returns:

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_session_history"
}
```

If a route is active but the supplied `session_id` does not match it (a stale or superseded session), the append is skipped:

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_session_history",
  "state": "<route state>"
}
```

### `POST /api/icebreaker/choice`

Persist a single effective tutorial choice into the durable choices pool, so it survives across sessions. Requires an active route bound to the same session.

::: info
This endpoint is write-only for now: the recorded choice does not enter the memory system and does not influence the model. It is kept separate from `/context` (which feeds transient session history) so the pool stays an independent signal that can be consumed later.
:::

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "day": 1,
  "node_id": "intro",
  "choice": "option_a",
  "label": "Tell me more",
  "handoff": false,
  "completed": false,
  "seq": 0
}
```

**Response:** The persistence result from the choices pool, with `source` set to `new_user_icebreaker`. If no route is active, the call returns `{ "ok": false, "reason": "route_not_active", ... }`.

### `POST /api/icebreaker/speak`

Speak a fixed onboarding line through the project TTS pipeline (and mirror it into the chat surface). Requires an active route bound to the same session.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "line": "Nice to meet you!",
  "mirror_text": true,
  "emit_turn_end": true,
  "interrupt_audio": false
}
```

- `line` is required (`missing_line`); SSML-like tags are stripped and the line is truncated to 240 characters.
- `mirror_text` and `emit_turn_end` default to `true`; `interrupt_audio` defaults to `false`.

**Response:** The TTS result envelope, including `method: "project_tts"` and a `voice_source` block:

```json
{
  "ok": true,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "voice_source": { "provider": "project_tts", "method": "project_tts" }
}
```

If no route is active, the call returns:

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_tts",
  "audio_sent": false
}
```

If a route is active but the supplied `session_id` does not match it (a stale or superseded session), the line is not spoken:

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "state": "<route state>",
  "audio_sent": false,
  "audio_committed": false,
  "voice_source": {
    "provider": "project_tts",
    "method": "project_tts",
    "skipped": "stale_session"
  }
}
```
