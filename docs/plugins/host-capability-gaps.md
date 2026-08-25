# Plugin Host Capability Gaps: No User-Visible Image Channel + Dropped Audio/Video Parts

> This PR summarizes host-side capability gaps that plugins **cannot work around**
> themselves (verified in `app/main_server/character_runtime.py` source; line
> numbers reference `main` at the time of this PR's last commit). It cross-checks
> the "schema-placeholder parts" section of `plugin/PLUGIN_DEVELOPMENT_GUIDE.md`.
>
> Plugin-side solvable adaptation issues (double-reply, session interruption,
> message listening, background photo gating, missing `target_lanlan`, etc.) were
> already fixed inside the community plugins themselves and are out of scope here.

## Issue 1: Image parts have no "user-visible in chat" channel

**Symptom**: When a plugin pushes an image part with
`visibility=["chat"]`, `ai_behavior="blind"`, the user **cannot see** the image
in the chat window; `visibility=["chat"]` only applies to text parts.

```python
push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)
```

**Root cause** (source-verified):

- `app/main_server/character_runtime.py` collects media parts into
  `media_parts` carried on the `proactive_message` event to the AI session;
  only when `ai_behavior in ("respond", "read")` does it feed **inline**
  images to the LLM via `stream_image` (vision input). **No branch renders an
  image part into the user chat window.**
- **inline vs url**: only inline `binary_base64` reaches `stream_image`; the
  `url` form is warn-dropped in `character_runtime.py`
  ("image media_part url=... not yet fetched; dropped"), reaching neither the
  AI nor the user window. The official guide likewise recommends inlining small
  images (≤256KB).
- The only currently viable user-visible path is a text part embedding markdown
  `![alt](http://...)` (rendered by the frontend ReactMarkdown), with the URL
  reachable from the client — plugins can only approximate this via their own
  static server + URL assembly, which does not remove the underlying host-gap.

**Impact**: Game/tool plugins have no native channel to let the user "see" an
image (result cards, photo albums, screenshots). Verified community patterns
split into two strategies:

- lifekit: converts image blocks to markdown and pushes a text part with
  `visibility=["chat"]` + `ai_behavior="blind"` (user-visible, depends on
  external hosting);
- neko_live: avatar image as `visibility=[]` + inline image part (not
  user-visible, AI-vision only).

**Expectation**: `visibility=["chat"]` + an image part (at least inline
`binary_base64`, ideally also `url`) should render directly as an image bubble
in the user chat window (same channel as text parts); `ai_behavior` should only
control whether the image is also injected into AI context, not user
visibility.

**Related**: This aligns with #2835 / #2905 (host-side image visibility work);
recommend implementing the channel officially after this PR.

## Issue 2: audio / video parts are dropped by the host

**Symptom**: Pushing an audio/video part produces no output; only when
`ai_behavior in ("respond", "read")` does the host enter the media loop and
warn-drop non-image parts. With `ai_behavior="blind"` the whole media path is
skipped — **completely silent** (no warning).

```python
# respond/read: host enters media loop, warns and drops audio/video
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}],
)
```

**Root cause** (source-verified):

- `app/main_server/character_runtime.py`: the media loop is guarded as a whole
  by `ai_behavior in ("respond", "read")`; inside the loop, parts with
  `part_type != "image"` hit `logger.warning("media_part type=%s not yet
  supported ... dropped")`, with a comment that `stream_audio` is a realtime
  microphone PCM pipeline (specific sample rate + RNNoise gating), not a
  generic file injector, and there is no video API.
- So `{"type": "audio"}` / `{"type": "video"}` parts are defined in the v2
  schema (see `plugin/sdk/shared/core/push_message_schema.py` and the official
  guide) but the host consumption side is not implemented; pushing them has no
  effect (`blind` produces no diagnostics at all).

**Impact**: Plugins cannot push voice/video content into the session (TTS audio
files, music clips, game cutscenes). Verified workarounds:

- **audio**: `ui_action=media_play_url` (with `media_type: "audio"`) plays an
  audio URL through the frontend audio player (the bridge maps the action to a
  `music_play_url` event; the frontend routes it to `dispatchMusicPlay`);
- **video**: there is no URL playback path for video today (no frontend video
  event channel), so no workaround exists.

**Expectation**: Provide a host-side consumer for audio/video parts (audio may
be injected into the session or forwarded to the frontend player; video should
at least support url form).

---

### Verification

1. Push `push_message(visibility=["chat"], ai_behavior="blind",
   parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}])`:
   expected an image bubble in the user chat window; none appears.
2. Same payload with `ai_behavior="respond"`: inline image reaches AI context
   (`stream_image`), but the user chat window still shows nothing.
3. Replace the part with `{"type": "image", "url": <png url>, "mime":
   "image/png"}` (`ai_behavior="respond"`): host logs
   `image media_part url=... not yet fetched; dropped`; neither AI nor user
   sees it.
4. Push `parts=[{"type": "audio", "data": <wav bytes>, "mime":
   "audio/wav"}]` with `ai_behavior="respond"`: host logs
   `media_part type=audio not yet supported`, no output; with
   `ai_behavior="blind"` there is no log at all (media path skipped).
