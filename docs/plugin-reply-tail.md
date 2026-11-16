# Original Reply Tail Images (v1)

Ordinary text-chat tools can opt into display-only images placed after their
original assistant reply. The host waits for successful generation, the original
sentence queue and committed text presentation, including the compact caption.
This is a text presentation contract, not an audio playback completion contract.

## Plugin Usage

```python
from plugin.sdk.plugin import llm_tool

@llm_tool(name="choose_image", reply_tail=True)
async def choose_image(self, _ctx=None):
    original = (_ctx or {}).get("host_reply")
    if original is None:
        return {"supported": False}
    result = await self.ctx.reply_tail.register(
        original,
        registration_id="chosen-image",
        parts=[{"type": "image", "data": image_bytes, "mime": "image/gif"}],
    )
    return {"status": result["status"]}
```

`image_bytes` should already be selected by the plugin. Registration must return
promptly; never wait inside the tool for the reply to finish. The model is still
waiting for the tool before it can continue its text.

The handler must accept `_ctx` or `**kwargs`. `_ctx.host_reply` is reserved host
context, captured before awaiting the original tool invocation. Do not build it
from model parameters, logs, the latest role, or the latest active reply. The
credential binds the plugin source, role, reply, request, call and retry attempt.
Every invocation gets its own context, even when a provider reuses a call ID
within one reply. Treat its token as private and do not log it.

Registration IDs are idempotent within one invocation. Use the same original context
and ID for `self.ctx.reply_tail.status(...)` and `.cancel(...)`. Do not
immediately resend on timeout: the host might already have accepted the image.
The SDK deliberately does not retry or fall back to `push_message`.

## Receipts

| Status | Meaning |
| --- | --- |
| `registered` | Validated and retained for the original reply; not sent yet |
| `submitted` | Submitted to the original WebSocket, with `submitted_at` |
| `cancelled` | Removed before submission |
| `failed` | Rejected or transport failed; inspect `reason` |
| `uncertain` | Submission is in progress, or timed out/interrupted with delivery unknown; inspect `reason` |

`submitted` is not a display acknowledgement and does not provide exactly-once
delivery after disconnect or restart. Cancelling a submitted image reports
`submitted`, rather than claiming the already transmitted image was cancelled.
`uncertain` with `submission_timeout` or `submission_interrupted` is a settled unknown-delivery outcome:
image memory is released, remaining unsent attachments are cancelled, and the
host does not retry. Reusing the same registration ID does not resubmit it.

## Scope And Limits

The capability requires both an opted-in tool and a frontend advertising v1.
Old tools, ordinary plugin messages, realtime voice, ASR, proactive replies,
Agent and panel sends keep their existing paths. Context absence is a capability
result; a plugin may choose its documented old-host behavior before registration.

Only `blind` image parts are accepted. Inline bytes use the existing display
part encoding and validator, preserving GIF animation. Host-minted image
references are also supported. Arbitrary remote images and `ToolResult.images`
are not part of this API; the latter feeds model vision.

## Plugin Decisions After A Reply

A plugin can subscribe to successful ordinary text replies without exposing an
LLM tool. The host invokes a regular plugin entry once the reply has completed;
the plugin decides whether to attach an image using its own rules, retrieval or
auxiliary model. The main conversation model does not have to request an image.

```python
from plugin.sdk.plugin import lifecycle, plugin_entry

@lifecycle(id="startup")
async def startup(self):
    result = await self.ctx.reply_tail.subscribe("after_reply", handler_timeout=15.0)
    if not result["ok"]:
        raise RuntimeError("reply completion subscription failed")

@plugin_entry(id="after_reply", kind="hook")
async def after_reply(self, text: str, user_text: str,
                      has_attachments: bool, _ctx=None):
    original = (_ctx or {}).get("host_reply")
    if original is None or has_attachments:
        return
    # Plugin-owned selection; returning None deliberately means no image.
    image = await self.select_image(user_text, text)
    if image is None:
        return
    await self.ctx.reply_tail.register(
        original, registration_id="selected",
        parts=[{"type": "image", "data": image, "mime": "image/gif"}],
    )
```

`text` is the retained reply text, including pre-tool text; `user_text` is the
original visible input captured for that request. Neither is a system prompt.
`has_attachments` reports this plugin's already registered, submitted or uncertain
attachments for the reply, allowing the plugin to avoid adding a second image
after its own tool has already chosen one. It does not suppress other plugins.
`_ctx.host_reply` is a new, private invocation scope bound to this callback and
the original request. Register before returning: that scope accepts images only
while its callback is running. A late background registration is rejected.
Handler return values are ignored and never cause a model continuation.

Pass `role="Character"` to subscribe only to that character. Calling `subscribe`
again for the same plugin and entry replaces its subscription and cancels the old
callback work. Call `await self.ctx.reply_tail.unsubscribe("after_reply")` to stop
it. Plugin stop uses the existing source cleanup to remove subscriptions and
cancel pending callback work, including plugins that register no LLM tools.
If a callback finds the plugin absent or its process no longer alive, the plugin
service returns HTTP 404 with `detail.code="PLUGIN_NOT_RUNNING"`. The host removes
that subscription and cancels its pending callbacks and unsent images. This check
happens on callback dispatch, not immediately when a process exits. An old callback
cannot remove a replacement subscription. Other 404 responses, timeouts and
handler errors do not automatically unsubscribe a plugin.

Subscriptions live only in main-server memory. There is no main-server restart
notification or automatic subscription replay in this API. If only the main server
restarts while plugins remain running, their subscriptions are lost: reload or
restart the affected plugins so their startup code subscribes again. This has the
same recovery limitation as the existing plugin tool registration mechanism.
Older hosts without this endpoint return HTTP 404; plugins should detect that
before choosing their documented compatibility behavior.

Subscribers are captured when generation starts. Failed, discarded, interrupted,
empty and synthetic-input replies do not notify; a retry that succeeds notifies
once with the retained attempt. Long-reply truncation that keeps a valid reply
does notify. Realtime voice, ASR, proactive chat and Agent-only replies are outside
this hook. Frontend v1 capability is required, just as for tool attachments.

Callbacks run in bounded background tasks after the normal turn end, so selecting
an image does not delay normal reply cleanup or the next input. There are at most
eight subscriptions globally, at most two per plugin, and eight simultaneous
callbacks globally. A new subscription exceeding these limits returns
`{"ok": false, "reason": "capacity"}` or `"reason": "plugin_capacity"`;
replacing an existing subscription does not consume another slot. These limits
bound resources; they do not guarantee fair callback scheduling across plugins.
Each callback has a total
deadline of at most 15 seconds, followed by the existing bounded image submission
phase. Input and reply text are each limited to 65,536 characters; oversized
content is skipped as a whole, rather than truncating a possible output restriction.
Capacity skips and failures log metadata only. There is no retry or replay.

Multiple plugins select images concurrently. Each callback submits its images
after it returns successfully, without waiting for other callbacks. Cross-plugin
image order follows arrival at the frontend, not subscription order or plugin
name; concurrent completion and transport timing can affect it. Images retain
their original reply anchor. Tool attachments already submitted before callbacks
run use the existing ordering path.

One failing or timed-out plugin loses only its own unsent callback attachments.
A completed reply remains the original anchor if a later input arrives; a
reconnect rejects attachments for the previous connection. As with tool
attachments, this is a bounded best-effort delivery contract, not an exactly-once
display guarantee. The hook makes the decision independent of main-model tool
use; it does not force the plugin to select an image on every reply.

## State Limits

State is in memory: 128 invocation scopes, 64 registrations still holding
images, two images per registration, 2 MiB serialized payload per registration,
16 MiB retained or validating payload, four concurrent validations, and
120-second retention. Settled receipts stay for status queries within a
separate bound of 256: past it, and past the scope bound, the oldest receipts
and scopes of closed replies holding no image are evicted first. Capacity
exhaustion rejects new work. Expiry reclaims state and
never triggers submission.
The retention of an invocation's context starts at the tool invocation, so a
reply that keeps generating for more than 120 seconds after it loses that
invocation's images as `expired`.
Frontend pending images also have a one-shot expiry check, so reclamation does
not require a later chat event. Display and reconnect cancel that check. An
image whose original text stays hidden for the whole 120 seconds (the chat
collapsed, or in compact mode history closed while a newer caption replaced
the original) is dropped at expiry and does not enter history either.

The whole reply's attachment submission phase has a five-second deadline,
also capped by each original context's expiry. A stalled write is cancelled
at the deadline so it cannot indefinitely retain image memory or hold reply
cleanup. Status records remain bounded by the normal retention policy.

A reply counts as successful whenever its shown text is kept as the reply,
including the long-reply truncation recovery. Retry or discard cancels the old
attempt. Interruption cancels incomplete
replies; a new input cannot reassign a completed reply's image to a newer turn.
Reconnect drops pending frontend attachments rather than replaying them under a
different connection.

In compact mode, a visible completed original caption can release the image
while history is closed; the image keeps its original history position. If the
caption has been replaced, presentation waits for the original history anchor
to be visible. No old image is replayed under a newer caption and no new input
is blocked to show a skipped image. This API uses the existing image surfaces;
it does not create a new compact image overlay.
