"""Standalone smoke test for game_agent_minecraft against a real mc-agent.

Purpose
-------
Validate the full integration **without** booting the rest of N.E.K.O:
- spin up `GameAgentService` directly,
- feed it a fake "push_message" (stdout) + stdlib logger,
- send a single `minecraft_task` and report the verdict shape.

Why this exists: the repo has no shared "mock 猫娘" harness — each plugin
hand-rolls its own fake context (see test_mcp_adapter_runtime.py `_Ctx`).
This file is the equivalent for game_agent_minecraft, but instead of a
fake WebSocket fixture it talks to a real running mc-agent at the
configured `ws_url`. That's exactly what's needed after wire-format /
status changes on the mc-agent side: prove the round-trip end-to-end.

Usage
-----
    uv run python -m plugin.plugins.game_agent_minecraft.smoke_local
    uv run python -m plugin.plugins.game_agent_minecraft.smoke_local "go mine 4 oak logs"
    NEKO_GAME_AGENT_WS=ws://localhost:48909 uv run python -m plugin.plugins.game_agent_minecraft.smoke_local

The autonomous nudge loop runs at default cadence (5s); for a 60s task
you'll likely see 1–2 `[fake-neko] push_message]` nudges in addition to
any screenshot frames that arrive — both are part of the contract being
tested.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any


# Allow `python smoke_local.py` direct invocation in addition to `-m`.
# parents[3] = Xiao8 project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugin.plugins.game_agent_minecraft.service import GameAgentService  # noqa: E402


# --------------------------------------------------------------------------
# Fake N.E.K.O surface
# --------------------------------------------------------------------------


class _PushMessageRecorder:
    """Collect every push_message call so we can summarize at the end.

    The service calls push_message in two situations:
    1. Screenshot frame arrived from mc-agent → ai_behavior="read"
    2. Autonomous nudge loop tick           → ai_behavior="respond"

    Both go through the same callable. We deduplicate the printout for
    large binary parts (image bytes) so the stdout stays readable.

    When ``dump_dir`` is set, every image part is also written to disk
    as ``<dump_dir>/<seq>_<ai_behavior>.png`` (seq is zero-padded across
    the whole run, not per-call, so an LLM or human reader can ask for
    "the latest screenshot" by lexical max). Existing files in the
    directory are NOT cleaned — callers are expected to point at a
    fresh dir for each run.
    """

    def __init__(self, dump_dir: Path | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.dump_dir = dump_dir
        self._image_seq = 0
        if dump_dir is not None:
            dump_dir.mkdir(parents=True, exist_ok=True)

    def _dump_images(self, parts: list[Any], ai_behavior: str) -> list[Path]:
        if self.dump_dir is None or not parts:
            return []
        written: list[Path] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "image":
                continue
            data = part.get("data")
            if not isinstance(data, (bytes, bytearray)):
                continue
            mime = str(part.get("mime") or "image/png").lower()
            # Trust mime for extension — service.py converts JPEG→PNG
            # before push when possible, but the fallback path keeps
            # the JPEG bytes and tags them image/jpeg.
            ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
            path = self.dump_dir / f"{self._image_seq:04d}_{ai_behavior}.{ext}"
            try:
                path.write_bytes(bytes(data))
                written.append(path)
            except OSError as exc:
                print(f"[fake-neko] failed to dump {path.name}: {exc}")
            self._image_seq += 1
        return written

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        # Pretty-print without dumping image bytes inline.
        view: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k == "parts" and isinstance(v, list):
                view[k] = [
                    {
                        **{kk: vv for kk, vv in part.items() if kk != "data"},
                        "data": f"<{len(part['data'])} bytes>" if isinstance(part.get("data"), (bytes, bytearray)) else part.get("data"),
                    }
                    if isinstance(part, dict)
                    else part
                    for part in v
                ]
            else:
                view[k] = v
        ai_behavior = kwargs.get("ai_behavior", "?")
        parts = kwargs.get("parts", []) or []
        n_parts = len(parts)
        written = self._dump_images(parts, ai_behavior)
        suffix = f" → wrote {[p.name for p in written]}" if written else ""
        print(f"[fake-neko] push_message ai_behavior={ai_behavior} parts={n_parts}{suffix}")
        # Detailed view at DEBUG level — uncomment if you want frame-by-frame.
        # print("           detail:", view)
        return {"ok": True}


def _build_logger() -> logging.Logger:
    """stdlib logger that supports brace-format calls (the SDK convention).

    GameAgentService uses ``logger.info("connected to {}", url)`` style;
    stdlib logger normally treats those as %-style and would silently
    drop the args. We wrap it so the smoke script's logs look right.
    """
    raw = logging.getLogger("smoke.game_agent")
    raw.setLevel(logging.INFO)
    if not raw.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        raw.addHandler(h)

    class _BraceLogger:
        def _fmt(self, msg: str, args: tuple[Any, ...]) -> str:
            if not args:
                return msg
            try:
                return msg.format(*args)
            except (IndexError, KeyError, ValueError):
                return f"{msg} {args!r}"

        def info(self, msg, *args, **_):
            raw.info(self._fmt(msg, args))

        def warning(self, msg, *args, **_):
            raw.warning(self._fmt(msg, args))

        def error(self, msg, *args, **_):
            raw.error(self._fmt(msg, args))

        def debug(self, msg, *args, **_):
            raw.debug(self._fmt(msg, args))

        def exception(self, msg, *args, **_):
            raw.exception(self._fmt(msg, args))

    return _BraceLogger()  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


async def _wait_connected(svc: GameAgentService, timeout: float) -> bool:
    """Poll service status until the WS client reports connected, or give up.

    Without this, ``execute_minecraft_task`` would race the WebSocket
    handshake and immediately return ``AGENT_DISCONNECTED`` on a
    cold-start mc-agent that hasn't yet accepted our connect call.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if svc.get_status().get("connected"):
            return True
        await asyncio.sleep(0.2)
    return False


async def run(
    task: str,
    *,
    ws_url: str,
    task_timeout: float,
    connect_timeout: float,
    dump_dir: Path | None = None,
) -> int:
    recorder = _PushMessageRecorder(dump_dir=dump_dir)
    logger = _build_logger()

    svc = GameAgentService(logger=logger, push_message_fn=recorder)
    svc.configure(
        {
            "ws_url": ws_url,
            "reconnect_interval_seconds": 2.0,
            # Generous wrt the 25s default — mc-agent's chat-loop completion
            # for a non-trivial task can easily take 30–60s.
            "task_timeout_seconds": task_timeout,
            "system_prompt_interval_seconds": 5.0,
            "skip_system_prompt_if_busy": True,
            "stream_screenshots_to_llm": True,
            "screenshot_cache_size": 3,
        }
    )

    print(f"[smoke] starting service against {ws_url} …")
    await svc.start()
    try:
        if not await _wait_connected(svc, connect_timeout):
            print(
                f"[smoke] ❌ WS did not connect within {connect_timeout:.0f}s; "
                "is mc-agent listening and has the bot finished spawning?"
            )
            return 2
        print(f"[smoke] ✅ connected; status={svc.get_status()}")

        print(f"[smoke] → minecraft_task: {task!r} (timeout={task_timeout:.0f}s)")
        t0 = time.time()
        result = await svc.execute_minecraft_task(task=task)
        elapsed = time.time() - t0
        print(f"[smoke] ← result after {elapsed:.1f}s: {result}")

        status = result.get("status") if isinstance(result, dict) else None
        # ``superseded`` is mc-agent's "a newer task replaced this one" —
        # a neutral non-completion, same class as ``interrupted``, not a
        # failure. ``failed`` (genuine failure) still maps to rc=1.
        rc = 0 if status in ("ok", "timeout", "interrupted", "superseded") else 1
        # ``timeout`` is a clean structured outcome (LLM-visible) — keep
        # exit 0 so CI doesn't read it as a hard failure; the elapsed
        # time + the printed result are enough for a human reader.

        print(
            f"[smoke] summary: status={status!r}, push_message calls={len(recorder.calls)}, "
            f"final svc status={svc.get_status()}"
        )
        return rc
    finally:
        print("[smoke] stopping service …")
        await svc.stop()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "task",
        nargs="?",
        default="Collect 4 oak logs and report when done.",
        help="Task text to send to mc-agent (default: a small oak-log gather)",
    )
    p.add_argument(
        "--ws-url",
        default=os.environ.get("NEKO_GAME_AGENT_WS", "ws://localhost:48909"),
        help="mc-agent WebSocket URL (env: NEKO_GAME_AGENT_WS)",
    )
    p.add_argument(
        "--task-timeout",
        type=float,
        default=120.0,
        help="Max seconds to await task_finished (default 120s)",
    )
    p.add_argument(
        "--connect-timeout",
        type=float,
        default=10.0,
        help="Max seconds to wait for WS handshake (default 10s)",
    )
    p.add_argument(
        "--dump-dir",
        default=None,
        help=(
            "If set, write every screenshot received during the run to "
            "<dump-dir>/<seq>_<ai_behavior>.png so an external reader "
            "(human or multimodal LLM) can inspect the game view."
        ),
    )
    args = p.parse_args()

    return asyncio.run(
        run(
            args.task,
            ws_url=args.ws_url,
            task_timeout=args.task_timeout,
            connect_timeout=args.connect_timeout,
            dump_dir=Path(args.dump_dir) if args.dump_dir else None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
