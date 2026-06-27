# Game API

**Prefix:** `/api/game`

Backend for the in-app minigames (soccer, badminton, …). Each minigame is driven by a generic two-player "double act": a behind-the-scenes **A** layer (a text-only LLM that receives game events and decides on lines plus structured control instructions) and a front-of-stage **B** layer that speaks or mirrors the chosen line through the existing project output channels (voice / TTS / text bubble).

Most endpoints take a `{game_type}` path parameter (e.g. `soccer`, `badminton`) so the same routes extend to new games without new handlers. The endpoints below are grouped by **Logs**, **Route lifecycle**, **Interaction**, and **Leaderboard**.

::: info
Some response fields and behaviours are game-specific. For example, leaderboards are currently only implemented for badminton, and `quick-lines` only supports `soccer` and badminton; other `game_type` values return an `ok: false` / skipped response rather than an error.
:::

## Logs

Per-session diagnostic logging for a minigame round. Logging is opt-in and bounded (limited number of retained sessions and entries per session).

### `GET /api/game/logs`

Read the diagnostic log for a game session as JSON, or list available sessions when no `session_id` is given.

**Query:** `session_id`, `game_type`, `since` (sequence cursor), `limit` (default `300`).

### `GET /api/game/logs/view`

Human-readable HTML view of the same diagnostic log. Pass `session_id` to view a single session; otherwise it renders a list of available sessions.

**Query:** `session_id`, `game_type`, `limit` (default `300`).

**Response:** An HTML page (not JSON).

### `POST /api/game/logs`

Append a diagnostic log entry for a session (frontend log ingestion; CSRF-validated).

**Body:** `session_id` (required; alias `sessionId`), `game_type` (alias `gameType`, default `game`), `lanlan_name` (alias `lanlanName`), plus the log-entry payload. A missing `session_id` returns `{ "ok": false, "reason": "missing_session_id" }`.

### `POST /api/game/logs/enable`

Manually enable diagnostic logging for a session.

**Body:**

```json
{
  "session_id": "round-id",
  "game_type": "soccer",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "session_id": "...", "game_type": "...", "seq": <number> }`, or `{ "ok": false, "reason": "..." }` when `session_id` is missing or enabling fails.

::: info
This is a local mutation endpoint and is CSRF-validated; a failed check returns `{ "ok": false, "reason": "csrf_validation_failed" }`.
:::

## Route lifecycle

A "game route" tracks the period during which a game window is open and the main external inputs (text / voice) are hijacked into the game. Only one route per character can be active at a time; starting a new one supersedes any other active route for that character.

### `POST /api/game/{game_type}/route/start`

Declare that the game window is open and that main-window inputs should be routed into this game.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, ... }` with the public route state, or `{ "ok": false, "reason": "missing_lanlan_name" }`.

::: info
`game_type` of `new_user_icebreaker` is rejected here with HTTP 400 — use the dedicated `/api/icebreaker/route/start` endpoint instead.
:::

### `GET /api/game/{game_type}/route/state`

Read the current public route state for a character + game type.

**Query:** `lanlan_name`.

**Response:** `{ "ok": true, "state": { ... } }`.

### `GET /api/game/route/active`

Reconcile late subscribers with the current game-window route state (the window state change is edge-triggered, so a newly loaded chat/pet client can miss the original "opened" event). Read-only; not scoped to a `game_type`.

**Query:** `lanlan_name`.

**Response:** `{ "ok": true, "active": false }`, or when active `{ "ok": true, "active": true, "game_type": "...", "session_id": "...", "lanlan_name": "..." }`.

### `POST /api/game/{game_type}/route/drain`

Drain backend outputs that were produced by hijacked main-window input for the game page.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "outputs": [ ... ], "state": { ... } }`. Returns an empty `outputs` list when no route is active or the `session_id` does not match the active route.

### `POST /api/game/{game_type}/route/voice-transcript`

Accept final text from an independent speech-to-text gate and route it into the game as user input.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "transcript": "final recognized text"
}
```

**Response:** `{ "ok": true, "handled": <bool>, "state": { ... } }`, or `{ "ok": false, "reason": "..." }` (e.g. `missing_transcript`, `missing_lanlan_name`, `invalid_body`). When the route is inactive or the session does not match, `handled` is `false` with a `reason`.

### `POST /api/game/{game_type}/route/heartbeat`

Refresh the game page heartbeat used to detect a missed exit cleanup, and report page visibility.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "active": true, "heartbeat_interval_seconds": <number>, "heartbeat_timeout_seconds": <number>, "state": { ... } }`. Returns `active: false` when no matching route is found.

### `POST /api/game/{game_type}/route/end`

End the game route, using the same cleanup contract as the public game-end endpoint.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "reason": "route_end"
}
```

**Response:** A cleanup result describing the ended route (archive / postgame info where applicable).

## Interaction

The core "double act" endpoints: send events to the A layer, then mirror or speak the resulting line through the B layer.

### `POST /api/game/{game_type}/chat`

Generic game LLM chat endpoint. Send a game event to the behind-the-scenes A layer and get back a spoken line plus optional control instructions.

**Body:**

```json
{
  "session_id": "round-id",
  "event": { },
  "lanlan_name": "character_name"
}
```

`event` is a game-defined dict passed through to the LLM.

**Response:**

```json
{
  "line": "the character's line",
  "control": { }
}
```

`control` carries optional game control instructions (e.g. mood / difficulty). On an invalid body or rate-limited / inactive route, the response includes an `error` or `skipped` field with empty `line` / `control`.

### `POST /api/game/{game_type}/mirror-assistant`

B-layer text output: mirror the A-layer line into the normal chat display **without** invoking TTS.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to mirror"
}
```

**Response:** `{ "ok": true, "lanlan_name": "...", "method": "project_text_mirror", ... }`, or `{ "ok": false, "reason": "..." }` (e.g. `missing_line`, `missing_lanlan_name`, `no_session_manager`).

### `POST /api/game/{game_type}/speak`

Formal B-layer output: speak the A-layer line through the existing project TTS pipeline.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to speak"
}
```

**Response:** A result describing the speak attempt (including TTS pipeline state), or `{ "ok": false, "reason": "..." }`.

::: info
`game_type` of `new_user_icebreaker` is rejected here with HTTP 400 — use `/api/icebreaker/speak` instead.
:::

### `POST /api/game/{game_type}/realtime-context`

Inject compact game context into the active Realtime voice session — a deliberately simple bridge for "non-voice information entering Realtime" that does not require provider function-calling support.

**Body:** Includes `lanlan_name` and a game `state` describing the current context.

**Response:** `{ "ok": true, ... }`, or `{ "ok": false, "reason": "..." }` (e.g. `no_active_realtime_session`, `no_session_manager`). CSRF-validated.

### `POST /api/game/{game_type}/quick-lines`

Generate character-specific quick lines when entering a game. On success the frontend replaces its built-in quick lines; on failure it keeps the built-ins.

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "character": "...", "lines": { }, "missing": [ ] }`. For badminton, a cache hit additionally sets `"cached": true`.

::: info
Only `soccer` and badminton are supported. Other `game_type` values return `{ "ok": false, "error": "...", "lines": {} }`.
:::

### `GET /api/game/{game_type}/character`

Return the current character's model information for in-game model replacement. Each minigame chooses Live2D, VRM, MMD, or an explicit fallback according to its own rendering support.

**Query:** `lanlan_name` (optional; defaults to the current character).

**Response:**

```json
{
  "lanlan_name": "character_name",
  "model_type": "live2d",
  "live3d_sub_type": "",
  "live2d_path": "/static/...",
  "mmd_path": "",
  "vrm_path": ""
}
```

### `POST /api/game/{game_type}/end`

End a game round and clean up the matching LLM session.

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name",
  "reason": "game_end"
}
```

**Response:** A cleanup result describing the ended round (archive / postgame info where applicable).

## Leaderboard

Per-game high-score leaderboards. Currently only badminton is backed by storage; other game types return empty / unsupported responses.

### `GET /api/game/{game_type}/leaderboard`

Read the leaderboard top entries and the caller's personal best.

**Query:** `session_id`, `lanlan_name`, `limit` (default `10`), `offset` (default `0`).

**Response:**

```json
{
  "ok": true,
  "top": [ ],
  "total_players": 0,
  "total_scores": 0,
  "limit": 10,
  "offset": 0,
  "has_more": false,
  "your_best": null
}
```

For unsupported game types, the same shape is returned with empty `top` and zero counts.

### `POST /api/game/{game_type}/leaderboard`

Submit a score to the leaderboard.

**Body:**

```json
{ "lanlan_name": "character_name", "session_id": "round-id", "mode": "..." }
```

The body must echo the round's score totals (e.g. `finalScore`); the server **validates them against its own recorded session totals** for that `lanlan_name` / `session_id` / `mode` (reserved during play). A mismatch — or an unknown/expired session — returns `{ "ok": false, "reason": "invalid_session" }`; a malformed body returns `invalid_body`. So a client cannot submit an arbitrary score: only the totals the server actually recorded for an in-progress round are accepted.

**Response:** `{ "ok": true, "rank": <number>, "total_players": <number>, "is_personal_best": <bool> }`, or `{ "ok": false, "reason": "..." }` (e.g. `invalid_session`, `invalid_body`, or an unsupported game type).
