#!/usr/bin/env python
# -- coding: utf-8 --
"""Measure how much margin a real provider leaves the arbiter's fail-close bounds.

Every bound in ``main_logic/omni_realtime_client/_response_arbiter.py`` was
chosen without field data, and each one tears the realtime WebSocket down when
it expires — which the user experiences as a disconnect and session rebuild.
Unit tests cannot settle whether a bound is right, because the provider in a
unit test is one we wrote. This probe asks the provider instead.

It drives the real ``OmniRealtimeClient`` rather than hand-rolling the wire
protocol. That is not incidental: the Lanlan free routes fingerprint the first
packet and reject anything that is not the application ("Invalid first packet:
you are not using Lanlan"), and more importantly the numbers are only worth
having if they came from the same handshake, session config and event sequence
production uses. A tap on the socket records every frame with a timestamp; the
client's own behaviour is untouched.

The arbiter's constants are imported rather than copied, so the margin table
stays honest if a bound is ever retuned.

Deliberately an opt-in development probe. Nothing imports it and no test runs
it. The free routes are sponsored capacity — the defaults here are small on
purpose; raise ``--turns`` deliberately, not by habit.

Scenarios
---------
baseline     N ordinary turns: create -> created -> done latencies.
bargein      ``cancel_response(wait=True)`` mid-reply, which is what a user
             talking over the answer triggers. Highest-frequency fail-close
             entry in production, and the one with the tightest bound (3s).
idle-cancel  Cancel with nothing running: what comes back, and does it carry
             an event_id the arbiter could have correlated?
soak         Long run counting turns whose ``response.done`` never arrived.

Usage
-----
    uv run python scripts/realtime_arbiter_probe.py --route tech
    uv run python scripts/realtime_arbiter_probe.py --route app --scenario bargein
    uv run python scripts/realtime_arbiter_probe.py --route tech --scenario soak \
        --soak-minutes 30 --json-out probe-tech.json

``--route tech`` is www.lanlan.tech (China free, StepFun-backed, server VAD);
``--route app`` is www.lanlan.app (international free, Gemini proxy, NO server
VAD — the only route where the deliberately unbounded speech-id rotation runs).
They exercise different arbiter paths and both are worth a run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_logic.omni_realtime_client import OmniRealtimeClient  # noqa: E402
from main_logic.omni_realtime_client._response_arbiter import (  # noqa: E402
    _DEFAULT_RESPONSE_DONE_TIMEOUT,
    _SERVER_VAD_RESPONSE_STARTED_TIMEOUT,
)


# The per-ticket defaults ``enqueue`` applies unless a caller overrides them.
# Kept as a table because they are keyword defaults on a method signature; the
# two that are module constants are imported so they cannot drift silently.
_ARBITER_BOUNDS: dict[str, float] = {
    "item_ack": 1.5,
    "response_started": 5.0,
    "response_done": _DEFAULT_RESPONSE_DONE_TIMEOUT,
    "cancel": 3.0,
    "server_vad_started": _SERVER_VAD_RESPONSE_STARTED_TIMEOUT,
}

_ROUTES = {
    "tech": {
        "url": "wss://www.lanlan.tech/core",
        "label": "lanlan.tech (China free, StepFun proxy, server VAD)",
        "expects_server_vad": True,
    },
    "app": {
        "url": "wss://www.lanlan.app/core",
        "label": "lanlan.app (international free, Gemini proxy, no server VAD)",
        "expects_server_vad": False,
    },
}

_FREE_API_KEY = "free-access"
_FREE_MODEL = "free-model"

# Short, boring and identical every turn: the point is to time the protocol,
# not to explore the model. A varying prompt makes latencies incomparable.
#
# Chinese on purpose, and the reason is the measurement itself. What is being
# timed is how long `response.done` takes, and that is driven by how much the
# model says and how long its audio runs — both language-dependent. These are
# the routes a Chinese-language product ships on, so an English prompt would
# produce numbers that are precise about the wrong traffic. Not a user-facing
# prompt: it never leaves this dev probe.
_PROMPT = "用一句话说说今天的天气。"  # noqa: INLINE_PROMPT_NON_EN  # measurement realism, see above

# The Lanlan free routes admit a client by watermark: the session instructions
# must carry this line from the character system prompt
# (config/prompts/prompts_chara.py, <Context Awareness>). It is what tells the
# proxy the connection is N.E.K.O rather than a third party reselling sponsored
# capacity — without it the socket is closed with
# ``1008 Invalid first packet: you are not using Lanlan``.
#
# Reproduced with {LANLAN_NAME} already substituted: update_session runs
# llm_prompt_leak_check over the payload and asserts on any unrendered
# {placeholder}.
_WATERMARK = (
    "- System Info: The system periodically sends some useful information to "
    "Neko. Neko can leverage this information to better understand the context."
)
_INSTRUCTIONS = (
    "You are a terse assistant. Answer in one short sentence.\n"
    "<Context Awareness>\n"
    f"{_WATERMARK}\n"
    "</Context Awareness>"
)


@dataclass
class _Turn:
    """One request's observed lifecycle, in seconds since the probe started."""

    label: str
    sent_at: float
    created_at: float | None = None
    done_at: float | None = None
    item_acked_at: float | None = None
    first_delta_at: float | None = None
    cancel_sent_at: float | None = None
    response_id: str | None = None
    status: str | None = None
    # An id-less streaming provider is the case the escape hatch documents as
    # unprotectable (issue #2611). It is a property of the provider, so only a
    # run like this one can establish whether that case is real here.
    stream_events: int = 0
    stream_events_with_id: int = 0

    @staticmethod
    def _delta(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return b - a


@dataclass
class _Observations:
    route: str
    scenario: str
    turns: list[_Turn] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    idle_cancel_replies: list[dict[str, Any]] = field(default_factory=list)
    server_vad_seen: bool = False
    connection_errors: list[str] = field(default_factory=list)
    # Set when the arbiter itself decided to drop the transport. This is what
    # the whole probe exists to detect, so it is reported whether or not any
    # latency was interesting.
    teardown: str | None = None
    frames_in: int = 0


class _TappedSocket:
    """Pass-through wrapper that timestamps every inbound frame.

    ``handle_messages`` consumes the socket with ``async for message in
    self.ws``, so overriding ``__aiter__`` is enough to see everything the
    client sees, in the order it sees it, without changing what it does.
    Everything else is delegated, including ``send`` and ``close``.
    """

    def __init__(self, inner: Any, on_frame: Any) -> None:
        self._inner = inner
        self._on_frame = on_frame

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aiter__(self):
        async for message in self._inner:
            self._on_frame(message)
            yield message


class _Probe:
    def __init__(self, route: str, verbose: bool) -> None:
        self._route = route
        self._verbose = verbose
        self._obs = _Observations(route=route, scenario="")
        self._t0 = time.monotonic()
        self._client: OmniRealtimeClient | None = None
        self._pump: asyncio.Task | None = None
        self._by_response_id: dict[str, _Turn] = {}
        self._open: _Turn | None = None
        self._announced = asyncio.Event()
        self._terminal = asyncio.Event()
        self._first_delta = asyncio.Event()
        self._idle_cancel_watch: list[dict[str, Any]] | None = None

    # -- plumbing ---------------------------------------------------------

    @property
    def observations(self) -> _Observations:
        return self._obs

    def _stamp(self) -> float:
        return time.monotonic() - self._t0

    def _log(self, message: str) -> None:
        if self._verbose:
            print(f"[{self._stamp():7.3f}s] {message}", flush=True)

    async def connect(self, url_override: str | None = None) -> None:
        spec = _ROUTES[self._route]
        url = url_override or spec["url"]

        async def _on_connection_error(reason: str) -> None:
            self._obs.connection_errors.append(f"{self._stamp():.3f}s {reason}")
            self._log(f"!! connection error: {reason}")

        client = OmniRealtimeClient(
            url,
            _FREE_API_KEY,
            model=_FREE_MODEL,
            api_type="free",
            on_connection_error=_on_connection_error,
        )
        await client.connect(_INSTRUCTIONS)
        client.ws = _TappedSocket(client.ws, self._on_frame)

        # Wrap the arbiter's own teardown callback. `create_response()` returns
        # at `ticket.sent`, while the missing-announcement and missing-terminal
        # fail-closes fire later on the worker — so inferring a teardown from
        # the NEXT create's ConnectionError attributes it to the wrong turn and
        # misses one on the last turn of a run entirely. The report said
        # "teardown: none" after the socket was already gone.
        arbiter = client._response_arbiter
        inner_abort = arbiter._abort_transport

        async def _observed_abort(reason: str) -> None:
            if self._obs.teardown is None:
                self._obs.teardown = f"{self._stamp():.2f}s {reason}"
            self._log(f"!! arbiter tore the transport down: {reason}")
            if inner_abort is not None:
                await inner_abort(reason)

        arbiter._abort_transport = _observed_abort
        self._client = client
        self._pump = asyncio.create_task(client.handle_messages())
        # The provider acknowledges session.update asynchronously; let that
        # settle so it does not show up as first-turn latency.
        await asyncio.sleep(1.5)

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):
                # Teardown of a probe run: the receive loop was just cancelled,
                # and a transport that is already gone raising on the way out
                # says nothing the report has not already recorded.
                pass
        if self._client is not None:
            try:
                await self._client.close()
            except (asyncio.CancelledError, Exception):
                # Same: closing a socket the provider already dropped is the
                # normal end of a run that observed a teardown.
                pass

    # -- observation ------------------------------------------------------

    def _on_frame(self, raw: Any) -> None:
        self._obs.frames_in += 1
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(event, dict):
            return
        self._record(event)

    def _record(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        now = self._stamp()
        response_id = self._response_id_of(event)

        if etype.startswith("input_audio_buffer.speech_"):
            self._obs.server_vad_seen = True
            return

        if etype == "conversation.item.created":
            if self._open is not None and self._open.item_acked_at is None:
                self._open.item_acked_at = now
            return

        if etype == "response.created":
            self._log(f"<- response.created id={response_id}")
            if self._open is not None and self._open.created_at is None:
                self._open.created_at = now
                self._open.response_id = response_id
                if response_id:
                    self._by_response_id[response_id] = self._open
                self._announced.set()
            return

        if etype == "response.done":
            response = event.get("response")
            status = ""
            if isinstance(response, dict):
                status = str(response.get("status") or "")
            self._log(f"<- response.done id={response_id} status={status or '(none)'}")
            turn = self._resolve(response_id)
            if turn is not None:
                turn.done_at = now
                turn.status = status or None
            self._terminal.set()
            return

        if etype == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else {}
            record = {
                "at": now,
                "message": str(event.get("error"))[:400],
                # Whether the provider echoes the offending client event_id is
                # what decides whether an arbiter could ever correlate this
                # error back to the request it belongs to.
                "echoed_event_id": err.get("event_id") or event.get("event_id"),
                "code": err.get("code") or err.get("type"),
            }
            self._log(f"<- error code={record['code']} echo={record['echoed_event_id']}")
            self._obs.errors.append(record)
            if self._idle_cancel_watch is not None:
                self._idle_cancel_watch.append(record)
            self._terminal.set()
            return

        if etype.startswith("response."):
            turn = self._resolve(response_id)
            if turn is not None:
                turn.stream_events += 1
                if response_id:
                    turn.stream_events_with_id += 1
                if turn.first_delta_at is None:
                    turn.first_delta_at = now
            self._first_delta.set()

    @staticmethod
    def _response_id_of(event: dict[str, Any]) -> str | None:
        direct = event.get("response_id")
        if direct:
            return str(direct)
        response = event.get("response")
        if isinstance(response, dict) and response.get("id"):
            return str(response["id"])
        return None

    def _resolve(self, response_id: str | None) -> _Turn | None:
        if response_id and response_id in self._by_response_id:
            return self._by_response_id[response_id]
        return self._open

    # -- scenarios --------------------------------------------------------

    @staticmethod
    async def _wait_for_first_of(
        first: asyncio.Event, second: asyncio.Event, timeout: float
    ) -> None:
        """Return as soon as either event is set, or the bound expires."""

        waiters = [asyncio.create_task(first.wait()), asyncio.create_task(second.wait())]
        try:
            await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def run_turn(self, label: str, *, barge_in: bool = False) -> _Turn:
        """Drive one create -> created -> done cycle, optionally cancelling it."""

        assert self._client is not None
        turn = _Turn(label=label, sent_at=self._stamp())
        self._open = turn
        self._announced.clear()
        self._terminal.clear()
        self._first_delta.clear()

        try:
            await self._client.create_response(_PROMPT)
        except ConnectionError as exc:
            # The headline observation, not an error to crash on: the arbiter
            # gave up on a lifecycle and tore the transport down, which the
            # user experiences as a disconnect and session rebuild. Record what
            # it said and stop — the socket is gone, so every later turn would
            # only re-report the same corpse.
            self._obs.teardown = f"{self._stamp():.2f}s {exc}"
            self._log(f"!! arbiter tore the transport down: {exc}")
            self._open = None
            self._obs.turns.append(turn)
            return turn

        # Deliberately generous: the probe measures how long things take, so
        # its own waits must never be what expires. The comparison against the
        # arbiter's bound happens in the report, not here.
        #
        # Whichever comes first, and NOT the announcement alone: one of the two
        # routes this probe exists for never sends response.created at all, so
        # waiting for it burned the whole budget while the reply came and went.
        # The barge-in then landed on an idle connection and measured nothing —
        # a cancel latency for a turn that had already finished. Derived from
        # what the provider does rather than from a per-route flag, for the same
        # reason the arbiter is: these proxies have changed which events they
        # emit.
        await self._wait_for_first_of(
            self._announced,
            self._first_delta,
            max(_ARBITER_BOUNDS["response_started"] * 4, 20.0),
        )
        if not self._announced.is_set():
            self._log("(no response.created — this route does not announce)")

        if barge_in:
            if not self._first_delta.is_set():
                try:
                    await asyncio.wait_for(self._first_delta.wait(), 15.0)
                except asyncio.TimeoutError:
                    self._log("!! nothing streaming to barge in on")
            if turn.done_at is not None:
                # It already finished. Cancelling now would time an idle
                # connection and report it as barge-in latency.
                self._log("!! reply finished before the barge-in — not measured")
                self._open = None
                self._obs.turns.append(turn)
                return turn
            turn.cancel_sent_at = self._stamp()
            # Exactly what the production barge-in does: cancel_response(wait=True)
            # is cancel_current(), the 3s bound whose expiry tears the socket down.
            # Swallow its TimeoutError here — that IS the measurement.
            try:
                await self._client.cancel_response(wait=True)
            except asyncio.TimeoutError:
                self._log("!! cancel_current timed out — this is a fail-close in production")
            except Exception as exc:  # noqa: BLE001 - probe records, never fails
                self._log(f"!! cancel raised {type(exc).__name__}: {exc}")

        try:
            await asyncio.wait_for(
                self._terminal.wait(), max(_ARBITER_BOUNDS["response_done"], 30.0)
            )
        except asyncio.TimeoutError:
            self._log("!! no terminal event")

        self._open = None
        self._obs.turns.append(turn)
        return turn

    async def run_idle_cancel(self) -> None:
        """Cancel with nothing running and record exactly what comes back.

        This is the reply the arbiter cannot correlate today: ``response.cancel``
        goes out unstamped by any ticket, so an error echoing its event_id
        matches no ticket's ``event_ids``. Whether that matters at all depends
        on what this provider actually answers.
        """

        assert self._client is not None
        watch: list[dict[str, Any]] = []
        self._idle_cancel_watch = watch
        try:
            # wait=False: send the bare cancel without entering the 3s wait, so
            # what is measured is the provider's reply, not our own bound.
            await self._client.cancel_response(wait=False)
            deadline = self._stamp() + 8.0
            while self._stamp() < deadline and not watch:
                await asyncio.sleep(0.25)
        finally:
            self._idle_cancel_watch = None
        if not watch:
            watch.append(
                {
                    "message": "(no reply at all within 8s)",
                    "echoed_event_id": None,
                    "code": None,
                }
            )
        self._obs.idle_cancel_replies.extend(watch)


def _summarize(values: list[float | None]) -> dict[str, float] | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    ordered = sorted(clean)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return {
        "n": float(len(ordered)),
        "p50": statistics.median(ordered),
        "p95": ordered[index],
        "max": ordered[-1],
    }


def _margin_line(name: str, stats: dict[str, float] | None, bound: float) -> str:
    if stats is None:
        return f"  {name:<22} (no observations){' ' * 30}bound={bound:>5.1f}s"
    worst = stats["max"]
    pct = 100.0 * worst / bound if bound else 0.0
    verdict = "OK" if pct < 50 else ("TIGHT" if pct < 100 else "OVER")
    return (
        f"  {name:<22} n={int(stats['n']):<3d} p50={stats['p50']:6.2f}s "
        f"p95={stats['p95']:6.2f}s max={worst:6.2f}s  bound={bound:>5.1f}s "
        f"worst={pct:5.1f}%  {verdict}"
    )


def _report(obs: _Observations) -> dict[str, Any]:
    announce = [_Turn._delta(t.sent_at, t.created_at) for t in obs.turns]
    # From the announcement when there is one, otherwise from the request
    # itself. One of the two routes this probe exists for never announces,
    # so keying completion on created_at alone reported 'no observations'
    # for that whole route — i.e. it could not measure the 60s bound it was
    # written to measure.
    complete = [
        _Turn._delta(t.created_at if t.created_at is not None else t.sent_at, t.done_at)
        for t in obs.turns
    ]
    item_ack = [_Turn._delta(t.sent_at, t.item_acked_at) for t in obs.turns]
    cancel = [
        _Turn._delta(t.cancel_sent_at, t.done_at)
        for t in obs.turns
        if t.cancel_sent_at is not None
    ]
    missing = [t.label for t in obs.turns if t.done_at is None]
    idless = [t.label for t in obs.turns if t.stream_events and not t.stream_events_with_id]

    stats = {
        "item_ack": _summarize(item_ack),
        "response_started": _summarize(announce),
        "response_done": _summarize(complete),
        "cancel": _summarize(cancel),
    }

    print()
    print("=" * 80)
    print(f"route     {_ROUTES[obs.route]['label']}")
    print(f"scenario  {obs.scenario}   turns={len(obs.turns)}   frames={obs.frames_in}")
    print("-" * 80)
    print("margin against the arbiter's fail-close bounds")
    print(_margin_line("item ack", stats["item_ack"], _ARBITER_BOUNDS["item_ack"]))
    print(_margin_line("response announce", stats["response_started"], _ARBITER_BOUNDS["response_started"]))
    print(_margin_line("response complete", stats["response_done"], _ARBITER_BOUNDS["response_done"]))
    print(_margin_line("cancel -> terminal", stats["cancel"], _ARBITER_BOUNDS["cancel"]))
    print("-" * 80)
    print(f"terminals never received : {len(missing)} / {len(obs.turns)}")
    if missing:
        print(f"  a lost terminal holds the lane {_DEFAULT_RESPONSE_DONE_TIMEOUT:.0f}s: {missing}")
    print(
        f"server VAD frames seen   : {obs.server_vad_seen} "
        f"(expected {_ROUTES[obs.route]['expects_server_vad']})"
    )
    print(
        f"turns with id-less stream: {len(idless)}"
        f"{'   <- issue #2611 applies on this route' if idless else ''}"
    )
    if obs.teardown:
        print(f"ARBITER TORE DOWN THE SOCKET : {obs.teardown}")
        print("  this is the user-visible disconnect the escape hatch exists for")
    else:
        print("arbiter teardown         : none")
    for reason in obs.connection_errors:
        print(f"  connection error: {reason}")
    if obs.idle_cancel_replies:
        print("-" * 80)
        print("reply to a cancel with nothing running:")
        for record in obs.idle_cancel_replies:
            print(f"  code={record.get('code')} echoed_event_id={record.get('echoed_event_id')}")
            print(f"    {str(record.get('message'))[:220]}")
    if obs.errors:
        print("-" * 80)
        print(f"provider errors          : {len(obs.errors)}")
        for record in obs.errors[:5]:
            print(f"  {record['at']:.2f}s code={record['code']} {record['message'][:140]}")
    print("=" * 80)

    return {
        "route": obs.route,
        "scenario": obs.scenario,
        "bounds": _ARBITER_BOUNDS,
        "stats": stats,
        "teardown": obs.teardown,
        "missing_terminal": missing,
        "idless_stream_turns": idless,
        "server_vad_seen": obs.server_vad_seen,
        "connection_errors": obs.connection_errors,
        "idle_cancel_replies": obs.idle_cancel_replies,
        "errors": obs.errors,
        "turns": [
            {
                "label": t.label,
                "response_id": t.response_id,
                "status": t.status,
                "announce_s": _Turn._delta(t.sent_at, t.created_at),
                "complete_s": _Turn._delta(
                    t.created_at if t.created_at is not None else t.sent_at, t.done_at
                ),
                "complete_from": "created" if t.created_at is not None else "sent",
                "cancel_s": _Turn._delta(t.cancel_sent_at, t.done_at),
                "stream_events": t.stream_events,
                "stream_events_with_id": t.stream_events_with_id,
            }
            for t in obs.turns
        ],
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    probe = _Probe(args.route, verbose=not args.quiet)
    probe.observations.scenario = args.scenario
    await probe.connect(args.url)
    obs = probe.observations
    try:
        if args.scenario in ("baseline", "all"):
            for index in range(args.turns):
                await probe.run_turn(f"baseline-{index + 1}")
                if obs.teardown:
                    break
                await asyncio.sleep(args.gap)

        if args.scenario in ("bargein", "all") and not obs.teardown:
            for index in range(args.turns):
                await probe.run_turn(f"bargein-{index + 1}", barge_in=True)
                if obs.teardown:
                    break
                await asyncio.sleep(args.gap)

        if args.scenario in ("idle-cancel", "all") and not obs.teardown:
            await probe.run_idle_cancel()

        if args.scenario == "soak":
            deadline = time.monotonic() + args.soak_minutes * 60
            index = 0
            while time.monotonic() < deadline and not obs.teardown:
                index += 1
                await probe.run_turn(f"soak-{index}")
                await asyncio.sleep(args.gap)
    finally:
        await probe.close()

    return _report(probe.observations)


def main() -> int:
    parser = argparse.ArgumentParser(description="Realtime arbiter provider probe")
    parser.add_argument("--route", choices=sorted(_ROUTES), default="tech")
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "override the route's websocket URL. Use for a self-hosted or "
            "livestream upstream, or to point the probe at a local stub while "
            "validating the probe itself. --route still selects which "
            "server-VAD expectation the report checks against."
        ),
    )
    parser.add_argument(
        "--scenario",
        choices=["baseline", "bargein", "idle-cancel", "soak", "all"],
        default="all",
    )
    parser.add_argument("--turns", type=int, default=5, help="turns per scenario")
    parser.add_argument("--gap", type=float, default=2.0, help="seconds between turns")
    parser.add_argument("--soak-minutes", type=float, default=30.0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true", help="suppress the event trace")
    args = parser.parse_args()

    try:
        result = asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    if args.json_out is not None:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
