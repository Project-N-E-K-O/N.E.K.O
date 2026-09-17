# Lanlan Gemini item-driven responses

The 2026-09-17 wire trace reproduced a stuck proactive owner: function-call
content carried a different response ID from its terminal. The tool result
remained queued until the 60-second completion timeout closed the connection.
The same trace showed replies arriving before the explicit response.create.

The lanlan_app_gemini capability profile treats user messages and function-call
outputs as generation triggers, including parallel tool results and proactive
prefix batches. The arbiter installs ownership once before writing the first
item, skips the unsupported item-ack wait and does not send
another response.create. Existing completion, cancellation and timeout bounds
still apply. Function-call content cannot bind this route's owner to its
unreliable response ID; the terminal can settle an unannounced owner. Audio/text
content retains ordinary ID matching. Other routes retain explicit creation.

A provider error rejecting the only submitted triggering item fails the ticket
directly, even if more siblings were planned: there is no separate response.create
whose lifecycle needs cancellation. Submission is recorded before entering the
write so synchronous rejection uses the same evidence as asynchronous rejection.
Once multiple items have entered send, the conservative error path remains.

If a batch response terminates before the next item can be submitted, the batch
fails as a partial dispatch and suppresses its unsent tail. It does not report the
first response as success for the whole batch, restart ownership, or send another
generation trigger. This is a bounded failure, not atomic multi-item support:
the provider may already have answered the prefix. A future atomic protocol must
be verified before promising delivery of every sibling in that situation.

Function-call IDs still cannot bind an owner on this profile. However, that does
not justify bypassing transport stale filtering on connections that announce
responses. A first-time delayed call from a cancelled response can otherwise be
assigned the successor's scope at receive time; call-ID deduplication cannot prove
its origin. Ambiguous mismatched calls are quarantined on announcing connections.
This may also drop a legitimate mismatched call there until independent origin
correlation is available. The observed never-announcing proxy retains its normal
tool-dispatch path. Regression tests exercise both cases and strict profiles.

No new queues, timers, dependencies or background network activity are added.
Trace-enabled dispatch reports item_response_sent for this path. Regression
tests are in test_realtime_gemini_item_response.py; also run response/tool
ownership, arbiter cancellation and external text-turn tests. The focused suite
passed 460 tests. On 2026-09-17 the user also reported that live-plugin delivery
and voice conversation worked together after applying the fix. Minecraft tool
delivery remains a separate manual validation scenario.
