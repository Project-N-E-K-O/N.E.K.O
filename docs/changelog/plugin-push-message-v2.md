# Plugin SDK: `push_message` v2 (orthogonal axes + parts)

**Status**: introduced this release · old fields scheduled to be removed in **v0.9**.

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
| `binary_data=bytes, mime=...` | `parts=[{"type":"image","data":bytes,"mime":...}]` (or `audio`; `video` accepted in schema but main_server warn-drops it for now) |
| `binary_url=URL` | `parts=[{"type":"image","url":URL}]` |
| `message_type="music_play_url"` | `parts=[{"type":"ui_action","action":"media_play_url","url":..., "media_type":"audio"}]`, `visibility=["chat"]`, `ai_behavior="blind"` |
| `message_type="music_allowlist_add"` | `parts=[{"type":"ui_action","action":"media_allowlist_add","domains":[...]}]`, `ai_behavior="blind"` |
| `register_music_domains(domains)` SDK helper | **deleted** — push directly via `ui_action: media_allowlist_add` (see above) |
| `description="X"` | `metadata={"description": "X"}` |
| `unsafe=True` | drop |

## Backward compatibility

All legacy parameters (`message_type`, `description`, `content`,
`binary_data`, `binary_url`, `mime`, `delivery`, `reply`, `unsafe`) still
work and are translated client-side by
`translate_push_message`.
Each legacy parameter that is actually passed emits a `DeprecationWarning`
on every call, citing this version target.

The wire payload populates **both** v2 (`schema`, `visibility`,
`ai_behavior`, `parts`) and synthesised legacy fields (`message_type`,
`content`, `binary_url`, `description`) so that downstream readers that
have not migrated yet (notably
`plugin/server/application/messages/query_service.py`)
keep working through the deprecation window.

`SdkContext.register_music_domains()` is **removed outright** — no
in-tree consumers were using it. Plugins that called it must migrate
to the `ui_action: media_allowlist_add` part shape.

## Removed in v0.9

* All legacy `push_message` parameters listed above.
* The legacy fields synthesised on the wire payload (`message_type`,
  `content`, `binary_data`, `binary_url`, `description`, `unsafe`,
  `delivery`, `reply`).
* `description` everywhere it currently lingers — has no semantic
  consumer in v2, only surfaces as a human label in legacy log lines and
  the `query_service` response.  Marked with `TODO(v0.9)` in
  `plugin/core/context.py`, `plugin/server/application/messages/query_service.py`,
  and the three migrated in-tree plugin senders
  (`bilibili_danmaku` / `memo_reminder` / `sts2_autoplay`) so the cleanup
  PR can grep for the marker.
* The legacy event-bus event shape (`proactive_message` event type
  itself stays, but its `media_parts` / `visibility` / `ai_behavior`
  fields become the only schema; `delivery_mode` becomes derived).

## Touched files (this release)

* `plugin/sdk/shared/core/push_message_schema.py` (new)
* `plugin/sdk/shared/core/context.py`, `types.py`
* `plugin/sdk/plugin/base.py` (deleted `register_music_domains`)
* `plugin/_types/protocols.py`, `_types/models.py`
* `plugin/core/context.py`
* `plugin/server/messaging/proactive_bridge.py`
* `main_server.py` (image `media_parts` → `session.stream_image`; audio/video warn-drop pending a transport)
* `plugin/plugins/{bilibili_danmaku,memo_reminder,sts2_autoplay}/__init__.py` (migrated senders)
* `plugin/PLUGIN_DEVELOPMENT_GUIDE.md`
