# System API

**Prefix:** `/api`

This router collects first-party application services that do not belong to a narrower resource router: readiness, notices, activity, prompt flows, screenshots, Steam integration, surveys, translation, proactive delivery, and small handoff helpers.

::: warning Local application API
Several endpoints read local files, capture the desktop, change Steam state, or mutate onboarding state. Mutation routes use the project's local-request/CSRF validation, and screenshot routes additionally require a loopback client. Do not expose this router directly to an untrusted network.
:::

## Readiness, usage, and notices

| Method and path | Purpose |
|---|---|
| `GET /api/system/status` | Return a `no-store` bootstrap snapshot. `status` is `starting`, `migration_required`, or `ready`, with storage-migration flags. |
| `GET /api/token-usage` | Return token statistics for query `days` (default 7, capped at 90). |
| `GET /api/pending-notices` | Peek queued prominent notices and return `{ notices, cursor }` without deleting them. |
| `POST /api/pending-notices/ack` | Drain only notices at or before body `cursor`, avoiding loss of notices queued after the read. |
| `POST /api/activity_signal` | Accept the bounded OS/activity heartbeat payload used by the frontend and feed it to the activity tracker. |

The status probe deliberately returns HTTP 200 with `ready: false` while startup information is unavailable; it is a bootstrap sentinel, not a deep health check.

## Changelog and survey

| Method and path | Purpose |
|---|---|
| `GET /api/changelog` | Return changelog entries newer than query `since`; query `lang` selects a validated locale with fallback. |
| `GET /api/survey` | Return the current-version localized survey for eligible Steam users, or `has_survey: false`. DNT/reporting opt-out disables delivery. |
| `POST /api/survey/submit` | Submit or skip the current-version survey. Answers are size/type capped and upload is best-effort; `uploaded` reports remote success. |

`POST /survey/submit` requires a valid local mutation request. The server uses its own app version rather than trusting a client-supplied survey version.

## Emotion and translation

### `POST /api/emotion/analysis`

Analyzes a text response for the named character and normalizes it to the project's emotion labels. The flexible JSON body includes `text` and `lanlan_name`; the response contains the normalized emotion and confidence. The route may use configured model analysis and degrade to bounded heuristic inference when necessary.

### `POST /api/translate`

First-party subtitle translation endpoint:

```json
{
  "text": "Hello",
  "target_lang": "ja",
  "source_lang": "en",
  "skip_google": false
}
```

`source_lang` is optional and auto-detected. The response contains `success`, `translated_text`, normalized source/target languages, and when relevant `google_failed`. Translation failure returns the original text in the application envelope.

## Local file and image helpers

| Method and path | Purpose and boundary |
|---|---|
| `GET /api/file-exists` | Required query `path`; returns `{ exists }`. It rejects explicit traversal components but intentionally supports normal absolute user/Workshop paths. |
| `GET /api/find-first-image` | Required query **`folder`**. Searches only approved application/assets/user-data roots for a fixed preview filename list and images under 1 MiB. |
| `GET /api/meme/proxy-image` | Required remote `url`; proxies HTTP(S) images with SSRF checks, content limits, and caching. |
| `GET /api/steam/proxy-image` | Required local `image_path`; serves approved local/Workshop images after containment and type checks. |

Missing input normally returns `400`; forbidden paths/targets use `403`; absent local files use `404`; upstream image failures can use `4xx`/`5xx` according to the proxy stage.

## Screenshots and active window

| Method and path | Purpose |
|---|---|
| `GET /api/get_window_title` | Return the active window title when the platform integration is available (primarily Windows). |
| `POST /api/screenshot` | Loopback-only pyautogui fallback capture. Returns a JPEG data URL and byte size. |
| `POST /api/screenshot/interactive` | Loopback-only native region selection on macOS; on other platforms tells the frontend to perform interactive capture. |

Screenshot success uses `{ "success": true, "data": "data:image/jpeg;base64,...", "size": 123 }`. Interactive cancellation uses `success: false, canceled: true`. Remote-configured or non-loopback requests are rejected rather than capturing the host desktop.

## Proactive and mini-game events

| Method and path | Purpose |
|---|---|
| `POST /api/proactive_chat` | Run the proactive source-selection/generation/delivery pipeline for `lanlan_name`. |
| `POST /api/proactive/music_played_through` | Record that a recommended song finished, a positive feedback signal for source weighting. |
| `POST /api/mini_game/invite/respond` | Apply the user's response to an active mini-game invitation state machine. |

Proactive responses use `action: chat` or `action: pass` and stable `reason_code`/`stage` fields for outcomes such as busy, empty source, duplicate, delivery preemption, timeout, or delivered chat. There is no separately callable "phase 1" screening route.

## Tutorial and autostart prompt state

These are internal endpoints used by the homepage prompt state machines:

| Method and path | Purpose |
|---|---|
| `GET /api/tutorial-prompt/state` | Read tutorial prompt state. |
| `POST /api/tutorial-prompt/heartbeat` | Record idle/interaction input and decide whether prompting is due. |
| `POST /api/tutorial-prompt/shown` | Record that the prompt was shown. |
| `POST /api/tutorial-prompt/decision` | Record the user's decision. |
| `POST /api/tutorial-prompt/reset` | Reset tutorial prompt state. |
| `POST /api/tutorial-prompt/tutorial-started` | Record tutorial start. |
| `POST /api/tutorial-prompt/tutorial-completed` | Record tutorial completion. |
| `GET /api/autostart-prompt/state` | Read autostart prompt state. |
| `POST /api/autostart-prompt/heartbeat` | Record homepage state and decide whether prompting is due. |
| `POST /api/autostart-prompt/shown` | Record display. |
| `POST /api/autostart-prompt/decision` | Record the user's autostart decision. |

All POST routes in this group require a validated local mutation request. Their bodies are first-party UI state payloads and are not a stable third-party schema.

## Steam state

| Method and path | Purpose |
|---|---|
| `POST /api/steam/set-achievement-status/{name}` | Unlock/set the named configured achievement. |
| `POST /api/steam/update-playtime` | Accumulate the bounded playtime delta and store Steam stats. |
| `GET /api/steam/list-achievements` | List configured achievement state; primarily a diagnostic endpoint. |

Steamworks-unavailable operations return a failure envelope; invalid local mutation requests are rejected before Steam state changes.

## Yui guide handoff

| Method and path | Purpose |
|---|---|
| `POST /api/yui-guide/handoff/create` | Create a short-lived, signed, in-memory one-time handoff token. Required: `target_page`; optional source/target path and resume metadata. |
| `POST /api/yui-guide/handoff/consume` | Consume a token using required `token`, `signature`, and `expected_page`; optional `consumer_id`. |

Responses are `no-store`. Invalid input is `400`, signature/origin/page mismatch is `403`, missing/expired token is `404`, and replay/conflict is `409`.

## Implementation-verified route inventory

```text
POST /api/activity_signal
GET  /api/changelog
GET  /api/survey
POST /api/survey/submit
POST /api/emotion/analysis
GET  /api/file-exists
GET  /api/find-first-image
GET  /api/meme/proxy-image
POST /api/mini_game/invite/respond
POST /api/proactive_chat
POST /api/proactive/music_played_through
GET  /api/tutorial-prompt/state
POST /api/tutorial-prompt/heartbeat
POST /api/tutorial-prompt/shown
POST /api/tutorial-prompt/decision
POST /api/tutorial-prompt/reset
GET  /api/autostart-prompt/state
POST /api/autostart-prompt/heartbeat
POST /api/autostart-prompt/shown
POST /api/autostart-prompt/decision
POST /api/tutorial-prompt/tutorial-started
POST /api/tutorial-prompt/tutorial-completed
GET  /api/get_window_title
POST /api/screenshot
POST /api/screenshot/interactive
GET  /api/system/status
GET  /api/token-usage
GET  /api/pending-notices
POST /api/pending-notices/ack
POST /api/steam/set-achievement-status/{name}
POST /api/steam/update-playtime
GET  /api/steam/list-achievements
GET  /api/steam/proxy-image
POST /api/translate
POST /api/yui-guide/handoff/create
POST /api/yui-guide/handoff/consume
```
