# Message Delivery

This page explains how a plugin gets content into the conversation: the two delivery axes of `push_message()`, `parts` types and size limits, image upload, and the delivery modes, result semantics, and role placeholders of `finish()`. For the method list, see the [Plugin Capabilities and SDK Reference](./sdk-reference).

## `push_message(**kwargs) -> PushMessageResult`

`push_message` is the **only** plugin → host message entry point. Two independent axes decide downstream behavior, and an ordered `parts` list carries OpenAI-style multimodal content. The full schema lives in `plugin/sdk/shared/core/push_message_schema.py`.

```python
self.push_message(
    visibility=[],                  # ["chat"] / ["hud"] / ["chat","hud"] / []
    ai_behavior="respond",          # "respond" / "read" / "blind"
    parts=[                         # ordered parts (chat keeps order; see the model-path limits below)
        {"type": "text",  "text": "Look at this"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
        {"type": "ui_action",
         "action": "media_play_url",
         "url": "https://example.com/song.mp3",
         "media_type": "audio"},
    ],
    source="my_feature",
    target_lanlan="Ling",           # optional: route to a specific session
    metadata={...},
    priority=0,                     # higher number = higher priority
)
```

### Submission result

The return value is a local submission receipt:

```python
result = self.push_message(
    visibility=[],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "Please respond to this event"}],
)
if not result["submitted"]:
    # Keep local work; your plugin decides whether and when to retry.
    # Never log the message body.
    self.logger.warning("message submission failed: %s", result.get("reason"))
```

`submitted=True` means only that the SDK handed the payload to its authoritative local submission path and took over responsibility for it. It does not mean the host consumed it, the AI replied, or audio played.

`submitted=False` carries a stable `reason`: `backpressure`, `transport_error`, `transport_unavailable`, or `payload_too_large`. The first three describe transport conditions at that moment (congestion / send failure / no channel), so resending the same payload later is meaningful. `payload_too_large` is different: the SDK measures it locally **before sending** — the packed payload exceeded `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` minus a 227-byte host headroom (see [Size limits](#size-limits)) — so an identical retry will always fail the same way. The only fix is a smaller push: move inline images to `ctx.images.upload()` and send the returned URL part instead; `audio` / `video` have no upload API yet, so either shrink them yourself (shorter clips, lower bitrate or resolution) or host them yourself and pass `url=` instead of `data=`.

Results never expose internal transport names and never echo the message body or exception text. Rejections also carry `ok=False` for legacy callers; new code should treat `submitted` as authoritative. Retry and deduplication remain plugin policy.

### The two axes

**`visibility`** — where the plugin's parts are rendered verbatim to the user (**independent of the AI**). Values can be combined, e.g. `["chat", "hud"]`:

| Value | Meaning |
|---|---|
| `"chat"` | Parts render **verbatim** in the chat window as a chat bubble |
| `"hud"`  | Parts show in the agent UI / HUD notification panel |
| `[]` (default) | The user does **not** see the parts directly; with `ai_behavior="respond"` they see the AI's reply |

**`ai_behavior`** — how the LLM treats the parts:

| Value | In LLM context | Starts a reply turn | When it is mentioned |
|---|---|---|---|
| `"respond"` (default) | ✅ | ✅ immediately | The AI responds right away |
| `"read"`  | ✅ | ❌ no interruption | The AI mentions it naturally on the next user turn |
| `"blind"` | ❌ | ❌ | The AI never sees it |

### `parts` types

Each part is a dict with a `type` discriminator:

| `type` | Fields | Purpose |
|---|---|---|
| `text` | `text: str` | Plain text |
| `image` | `data: bytes` + `mime`, or `url` + `mime` | Image (inline or remote) |
| `audio` | same as image | Audio (schema placeholder; **not consumed by the AI injection path yet**, warn-dropped) |
| `video` | same as image | Video (schema placeholder; **not consumed by the AI injection path yet**, warn-dropped) |
| `ui_action` | `action: str` + per-action fields | Frontend UI side effect |

Inline `data: bytes` is base64-encoded by the SDK. To extend push_message, add a new part `type`; do not add new `message_type` values.

### Current limitations

- With `ai_behavior in ("respond","read")`, inline `image` parts and the local temporary URLs returned by `ctx.images.upload()` both reach the LLM context (ultimately via `session.stream_image(base64)`). Arbitrary external URLs are rejected so remote fetching stays out of the agent event delivery path. At most 8 images totaling 8 MiB are injected into the model per message; extra image parts can still be displayed according to `visibility`, but do not enter model context.
- The 8 images / 8 MiB figure is a **per-push** ceiling. In text mode, `ai_behavior="read"` images are staged until the user's next turn rather than sent immediately, so **one turn** can carry several pushes' worth. Staging is budgeted per source, and sources never eat each other's quota:

  | Source | Images | Bytes |
  | --- | --- | --- |
  | The user's own screenshots / camera frames | 5 | 16 MiB |
  | Plugin `read` images | 3 | 8 MiB |
  | Leftover proactive-chat screenshot | 1 (dedicated slot with TTL) | — |

  When over quota, the **oldest image from the same source** is trimmed — a noisy plugin cannot evict the user's frames, and vice versa. A turn can therefore carry up to 9 images, one more than a single push. That is intentional: a shared total would force choosing which source to sacrifice, and there is no right answer.
- `parts` order is preserved in **chat rendering**. The **model path does not preserve order**: images are extracted and injected first, and text is merged into one block afterwards. Do not rely on "caption A, image A, caption B, image B" interleaving to pair captions with images — put the pairing into one text part instead ("the first image is…, the second is…").
- `visibility=["chat"]` displays image parts; HUD notifications currently render only text parts.
- `audio` / `video` have no realtime injection channel today (`stream_audio` is for live PCM microphone input, and there is no video API), so both are warn-dropped. For now, use them only with `ai_behavior="blind"` + a `ui_action` for frontend-only presentation; a Hosted UI panel can also play media itself.

### Size limits

Inline parts travel over ZMQ through the message plane. The whole payload is capped by `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` = 524288 (512 **KiB** = 512×1024, not decimal 512 KB). That is the shipped default; a deployment can override it with the `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` environment variable, and every figure below assumes the default. The limit applies to the **packed** envelope, not the raw image: inline images are base64 in `parts[].binary_base64`, i.e. 4/3 of the raw bytes (+33%). The raw ceiling for one inline image is therefore 512 KiB × 3/4 ≈ **384 KiB**, minus a few hundred bytes of envelope and any text parts. Measured: a 256 KiB image plus a short sentence packs to 341.8 KiB, leaving about 170 KiB of headroom; the raw size that exactly fills the 524288-byte cap is about 383.6 KiB. The local check (below) holds back a further 227 bytes of headroom, so the practical cutoff for that shape sits about 0.2 KiB lower — size against roughly 383 KiB, not the last decimal.

That effective ceiling used to be much lower. To serve downstream consumers not yet migrated to v2, the wire envelope once carried each inline image **twice** — base64 in `parts[].binary_base64` and raw bytes in the legacy `binary_data` field — about 2.34× the source, so the old 256 KiB cap only fit images of roughly 110 KiB. `_build_wire_payload` (`plugin/core/context.py`) now fills the legacy field only when the caller passes both `parts=` and `binary_data=`; ordinary inline images travel base64 once. Dropping the duplicate and raising the cap to 512 KiB together are what let a 256 KiB image fit.

Oversized payloads are **rejected locally**: `push_message()` packs and measures before handing anything to the message plane, and returns `submitted=False` with `reason="payload_too_large"` when the packed size exceeds the cap minus a 227-byte headroom (the host adds `plugin_id` / `message_id` / `time` fields to the record before measuring it; the headroom covers their worst case, so a push the SDK accepts is never dropped at ingest for size). Nothing goes on the wire, and the log names which part (`image` / `audio` / `video`) consumed the budget. The plugin side and the host side (`plugin/message_plane/ingest_server.py`) read the same constant; the host check stays as a final safeguard.

- The gate sits on **every** submission exit: the batch fast path, the synchronous path, and the legacy control-plane queue used when ZMQ is unavailable.
- It measures the whole payload, not only inline images — oversized text or metadata is rejected too. The only exception is a deployment that explicitly disables `NEKO_MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES` (on by default); then neither the SDK nor the host measures.
- Rejection affects the **whole push**: text parts and `ui_action`s in the same message are rejected with the image.
- This does not change what `submitted=True` means: host backpressure or a process restart can still drop a message silently.

Do not push a 1080p screenshot inline. Compress to JPEG q70 or a 256×256 PNG, or use the upload API below.

### Image upload: `ctx.images.upload()`

Larger images use a separate temporary upload API. It normalizes the image in a thread pool to a JPEG whose longest edge is at most 2048, uploads it over a separate media transport, and does not use the 512 KiB `push_message` payload:

```python
image_part = await self.ctx.images.upload(image_bytes, mime="image/png")
self.push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "Take a look"}, image_part],
)
```

`images.upload()` only prepares a temporary resource for the current runtime. It does not display the image, write model context, or trigger a reply; delivery is still controlled entirely by `push_message()`. Call it from a plugin entry, timer, message, or custom event handler. During lifecycle handlers (`startup` / `freeze` / `unfreeze` / `shutdown` / `config_change`) it raises `RuntimeError` immediately instead of waiting for a timeout.

**Hard failures of `upload()`** — each of these **raises**; none silently degrades to a smaller image, so wrap user-supplied images in `try`:

| Gate | Constant / rule | Result when exceeded |
|---|---|---|
| Source bytes | `MAX_SOURCE_IMAGE_BYTES` = 33554432 (32 MiB) | `ValueError` |
| Source pixels | `MAX_SOURCE_IMAGE_PIXELS` = 16777216 (16 MP, width × height) | `ValueError` |
| Normalized bytes | `MAX_UPLOADED_IMAGE_BYTES` = 8388608 (8 MiB) | `ValueError` |
| Decode slot wait | Only 2 decode slots per process; queueing counts against your `timeout` (default 3s, max 30s) | `TimeoutError` |
| Output longest edge | `MAX_IMAGE_EDGE` = 2048 | No error; scaled down proportionally |

The normalized-bytes gate is easy to miss: a source that passes 32 MiB / 16 MP can still re-encode to a JPEG over 8 MiB (noisy large images compress poorly), and that error is raised **after** decoding, so the CPU is already spent. A downlink that is not ready or a transport that never replies is also `TimeoutError`; empty bytes are `ValueError`, and non-bytes are `TypeError`.

**Animated images are flattened**: only frame 0 is kept and the output is always a single-frame JPEG, with no error or log. The `mime=` argument is only the caller's label; Pillow detects the real format and the output is always JPEG. For animation, let the frontend play it via a `ui_action`.

**Higher resolution buys chat display quality, not model accuracy**: every model-bound image is **unconditionally** re-encoded to the model profile first — width at most `MODEL_IMAGE_MAX_WIDTH` (1280), height at most `COMPRESS_TARGET_HEIGHT` (720), JPEG q80 — see `normalize_image_for_model` in `utils/screenshot_utils.py`. Images already within the profile pass through unchanged and are never re-encoded repeatedly. An image uploaded at a 2048 edge is therefore 720p-class by the time the model sees it. Only if a turn's images still exceed `TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES` (8 MiB) after that does the degradation ladder start: sample down to head / middle / tail, recompress the survivors once more, and only then drop the oldest — and the user is told only when a whole image was actually dropped. The chat-display copy skips all of this and keeps your uploaded resolution.

### Common combinations

```python
# 1. Let the AI relay it (most common): plugin text → LLM context → immediate reply
self.push_message(parts=[{"type": "text", "text": "The user tipped you 100"}])

# 2. Quietly inform the AI: mentioned naturally on the next user turn
self.push_message(
    visibility=[],
    ai_behavior="read",
    parts=[{"type": "text", "text": "New danmaku arrived in the live room"}],
)

# 3. Notify the user in the HUD only, without involving the AI
self.push_message(
    visibility=["hud"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "Plugin X started"}],
)

# 4. Render a playback card directly in chat; the AI is unaware
self.push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{
        "type": "ui_action",
        "action": "media_play_url",
        "url": "https://music.example.com/song.mp3",
        "media_type": "audio",
        "name": "Test Song",
        "artist": "Bot",
    }],
)

# 5. Text + image to the AI together
self.push_message(
    parts=[
        {"type": "text",  "text": "Look at this image"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
    ],
)
```

The v1 fields (`message_type`, `content`, `delivery`, `reply`, and others) are deprecated; they are still translated and emit `DeprecationWarning`. For field-by-field replacements see the [`push_message` v2 notes](/changelog/plugin-push-message-v2) and the [migration guide](./migration-v0.9#push-message-v2).

## Music playback domain allowlist

By default N.E.K.O only plays audio from known-safe domains such as `music.163.com`. A plugin that introduces a new audio source must allowlist its domain before playback.

### Backend plugin (Python SDK)

Push a `ui_action: media_allowlist_add`, then start playback:

```python
@plugin_entry(id="play_external")
async def play_external(self, url: str, **_):
    self.push_message(
        ai_behavior="blind",
        # Allowlist your own fixed source, never the caller-supplied url: an
        # LLM-chosen url here would bypass the allowlist entirely. Validate
        # url against that source before playing it.
        parts=[{"type": "ui_action", "action": "media_allowlist_add", "domains": ["music.my-cdn.com"]}],
    )
    self.push_message(
        visibility=["chat"],
        ai_behavior="blind",
        parts=[{"type": "ui_action", "action": "media_play_url", "url": url, "media_type": "audio"}],
    )
    return Ok({"status": "playing", "url": url})
```

The old SDK helper `self.register_music_domains(...)` has been removed.

### Plugin page (JavaScript)

Pages served via `register_static_ui` can use the global `window.MusicPluginAPI`:

| Method | Arguments | Description |
|------|------|------|
| `getAllowlist()` | none | Returns all currently allowed domains/IPs |
| `addAllowlist(input)` | `string \| Array<string>` | Adds domains. Extracts hosts from URLs and deduplicates |

A plugin page may be embedded in an `<iframe>` with unpredictable load timing. `music-ui-ready` is dispatched **once** on the host page's `window` (the iframe's `window.parent`) and is not replayed, so check for the API first and only wait for the event if it is not there yet:

```javascript
const domains = ['my-music-cdn.com', 'https://safe-storage.org/stream/'];
const getMusicApi = () => window.MusicPluginAPI || (window.parent && window.parent.MusicPluginAPI);

const api = getMusicApi();
if (api) {
    api.addAllowlist(domains);
} else {
    window.parent.addEventListener('music-ui-ready', () => {
        const readyApi = getMusicApi();
        if (readyApi) readyApi.addAllowlist(domains);
    }, { once: true });
}
```

## `finish()` delivery modes

`finish()` uses `delivery` to control how a task result reaches the main AI (default `"proactive"`):

| `delivery` | In LLM context | Starts a turn | Frontend HUD / notification |
|---|---|---|---|
| `"proactive"` (default) | ✅ | ✅ immediately | ✅ |
| `"passive"` | ✅ written to context | ❌ no interruption; mentioned on the next user turn | ✅ |
| `"silent"` | ❌ AI unaware | ❌ | ✅ (task_update only) |

```python
# Normal: the character reports the result immediately
return await self.finish(data={"summary": "Sunny today"})

# Quiet: enters context without interrupting; mentioned next time the user speaks
return await self.finish(data={"summary": "Pomodoro finished"}, delivery="passive")

# Fully silent: the AI never knows; the frontend sees only the task_update
return await self.finish(data={"summary": "..."}, delivery="silent")
```

`reply=True` / `reply=False` is the deprecated boolean alias for `"proactive"` / `"silent"`; when both are passed, `delivery` wins. `push_message()` does not use `delivery`; it uses the `visibility` + `ai_behavior` axes.

## Result semantics: task report vs event

When the main AI receives a notification, the host wraps it in an outer prompt with one of two framings:

| You call | Host classification | Gist of the outer prompt |
|---|---|---|
| `await self.finish(...)` (default) | `task_result` | "A task from {your plugin} finished; **report** it to the master..." |
| A query / instant-receipt entry declaring `result_kind="event"` | `event` | "**New message** from {your plugin}; **respond** to the master based on it..." |
| `self.push_message(...)` | `event` | "**New message** from {your plugin}; **respond** to the master based on it..." |

- Task report: the plugin was invoked and finished; the AI should say what was done and how it turned out.
- Event stream: the plugin listens to external events (danmaku, IM messages, timers); the AI should **respond to the event itself**, not narrate "I just handled something…".

Queries, status reads, and "started" receipts should explicitly downgrade to event semantics. Declare it statically:

```python
@plugin_entry(
    id="service_status",
    metadata={"result_kind": "event", "expires_in_s": 30},
)
```

Or override it for one run:

```python
return await self.finish(
    data={"status": "running"},
    meta={"agent": {"result_kind": "event", "expires_in_s": 10}},
)
```

Resolution order is runtime `meta.agent` > static entry `metadata` > default `task_result`. `expires_in_s` applies only to `event` results; expired receipts no longer reach the main AI. The host only lets a successful `user_plugin task_result` downgrade to `event`; a `proactive_message` cannot masquerade as a task completion. `result_kind` is orthogonal to `visibility` / `ai_behavior` / `delivery` and only changes the outer prompt wording.

## Role-aware text: `{MASTER_NAME}` / `{LANLAN_NAME}`

Plugins put strings into the conversation LLM context through `finish()`'s `data.summary` / `data.detail`, `push_message()`'s `parts[*].text`, and similar channels (or straight into TTS / chat bubbles with `direct_reply`). **Do not hardcode** `"user"` / `"master"` / `"用户"` / `"主人"` in these strings:

- **Awkward voice**: the AI reads "report to the user…" literally instead of using the actual `master_name`.
- **Multi-character drift**: each `LLMSessionManager` has its own `lanlan_name`; when a plugin broadcasts to several characters, hardcoded text makes all of them use the same wording.

| Placeholder | Replaced with |
|---|---|
| `{MASTER_NAME}` | The session's `master_name` (the name the user chose) |
| `{LANLAN_NAME}` | The session's character name |

The host expands them per target session at the LLM injection point (`main_logic.core.apply_role_placeholders`). **Do not substitute them in the plugin** — visibility filtering happens on the host, so the plugin cannot know which session a message lands in.

```python
# ✅ Recommended: let the host expand per session
await self.finish(data={
    "summary": "Immediately describe what just happened on screen to {MASTER_NAME}",
})

self.push_message(parts=[{
    "type": "text",
    "text": "{MASTER_NAME} just sent a danmaku: '...'",
}], ai_behavior="respond")

# ❌ Not recommended: hardcoded, generic voice + multi-character drift
await self.finish(data={"summary": "Immediately describe to the user..."})
```

Implementation details:

- **Substitution is `str.replace`, not `str.format`**: JSON fragments, code, or user text containing `{` inside `detail` never raise `KeyError`.
- **Empty names leave the placeholder literal**: if the host has no name yet (rare, during initialization), `{MASTER_NAME}` stays as-is rather than becoming an empty string that breaks the sentence.
- **Fixed spelling**: use only `{MASTER_NAME}` / `{LANLAN_NAME}` (uppercase, underscore, single braces). `{master_name}` / `{master}` / `{MASTER}` are `.format(...)` placeholders of internal host templates and do not cross the plugin boundary.

Scope:

| Channel | Expanded |
|---|---|
| `finish(data={"summary": ..., "detail": ...})` | ✅ |
| `push_message(parts=[{"type": "text", ...}])` into LLM context | ✅ |
| `task_result` + `direct_reply=True` (straight to TTS, bypassing the LLM) | ✅ |
| `push_message(visibility=["chat"], ai_behavior="blind")` straight into a chat bubble | ✅ |
| `push_message(visibility=["hud"])` HUD toast text | ✅ |
| Static description fields (`description` in `plugin.toml`, entry `name`, etc.) | ❌ not expanded (for developers / UI, not conversation channels) |
