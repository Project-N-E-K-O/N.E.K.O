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
Treat its token as private and do not log it.

Registration IDs are idempotent within one call. Use the same original context
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
| `uncertain` | Submission is in progress, or timed out with delivery unknown; inspect `reason` |

`submitted` is not a display acknowledgement and does not provide exactly-once
delivery after disconnect or restart. Cancelling a submitted image reports
`submitted`, rather than claiming the already transmitted image was cancelled.
`uncertain` with `submission_timeout` is a settled unknown-delivery outcome:
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

State is in memory: 128 call scopes, 64 registrations, two images per
registration, 2 MiB serialized payload per registration, 16 MiB retained or
validating payload, four concurrent validations, and 120-second retention.
Capacity exhaustion rejects new work. Expiry reclaims state and never triggers
submission.

The whole reply's attachment submission phase has a five-second deadline,
also capped by each original context's expiry. A stalled write is cancelled
at the deadline so it cannot indefinitely retain image memory or hold reply
cleanup. Status records remain bounded by the normal retention policy.

Retry or discard cancels the old attempt. Interruption cancels incomplete
replies; a new input cannot reassign a completed reply's image to a newer turn.
Reconnect drops pending frontend attachments rather than replaying them under a
different connection.

In compact mode, a visible completed original caption can release the image
while history is closed; the image keeps its original history position. If the
caption has been replaced, presentation waits for the original history anchor
to be visible. No old image is replayed under a newer caption and no new input
is blocked to show a skipped image. This API uses the existing image surfaces;
it does not create a new compact image overlay.
