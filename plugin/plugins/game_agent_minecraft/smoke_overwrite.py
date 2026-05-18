"""Overwrite-path smoke test for game_agent_minecraft.

Validates the remaining LLM-visible status codes that the main
smoke_local.py can't reach with a single tool call:

* ``{result: "busy", ...}`` — second minecraft_task while first is
  still running, ``overwrite=False`` (default).
* ``{status: "interrupted", ...}`` — second task with ``overwrite=True``
  wakes the first task's handler with the interrupted verdict before
  taking over the slot.

Wire shape mirrors ``smoke_local.py``: standalone, fake push_message,
talks to the real running mc-agent at ws://localhost:48909.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugin.plugins.game_agent_minecraft.service import GameAgentService  # noqa: E402
from plugin.plugins.game_agent_minecraft.smoke_local import (  # noqa: E402
    _PushMessageRecorder,
    _build_logger,
    _wait_connected,
)


async def main() -> int:
    recorder = _PushMessageRecorder()
    svc = GameAgentService(logger=_build_logger(), push_message_fn=recorder)
    svc.configure({"ws_url": "ws://localhost:48909", "task_timeout_seconds": 120.0})
    await svc.start()
    try:
        if not await _wait_connected(svc, 10.0):
            print("[smoke-ow] ❌ WS not connected")
            return 2

        # First task: long-ish so we can race it. Use newAction wrapper so
        # the agent commits to multi-step work that wouldn't naturally
        # complete inside our race window.
        long_task = "Walk slowly in a wide circle around your current position for at least 30 seconds, reporting what you see along the way."
        short_task = "Just stop moving and say 'stopped.' Nothing else."

        # T1 in the background — we don't await it here so we can race a
        # second call against it while it's still pending.
        async def fire_long():
            return await svc.execute_minecraft_task(task=long_task)

        t1 = asyncio.create_task(fire_long(), name="smoke-ow.long_task")

        # Give the service ~2s to register T1 as pending. Without this,
        # the second call's pending-slot race may not be deterministic
        # (the WS send_task await is itself a suspension point).
        await asyncio.sleep(2.0)
        print(f"[smoke-ow] after 2s, svc status = {svc.get_status()}")

        # T2 with overwrite=False → expect {result: "busy", ...}
        t0 = time.time()
        busy = await svc.execute_minecraft_task(task=short_task, overwrite=False)
        print(f"[smoke-ow] T2 (overwrite=False) after {time.time()-t0:.2f}s: {busy}")
        assert busy.get("result") == "busy", f"expected busy, got {busy}"

        # T3 with overwrite=True → expect T1 to wake with status=interrupted
        # and T3 itself to run to completion.
        t0 = time.time()
        t3 = await svc.execute_minecraft_task(task=short_task, overwrite=True)
        print(f"[smoke-ow] T3 (overwrite=True) after {time.time()-t0:.2f}s: {t3}")

        # T1 should have woken up with interrupted by now.
        t1_result = await asyncio.wait_for(t1, timeout=5.0)
        print(f"[smoke-ow] T1 result: {t1_result}")
        assert t1_result.get("status") == "interrupted", \
            f"expected T1=interrupted, got {t1_result}"

        print("[smoke-ow] ✅ all overwrite-path assertions passed")
        print(f"[smoke-ow] push_message calls during test: {len(recorder.calls)}")
        return 0
    finally:
        await svc.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
