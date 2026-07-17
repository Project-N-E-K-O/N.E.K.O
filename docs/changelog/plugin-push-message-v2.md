# Plugin SDK: `push_message` v2 (orthogonal axes + parts)

**Current-source status (verified 2026-07-16)**: v2 is the canonical schema. Legacy fields are still translated and emit deprecation warnings. This page does not guarantee which future release will remove them.

## Summary

`ctx.push_message` now uses two orthogonal axes plus an OpenAI-style `parts`
list, replacing the conflated `message_type` + `delivery` + `content` /
`binary_data` / `binary_url` legacy shape.

```python
ctx.push_message(
    visibility=[],                  # ["chat"] / ["hud"] / both / [] (default)
    ai_behavior="respond",          # respond / read / blind
    parts=[
        {"type": "text",  "text": "..."},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
        {"type": "image", "url":  "https://..."},
        {"type": "audio", "data": ..., "mime": "audio/mpeg"},
        {"type": "video", "url": "..."},
        {"type": "ui_action", "action": "media_play_url", "url": "..."},
        {"type": "ui_action", "action": "media_allowlist_add", "domains": [...]},
    ],
    source="my_plugin",
    target_lanlan="灵",
    metadata={...},
    priority=0,
)
```

* **`visibility`** = where the user sees the plugin's parts rendered
  *verbatim* (independent of AI). `[]` means "user does not see the
  parts directly; if AI replies, only the AI's bubble is visible".
* **`ai_behavior`** = how the LLM treats the parts (`respond` triggers a
  turn now, `read` ingests context for natural mention later, `blind`
  bypasses the LLM entirely).
* **`parts`** = ordered content list. `data: bytes` is base64-encoded by
  the SDK adapter for the wire.

Schema source-of-truth:
`plugin/sdk/shared/core/push_message_schema.py`.

## Why

The previous `push_message` had three problems we kept hitting:

1. **`message_type` overloaded routing + content shape** — every new use
   case (`proactive_notification`, `music_play_url`, `music_allowlist_add`,
   the proposed `media_inject`, …) needed a new enum value and a new
   `if msg_type == ...` branch in `proactive_bridge.py`. Two distinct
   axes (where it goes vs. what it carries) collapsed onto a single
   discriminator.
2. **`delivery` (`proactive` / `passive` / `silent`) implicitly bundled
   "AI engagement" with "user visibility"** — `silent` meant "no LLM AND
   HUD-only", which left no slot for "feed AI context but don't trigger
   a turn" (game agent screenshot streaming) or "render plugin's verbatim
   chat bubble without AI noticing" (music card today).
3. **`content` / `binary_data` / `binary_url` were one-of-three** — no
   way to send `text + image` together. Plugins that wanted a system
   prompt with an attached screenshot needed two separate
   `push_message` calls and hoped the order survived.

The new schema solves these by:

* dropping `message_type` entirely (use `parts[*].type` for content
  shape; use `visibility` + `ai_behavior` for routing);
* splitting `delivery` into two **truly orthogonal** axes — `visibility`
  and `ai_behavior` — that capture all 12 combinations the old single
  `delivery` enum couldn't express;
* using `parts: list[dict]` so a single push can carry text + media in
  any order.

## Migration cheat sheet

| Old | New |
|---|---|
| `message_type="proactive_notification"` (default) | drop the field; defaults are `visibility=[], ai_behavior="respond"` |
| `delivery="proactive"` / `reply=True` | default — drop |
| `delivery="passive"` | `ai_behavior="read"` |
| `delivery="silent"` / `reply=False` | `visibility=["hud"], ai_behavior="blind"` |
| `content="X"` | `parts=[{"type":"text","text":"X"}]` |
| `binary_data=bytes, mime=...` | choose `type="image" \| "audio" \| "video"` from the MIME and use `parts=[{"type":...,"data":bytes,"mime":...}]` (`video` is accepted by the schema but currently warn-dropped by `main_server`) |
| `binary_url=URL, mime=...` | choose `type="image" \| "audio" \| "video"` from the MIME and use `parts=[{"type":...,"url":URL,"mime":...}]` (same current `video` limitation) |
| `message_type="music_play_url"` | `parts=[{"type":"ui_action","action":"media_play_url","url":..., "media_type":"audio"}]`, `visibility=["chat"]`, `ai_behavior="blind"` |
| `message_type="music_allowlist_add"` | `parts=[{"type":"ui_action","action":"media_allowlist_add","domains":[...]}]`, `ai_behavior="blind"` |
| `register_music_domains(domains)` SDK helper | **deleted** — push directly via `ui_action: media_allowlist_add` (see above) |
| `description="X"` | `metadata={"description": "X"}` |
| `unsafe=True` | drop |
| `fast_mode=True` | drop; v2 uses the standard host delivery path (benchmark high-volume producers because the legacy batching/backpressure optimization is not preserved) |

## Backward compatibility

All legacy parameters (`message_type`, `description`, `content`,
`binary_data`, `binary_url`, `mime`, `delivery`, `reply`, `unsafe`,
`fast_mode`) still
work and are translated client-side by
`translate_push_message`.
Each active legacy parameter emits a `DeprecationWarning` on every call,
citing this version target. `None` values and inactive boolean flags
(`unsafe=False`, `fast_mode=False`) do not warn.

The wire payload populates **both** v2 (`schema`, `visibility`,
`ai_behavior`, `parts`) and synthesised legacy fields (`message_type`,
`content`, `binary_data`, `binary_url`, `mime`, `description`, `unsafe`,
`delivery`, `reply`) so that downstream readers that have not migrated yet (notably
`plugin/server/application/messages/query_service.py`)
keep working through the deprecation window.

`SdkContext.register_music_domains()` is **removed outright** — no
in-tree consumers were using it. Plugins that called it must migrate
to the `ui_action: media_allowlist_add` part shape.

## Wire serialisation: legacy `binary_data` is raw `bytes`

The synthesised legacy `binary_data` field on the wire payload carries the
image as **raw `bytes`** (the canonical, already-encoded copy rides in
`parts[].binary_base64`). The message_plane PUB publisher serialises every
event with `json.dumps`, and plain `json.dumps` raises `TypeError` on
`bytes`. That failure was swallowed by a bare `except: pass` in
`plugin/message_plane/pub_server.py`, so **every image-bearing
`push_message` — from any plugin, not just one — was silently dropped before
it reached `main_server`.** Symptom: the plugin UI shows the message
`queued`, but the cat never reacts and nothing is logged.

Fixed in `pub_server.py` by passing a `json.dumps(..., default=...)` hook
that base64-encodes `bytes` (and stringifies any other non-JSON value, so a
single unexpected field can never drop the whole message); the swallowing
`except: pass` now logs at `debug`. Subscribers are unaffected — they read
the image from `parts[].binary_base64`, never from `binary_data`.

This is shared infrastructure: any plugin sending an image part depends on
it. The whole class of bug can be removed only after the legacy
`binary_data` wire field is no longer emitted; that cleanup has not happened
in the source verified above.

## Recorded cleanup target (not a release guarantee)

The current source still carries TODO/deprecation text that names v0.9 as a
target. Treat that label as migration metadata, not as proof that a released
v0.9 has removed the compatibility layer. The pending cleanup consists of:

* Removing all legacy `push_message` parameters listed above.
* Removing the legacy fields synthesised on the wire payload (`message_type`,
  `content`, `binary_data`, `binary_url`, `mime`, `description`, `unsafe`,
  `delivery`, `reply`).
* Removing `description` everywhere it currently lingers — it has no semantic
  consumer in v2, only surfaces as a human label in legacy log lines and
  the `query_service` response.  Marked with `TODO(v0.9)` in
  `plugin/core/context.py` and
  `plugin/server/application/messages/query_service.py` so the cleanup PR
  can grep for the marker; plugin call sites are found by the static v1 checker.
* Removing the legacy event-bus event shape (`proactive_message` event type
  itself stays, but its `media_parts` / `visibility` / `ai_behavior`
  fields become the only schema; `delivery_mode` becomes derived).

## Current implementation map

* `plugin/sdk/shared/core/push_message_schema.py`
* `plugin/sdk/shared/core/context.py`, `types.py`
* `plugin/sdk/plugin/base.py` (deleted `register_music_domains`)
* `plugin/_types/protocols.py`, `_types/models.py`
* `plugin/core/context.py`
* `plugin/server/messaging/proactive_bridge.py`
* `app/main_server/character_runtime.py` (image `media_parts` → `session.stream_image`; audio/video warn-drop pending a transport)
* `plugin/plugins/{bilibili_danmaku,memo_reminder,sts2_autoplay}/__init__.py` (migrated senders)
* `plugin/PLUGIN_DEVELOPMENT_GUIDE.md`
