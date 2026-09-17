# Lanlan Gemini item-driven responses

The 2026-09-17 wire trace reproduced a stuck proactive owner: function-call
content carried a different response ID from its terminal. The tool result
remained queued until the 60-second completion timeout closed the connection.
The same trace showed replies arriving before the explicit response.create.

The lanlan_app_gemini capability profile now treats a single user message or
function-call output as the generation trigger. The arbiter installs ownership
before writing that item, skips the unsupported item-ack wait and does not send
another response.create. Existing completion, cancellation and timeout bounds
still apply. Function-call content cannot bind this route's owner to its
unreliable response ID; the terminal can settle an unannounced owner. Audio/text
content retains ordinary ID matching. Other routes retain explicit creation.

No new queues, timers, dependencies or background network activity are added.
Trace-enabled dispatch reports item_response_sent for this path. Regression
tests are in test_realtime_gemini_item_response.py; also run response/tool
ownership, arbiter cancellation and external text-turn tests. The focused suite
passed 460 tests. On 2026-09-17 the user also reported that live-plugin delivery
and voice conversation worked together after applying the fix. Minecraft tool
delivery remains a separate manual validation scenario.
