# Plugin Host Capability Gaps: audio / video parts

> This page records host-side capability gaps that plugins **cannot work
> around** themselves. Every claim is verified against
> `app/main_server/character_runtime.py` and
> `plugin/server/messaging/proactive_bridge.py`. Only function and constant
> names are cited, so line drift cannot make the page wrong.
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

Both image sources work:

- **inline**: `parts=[{"type": "image", "data": <bytes>, "mime": "image/png"}]`,
  encoded as `binary_base64` on the wire;
- **local temporary URL**: `await ctx.images.upload(data)` returns an image
  part you can drop straight into `parts`; its URL looks like
  `http://127.0.0.1:<port>/media/<id>` and is served by the host's temporary
  media store (`plugin/server/routes/media.py`).

Implementation path: `_ordered_plugin_chat_blocks` / `_build_plugin_chat_blocks`
build the blocks → `LLMSessionManager.render_chat_blocks` emits a `chat_blocks`
WS frame → the frontend's `appendReactChatBlocks` renders the system chip. The
model side resolves base64 through `_resolve_plugin_model_image` /
`_fetch_plugin_image_base64` and hands it to `stream_image`.

### Limits that still apply

- **Arbitrary external URLs are still rejected.**
  `_is_local_plugin_media_url` requires `http` + a loopback host + a
  `/media/<id>` path with no query, fragment, or credentials. When it fails,
  the chat render skips that part and the model path logs
  `plugin image resolve failed; dropped`. To send an external image, run it
  through `ctx.images.upload()` first.
- **The chat path and the model path have separate quotas.** Chat: at most
  `_PLUGIN_CHAT_IMAGE_MAX_COUNT = 8` images and
  `_PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES = 8 MiB` of inline bytes. Model: a
  per-image `_PLUGIN_IMAGE_MAX_BYTES = 8 MiB`, and each push shares the
  per-turn callback budget `_PLUGIN_IMAGE_MAX_COUNT` /
  `_PLUGIN_IMAGE_TOTAL_MAX_BYTES`. Images over budget are dropped without
  affecting the rest of the push.
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

- **audio**: `ui_action=media_play_url` plays an audio URL through the
  frontend audio player (the bridge maps the action to a `music_play_url`
  event, which the frontend routes to `dispatchMusicPlay`). **Precondition**:
  the URL must already be on the playback allowlist. `sendMusicMessageDetailed`
  gives it a 500 ms grace window waiting for a `music-allowlist-updated` event;
  if the URL is still not allowlisted it rejects with `unsafe_url` and shows a
  toast. Register it first with `ui_action=media_allowlist_add` (`domains`, or
  exact `http_urls`), then push the playback action.
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
   AND the image reaches the model (`stream_image`), so she replies about it.
3. Replace the part with `{"type": "image", "url": "https://example.com/cat.png"}`:
   the external URL is rejected — nothing renders and the host logs
   `plugin image resolve failed; dropped`. Using the part returned by
   `await ctx.images.upload(<bytes>)` works on both sides.
4. Push `parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}]`
   with `ai_behavior="respond"`: the host logs
   `media_part type=audio not yet supported` and produces nothing; with
   `ai_behavior="blind"` there is no log at all (the media loop is skipped and
   the chat render does not handle audio parts).
