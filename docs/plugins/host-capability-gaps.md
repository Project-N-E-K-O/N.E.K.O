# Plugin Host Capability Gaps: audio / video parts

> This page records host-side capability gaps that plugins **cannot close on
> their own**. A couple of them have a limited workaround (audio can borrow the
> frontend player, see below), but a workaround is not the native channel — and
> video has none at all. Every claim is verified against the sources it cites:
> `app/main_server/character_runtime.py` and
> `plugin/server/messaging/proactive_bridge.py` for host behaviour,
> `main_logic/core/proactive.py` for how a `respond` push's images are
> delivered, `plugin/core/context.py` + `plugin/settings.py` +
> `plugin/message_plane/ingest_server.py` for the wire payload and its ceiling,
> `plugin/server/routes/media.py` + `plugin/sdk/shared/core/images.py` for the
> temporary media store, `plugin/sdk/shared/core/push_message_schema.py` for
> the part schema, and `static/jukebox/music_ui.js` for the frontend player.
> Only function and constant names are cited, so line drift cannot make the
> page wrong.
>
> **Status (re-verified 2026-08-28)**: the "Issue 1: image parts have no
> user-visible channel" gap this page originally reported was closed by #2835
> (`1d654e302`, merged 2026-08-28); it is rewritten below as the "Already
> shipped" section. What is still missing is a host-side consumer for audio /
> video parts.
>
> Plugin-side solvable adaptation issues (double-reply, session interruption,
> message listening, background photo gating, missing `target_lanlan`, etc.)
> were already fixed inside the community plugins themselves and are out of
> scope here.

## Already shipped: the user-visible channel for image parts (#2835)

For a push whose `visibility` contains `"chat"`, the text and image parts
render into the user's chat window **in their original order**, as a
source-labelled `role="system"` bubble (a system chip) — neither the
assistant's bubble nor the user's. A plugin may still phrase its text in the
character's voice; the label says where it came from.

**The branch is gated on `visibility` alone, independently of
`ai_behavior`**: `respond`, `read`, and `blind` all render. `ai_behavior` only
decides whether the same parts also enter the model's context:

```python
# The user sees the image; the model has no idea it exists.
push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)

# The user sees it, the model sees it, and she replies to it right away.
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)
```

One caveat on `read`: reaching the model is **best-effort** there. The host
clears the pending images when the current session has no `stream_image` (or
there is no session at all), and again when the realtime provider has no
native vision — `read` owns no ticket-bound channel to deliver a VISION_MODEL
description, so the host bails out early and says so in the log. The chat
bubble still renders in both cases. When the image must reach the model, use
`ai_behavior="respond"`, whose images ride the callback and need no session
yet.

Both image sources work:

- **inline**: `parts=[{"type": "image", "data": <bytes>, "mime": "image/png"}]`,
  encoded as `binary_base64` on the wire. Keep these **small** — see the wire
  budget below;
- **local temporary URL**: `await ctx.images.upload(data)` returns an image
  part you can drop straight into `parts`; its URL looks like
  `http://127.0.0.1:<port>/media/<id>` and is served by the host's temporary
  media store (`plugin/server/routes/media.py`). This is the right choice for
  anything that is not tiny: the URL costs a handful of bytes on the wire.

Implementation path: `_ordered_plugin_chat_blocks` / `_build_plugin_chat_blocks`
build the blocks → `LLMSessionManager.render_chat_blocks` emits a `chat_blocks`
WS frame → the frontend's `appendReactChatBlocks` renders the system chip.

On the model side both `read` and `respond` first resolve base64 through
`_resolve_plugin_model_image` / `_fetch_plugin_image_base64`, but they deliver
it differently — worth knowing if you go looking for the image in a log:
`read` hands it straight to the session's `stream_image`, while `respond`
defers it onto the callback (`media_images`) so it arrives together with the
proactive response — in voice mode via `_stream_cb_media` → `stream_image`, in
text mode as the `images=` argument of `prompt_ephemeral`.

### Limits that still apply

- **Arbitrary external URLs are still rejected.**
  `_is_local_plugin_media_url` requires `http` + a loopback host + a
  `/media/<id>` path with no query, fragment, or credentials. When it fails,
  the chat render skips that part and the model path logs
  `plugin image resolve failed; dropped`. To send an external image, run it
  through `ctx.images.upload()` first.
- **The transport ceiling bites before the host quotas do.** The whole
  `MESSAGE_PUSH` envelope must pack under
  `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` (default 256 KiB, overridable via
  `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES`), and `_build_wire_payload` still
  carries a legacy `binary_data` copy alongside `parts[].binary_base64` — so
  one inline image travels roughly twice, and a source image much over
  ~110 KiB is rejected at `plugin/message_plane/ingest_server.py` before any
  host-side quota is consulted. Anything larger must go through
  `ctx.images.upload()`.
- **The chat path and the model path have separate quotas.** Chat: at most
  `_PLUGIN_CHAT_IMAGE_MAX_COUNT = 8` images and
  `_PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES = 8 MiB` of inline bytes. Model: a
  per-image `_PLUGIN_IMAGE_MAX_BYTES = 8 MiB`, and each push shares the
  per-turn callback budget `_PLUGIN_IMAGE_MAX_COUNT` /
  `_PLUGIN_IMAGE_TOTAL_MAX_BYTES`. Images over budget are dropped without
  affecting the rest of the push. These 8 MiB figures bound what the host
  accepts once a push is through the transport — for inline parts the wire
  ceiling above is the limit you actually hit.
- **`ctx.images.upload()` cannot be called from a lifecycle handler** — the
  plugin command loop is not servicing upload responses while those run, so it
  raises `RuntimeError`. Call it from an entry, timer, message, or custom
  event handler instead.
- **Uploads are normalised to JPEG**: longest edge `MAX_IMAGE_EDGE = 2048`,
  source limits `MAX_SOURCE_IMAGE_BYTES = 32 MiB` /
  `MAX_SOURCE_IMAGE_PIXELS = 16M pixels`, output limit
  `MAX_UPLOADED_IMAGE_BYTES = 8 MiB`.

## Still missing: audio / video parts are dropped by the host

**Symptom**: pushing an audio / video part produces no output. On the model
side, only `ai_behavior in ("respond", "read")` enters the media loop, which
warns once and drops any non-image part; with `ai_behavior="blind"` the loop
is not entered at all, so there is **no diagnostic either**. The chat render
does not know these part types either (`_build_plugin_chat_blocks` handles
only text and image), so `visibility=["chat"]` renders nothing and logs
nothing.

```python
# respond / read: the host enters the media loop, warns, and drops audio/video
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}],
)
```

**Root cause** (source-verified):

- In `app/main_server/character_runtime.py` the media loop as a whole is
  guarded by `ai_behavior_v2 in ("respond", "read")`; inside it, the
  `part_type != "image"` branch logs
  `logger.warning("[EventBus] media_part type=%s not yet supported (mime=%s); dropped")`
  and `continue`s. The comment explains that `stream_audio` is the realtime
  microphone PCM pipeline (fixed sample rate + RNNoise gating), not a generic
  file injector, and that there is no video API.
- So `{"type": "audio"}` / `{"type": "video"}` are defined in the v2 schema
  (`plugin/sdk/shared/core/push_message_schema.py` and the official guide) but
  the host consumption side is not implemented.

**Impact**: plugins cannot push voice or video content into the session (TTS
audio files, music clips, game cutscenes). Verified workarounds:

- **audio**: `ui_action=media_play_url` plays a **directly playable, non-HLS**
  audio URL through the frontend audio player (the bridge maps the action to a
  `music_play_url` event, which the frontend routes to `dispatchMusicPlay`).
  Two preconditions:
  - the URL must already be on the playback allowlist.
    `sendMusicMessageDetailed` gives it a 500 ms grace window waiting for a
    `music-allowlist-updated` event; if the URL is still not allowlisted it
    rejects with `unsafe_url` and shows a toast. Register it first with
    `ui_action=media_allowlist_add` (`domains`, or exact `http_urls`), then
    push the playback action;
  - the URL must not be an HLS stream. `isUnsupportedMusicStream` runs before
    the player starts and rejects `.m3u8` with `unsupported_stream` plus an
    error toast, allowlisted or not.
- **video**: no workaround. `media_play_url` cannot stand in for it: the
  `music_play_url` event the bridge emits carries no `media_type`, the
  frontend always hands it to the music/audio player, and there is no video
  event channel on the frontend at all.

**Expectation**: provide a host-side consumer for audio / video parts (audio
could be injected into the session or forwarded to the frontend player; video
should at least support the url form) — or mark both part types as
unimplemented at the schema layer so `neko-plugin check` can stop a plugin
from shipping them.

---

### Verification

1. Push
   `push_message(visibility=["chat"], ai_behavior="blind", parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}])`:
   a system-chip image bubble labelled with the plugin's name appears in the
   chat window, and the image is absent from the model's context.
2. Same payload with `ai_behavior="respond"`: the same image bubble appears
   AND the image reaches the model (carried on the callback — `prompt_ephemeral`
   in text mode, `stream_image` in voice mode), so she replies about it.
3. Replace the part with `{"type": "image", "url": "https://example.com/cat.png"}`
   and keep `ai_behavior="respond"` (the model path is what logs here; `blind`
   never enters the media loop): the external URL is rejected — nothing renders
   and the host logs `plugin image resolve failed; dropped`. Using the part
   returned by `await ctx.images.upload(<bytes>)` works on both sides.
4. Push `parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}]`
   with `ai_behavior="respond"`: the host logs
   `media_part type=audio not yet supported` and produces nothing; with
   `ai_behavior="blind"` there is no log at all (the media loop is skipped and
   the chat render does not handle audio parts).
