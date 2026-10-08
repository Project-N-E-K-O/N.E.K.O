# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test doubles for the visit runtime: clocks, a fake session-manager port, fake LLM sessions,
fake Servers credentials and a back-to-back data channel between a host and a guest runtime.

Every file the runtimes write lands under the ``tmp_path`` each side is given.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from main_logic.visit.identity import FetchedPubkeys, PubkeySet, b64url_encode, mint_ticket
from main_logic.visit.limits import Blocklist
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router.session_pool import VisitSession

NOW = 1_800_000_000.0
KID = "kt2026"
VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"
HOST_UID = "0123456789abcdef01234567"
GUEST_UID = "89abcdef0123456789abcdef"
HOST_VID = "h_" + "a" * 24
GUEST_VID = "g_" + "b" * 24
HOST_CHAR_UID = "1" * 32
GUEST_CHAR_UID = "2" * 32
INVITE = "ABCDEFGHJK"

_PRIV = Ed25519PrivateKey.generate()


def pubkeys() -> PubkeySet:
    raw = _PRIV.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    builtin = {KID: {"pub": b64url_encode(raw), "not_before": int(NOW) - 86400, "not_after": int(NOW) + 86400}}
    return PubkeySet.build(now=NOW, fetched=FetchedPubkeys(keys={}, revoked=frozenset(), fetched_at=NOW - 60),
                           builtin=builtin)


def ticket(*, role: str, sub: str, vid: str, char_tag: str, display_name: str = "Mimi",
           visit_id: str = VISIT_ID, jti: Optional[str] = None, transport: str = "livekit") -> str:
    iat = int(NOW) - 30
    return mint_ticket({
        "v": 1, "iss": "neko-servers", "aud": "neko-visit", "kid": KID, "sub": sub, "vid": vid,
        "visit_id": visit_id, "role": role, "transport": transport, "char_tag": char_tag,
        "display_name": display_name, "iat": iat, "exp": iat + (3000 if role == "host" else 2400),
        "jti": jti or uuid.uuid4().hex[:22],
    }, _PRIV)


def credentials(role: str, *, visit_id: str = VISIT_ID, invite_code: Optional[str] = INVITE,
                account: str = "acct") -> cr.VisitCredentials:
    host = role == "host"
    return cr.VisitCredentials(
        role=role, visit_id=visit_id, char_tag=HOST_CHAR_UID if host else GUEST_CHAR_UID,
        transport="livekit", tier="sd600", expires_at=NOW + (3000 if host else 2400),
        vendor_expires_at=NOW + 600,
        vendor={"livekit": {"url": "wss://lk.example.test", "token": "tok", "ttl_s": 600}},
        identity_ticket=ticket(role=role, sub=HOST_UID if host else GUEST_UID,
                               vid=HOST_VID if host else GUEST_VID,
                               char_tag=HOST_CHAR_UID if host else GUEST_CHAR_UID,
                               display_name="Host cat" if host else "Guest cat", visit_id=visit_id),
        visit_uid=HOST_UID if host else GUEST_UID, vid=HOST_VID if host else GUEST_VID,
        peer_vid=None if host else HOST_VID, invite_code=invite_code if host else None,
        invite_expires_at=NOW + 600 if host else None, account=account,
    )


class FakeClock:
    """A clock that follows real elapsed time from ``start`` and can be jumped forward (``advance``)."""

    def __init__(self, start: float) -> None:
        self._start = float(start)
        self._t0 = time.monotonic()
        self._offset = 0.0

    def __call__(self) -> float:
        return self._start + (time.monotonic() - self._t0) + self._offset

    @property
    def now(self) -> float:
        return self()

    def advance(self, seconds: float) -> None:
        self._offset += float(seconds)


class FakeStream:
    """A ``SpeechStream`` that records pushes and, with ``auto_play``, reports its own end right away."""

    def __init__(self, host: "FakeHost", request_id: str, on_enqueued: Callable[[int], None]) -> None:
        self.host = host
        self.request_id = request_id
        self.speech_id = "sp-" + uuid.uuid4().hex[:12]
        self.on_enqueued = on_enqueued
        self.pushed: list[str] = []
        self.finished = False
        self.aborted = False
        self.closed = False

    def push(self, delta: str) -> bool:
        if self.closed:
            return False
        self.pushed.append(delta)
        self.on_enqueued(len(delta))
        return True

    def finish(self):
        if self.closed:
            return False
        self.finished = True
        self.closed = True
        if self.host.auto_play:
            asyncio.get_running_loop().call_soon(self.host.play_out, self)
        return True

    def abort(self) -> bool:
        self.closed = True
        self.aborted = True
        return True


class FakeHost:
    """``VisitHost`` double recording everything the runtime asks of the session manager."""

    def __init__(self, lanlan_name: str) -> None:
        self.lanlan_name = lanlan_name
        self.current = True
        self.precondition: Optional[str] = None
        self.frames: list[dict] = []
        self.statuses: list[tuple[str, dict]] = []
        self.takeovers: list[object] = []
        self.released: list[object] = []
        self.holds: list[object] = []
        self.hold_released: list[object] = []
        self.resubmitted: list[dict] = []
        self.user_inputs: list[str] = []
        self.outputs: list[str] = []
        self.blocks: list[tuple[list, str]] = []
        self.streams: list[FakeStream] = []
        self.voice_streams = True
        self.auto_play = True
        self.last_input = 0.0
        self.parked = 0
        self.acked: list = []
        self.failed: list = []
        self.takeover_error: Optional[Exception] = None
        self.events: list[str] = []
        self.mirror_error: Optional[Exception] = None

    def is_current(self) -> bool:
        return self.current

    def precondition_failure(self):
        return self.precondition

    async def interrupt_main_turn(self, timeout: float) -> bool:
        return True

    async def send_frame(self, payload: dict) -> bool:
        self.frames.append(payload)
        return True

    async def send_status(self, code: str, details=None) -> bool:
        self.statuses.append((code, dict(details or {})))
        return True

    def acquire_takeover(self, dispatcher, sink):
        if self.takeover_error is not None:
            raise self.takeover_error
        token = object()
        self.takeovers.append(token)
        self.sink = sink
        self.events.append("acquire_takeover")
        return token

    def release_takeover(self, token) -> bool:
        self.released.append(token)
        self.events.append("release_takeover")
        return True

    async def interrupt_ordinary_speech(self) -> None:
        return None

    def hold_callbacks(self, sink):
        token = object()
        self.holds.append(token)
        self.hold_sink = sink
        self.events.append("hold_callbacks")
        return token

    def release_callback_hold(self, token) -> bool:
        self.hold_released.append(token)
        self.events.append("release_callback_hold")
        return True

    def resubmit_callbacks(self, callbacks) -> None:
        self.resubmitted.extend(callbacks)
        self.events.append("resubmit")

    def open_speech_stream(self, *, metadata, request_id, on_enqueued):
        if not self.voice_streams:
            return None
        stream = FakeStream(self, request_id, on_enqueued)
        stream.metadata = metadata
        self.streams.append(stream)
        return stream

    def play_out(self, stream: FakeStream) -> None:
        asyncio.ensure_future(rtm.on_page_signal(self.lanlan_name, {
            "action": "visit_speech_progress", "speech_id": stream.speech_id, "played_ms": 10 ** 7,
            "ended": True, "final": True,
        }))

    async def mirror_user_input(self, text, *, metadata, request_id) -> None:
        if self.mirror_error is not None:
            raise self.mirror_error
        self.user_inputs.append(text)
        self.events.append("mirror_user_input")

    async def mirror_assistant_output(self, text, *, metadata, request_id) -> None:
        self.outputs.append(text)
        self.events.append(f"output:{request_id}")

    async def render_chat_blocks(self, blocks, *, request_id, source_name) -> bool:
        self.blocks.append((blocks, request_id))
        self.events.append(f"blocks:{request_id}")
        return True

    def park_proactive(self) -> None:
        self.parked += 1

    def last_user_input(self) -> float:
        return self.last_input

    async def wait_turn_idle(self, timeout: float) -> None:
        self.events.append("wait_turn_idle")

    async def ack_text_session(self, request_id) -> None:
        self.acked.append(request_id)

    async def fail_session(self, mode, request_id) -> None:
        self.failed.append((mode, request_id))

    def frames_of(self, type_: str, action: Optional[str] = None) -> list[dict]:
        return [f for f in self.frames if f.get("type") == type_ and (action is None or f.get("action") == action)]

    def status_codes(self) -> list[str]:
        return [code for code, _ in self.statuses]


class FakeClient:
    """``OmniOfflineClient`` double: ``stream_text`` streams scripted replies through ``on_text_delta``."""

    def __init__(self, on_text_delta, replies: "Replies") -> None:
        from utils.llm_client import SystemMessage

        self.on_text_delta = on_text_delta
        self.replies = replies
        self._conversation_history: list = [SystemMessage(content="instructions")]
        self.prompts: list[str] = []
        self.seen: list[list[str]] = []
        self.closed = False

    async def stream_text(self, text: str, **_kwargs: Any) -> None:
        from utils.llm_client import AIMessage, HumanMessage

        self.prompts.append(text)
        # LLM 这一轮实际收到的历史（不含本轮提问）
        self.seen.append([getattr(m, "content", "") for m in self._conversation_history])
        self._conversation_history.append(HumanMessage(content=text))
        chunks = await self.replies.next(text)
        out = []
        for chunk in chunks:
            await asyncio.sleep(0)
            if isinstance(chunk, asyncio.Event):
                # 生成途中的停顿点：测试在这里插入打断 / 结束
                await chunk.wait()
                continue
            out.append(chunk)
            await self.on_text_delta(chunk, len(out) == 1)
        self._conversation_history.append(AIMessage(content="".join(out)))

    async def close(self) -> None:
        self.closed = True


@dataclass
class Replies:
    """Scripted LLM output: ``default`` chunks, or per-call overrides queued in ``queue``."""

    default: list[str] = field(default_factory=lambda: ["今天天气真好。", "我们去玩吧！"])
    queue: list = field(default_factory=list)
    gate: Optional[asyncio.Event] = None
    raise_error: Optional[BaseException] = None
    calls: int = 0

    async def next(self, prompt: str) -> list[str]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_error is not None:
            raise self.raise_error
        if self.queue:
            item = self.queue.pop(0)
            return item(prompt) if callable(item) else list(item)
        return list(self.default)


@dataclass
class Side:
    """Everything one test side owns."""

    name: str
    role: str
    host: FakeHost
    deps: rtm.RuntimeDeps
    config_dir: Path
    replies: Replies
    clients: list = field(default_factory=list)
    creds_calls: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    commits: list = field(default_factory=list)
    settings: dict = field(default_factory=lambda: {"visitMemoryEnabled": True, "visitVoiceEnabled": True})
    rt: Optional[rtm.VisitRuntime] = None
    creds_error: Optional[Exception] = None


def make_side(tmp_path: Path, role: str, *, clock: FakeClock, wall: FakeClock, name: Optional[str] = None,
              replies: Optional[Replies] = None) -> Side:
    name = name or ("Host" if role == "host" else "Guest")
    config_dir = tmp_path / role
    config_dir.mkdir(parents=True, exist_ok=True)
    side_holder: dict = {}
    replies = replies or Replies()

    async def fetch(**kwargs):
        side = side_holder["side"]
        side.creds_calls.append(kwargs)
        if side.creds_error is not None:
            raise side.creds_error
        return credentials(role)

    async def fetch_pubkeys():
        return pubkeys()

    async def load_blocklist(path):
        return Blocklist(path, ())

    async def record_account(account, visit_uid):
        return True

    async def cancel_room(visit_id, **kwargs):
        side_holder["side"].cancelled.append((visit_id, kwargs))
        return True

    async def settings():
        return dict(side_holder["side"].settings)

    async def create_session(name_, side_, *, instructions, lang):
        session_holder: dict = {}

        async def forward(text, is_first=False, **kw):
            await session_holder["s"].on_text_delta(text, is_first, **kw)

        client = FakeClient(forward, replies)
        client.instructions = instructions
        session = VisitSession(client=client, side=side_)
        session_holder["s"] = session
        side_holder["side"].clients.append(client)
        return session

    async def character_context():
        from main_routers.visit_router.local_context import CharacterContext

        return CharacterContext(family_names=("小明",), cards={name: "card"})

    async def memory_block(*args, **kwargs):
        return ""

    async def commit_region(spool, **kwargs):
        side_holder["side"].commits.append(("region", spool.visit_id))

    async def commit_summary(spool, **kwargs):
        side_holder["side"].commits.append(("summary", spool.visit_id))
        return True

    def schedule_upload(visit_id):
        side_holder["side"].uploads.append(visit_id)

    deps = rtm.RuntimeDeps(
        config_dir=lambda: config_dir, fetch_credentials=fetch, fetch_pubkeys=fetch_pubkeys,
        load_blocklist=load_blocklist, record_account=record_account, cancel_room=cancel_room,
        settings=settings, create_session=create_session, character_context=character_context,
        memory_block=memory_block, commit_region=commit_region, commit_summary=commit_summary,
        schedule_upload=schedule_upload, rng=random.Random(0), reply_gap_s=(0.0, 0.0),
    )
    side = Side(name=name, role=role, host=FakeHost(name), deps=deps, config_dir=config_dir, replies=replies)
    side_holder["side"] = side
    return side


class Wire:
    """Back-to-back data channel: what one runtime ``send``s reaches the other's ``on_recv`` in order."""

    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue] = {}
        self.downlinks: dict[str, list[dict]] = {}
        self.sent: dict[str, list[dict]] = {}
        self.drop: Optional[Callable[[str, dict], bool]] = None
        self.tasks: list[asyncio.Task] = []
        self.paused: set[str] = set()

    def attach(self, rt: rtm.VisitRuntime, peer: Optional[rtm.VisitRuntime], vid: str) -> None:
        role = rt.side
        self.downlinks[role] = []
        self.sent[role] = []
        queue: asyncio.Queue = asyncio.Queue()
        self.queues[role] = queue

        async def send(msg):
            self.downlinks[role].append(dict(msg))
            if msg.get("type") == "send":
                payload = msg["payload"]
                self.sent[role].append(payload)
                if self.drop is not None and self.drop(role, payload):
                    return True
                queue.put_nowait((msg["cmd"], payload))
            return True

        rt.transport.send = send  # type: ignore[method-assign]
        rt.transport_alive = lambda: True  # type: ignore[method-assign]
        if peer is not None:
            self.connect(queue, peer, vid)

    def connect(self, queue: asyncio.Queue, peer: rtm.VisitRuntime, from_vid: str) -> None:
        async def pump():
            while True:
                cmd, payload = await queue.get()
                try:
                    await peer.on_recv(from_vid=from_vid, cmd=cmd, payload=payload,
                                       nbytes=len(json.dumps(payload).encode()))
                finally:
                    queue.task_done()

        self.tasks.append(asyncio.ensure_future(pump()))

    def sent_types(self, role: str) -> list[str]:
        return [p.get("t") for p in self.sent.get(role, [])]

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def settle(rounds: int = 30) -> None:
    """Let queued tasks run (no real time passes)."""
    for _ in range(rounds):
        await asyncio.sleep(0)


async def wait_for(pred: Callable[[], bool], timeout: float = 5.0, step: float = 0.01) -> None:
    """Wait (real time, bounded) until ``pred()``; fails the test on timeout."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(step)


async def step(clock: FakeClock, seconds: float, *runtimes: rtm.VisitRuntime, every: float = 2.0) -> None:
    """Advance ``clock`` in small steps: each step flushes (heartbeats / acks reach the peer) and ticks."""
    left = float(seconds)
    while left > 0:
        dt = min(every, left)
        clock.advance(dt)
        left -= dt
        for rt in runtimes:
            if rt.exit_task is None:
                await rt.flush()
        await settle(60)
        for rt in runtimes:
            if rt.exit_task is None:
                await rt.tick()
        await settle(20)


async def start_side(side: Side, *, crop: str = "upper", invite_code: Optional[str] = None,
                     clock: FakeClock, wall: FakeClock) -> rtm.VisitRuntime:
    """``start_visit`` with the persona gate and the local account stubbed."""
    rt = await rtm.start_visit(side.name, side.role, crop=crop, invite_code=invite_code, visit_id=VISIT_ID,
                               host=side.host, deps=side.deps, clock=clock, wall=wall)
    side.rt = rt
    return rt


async def through_gate(rt: rtm.VisitRuntime, *, video_ok: bool = True) -> Optional[dict]:
    """Preflight ok → first credentials → SDK caps ok (returns the credentials downlink)."""
    await rt.on_preflight({"stage": "preflight", "preflight_ok": True})
    msg = await rt.issue_credentials()
    await rt.on_sdk_caps({"stage": "sdk", "transport_ok": True, "video_ok": video_ok, "codecs": []})
    return msg


async def bring_up(tmp_path: Path, monkeypatch, *, accept: bool = True, host_replies: Optional[Replies] = None,
                   guest_replies: Optional[Replies] = None, settings: Optional[dict] = None):
    """Host and guest through the gate, joined, hello verified; with ``accept`` the visit is active."""
    patch_admission(monkeypatch)
    clock = FakeClock(1000.0)
    wall = FakeClock(NOW)
    host = make_side(tmp_path, "host", clock=clock, wall=wall, replies=host_replies)
    guest = make_side(tmp_path, "guest", clock=clock, wall=wall, replies=guest_replies)
    if settings:
        host.settings.update(settings)
        guest.settings.update(settings)
    wire = Wire()
    hrt = await start_side(host, clock=clock, wall=wall)
    grt = await start_side(guest, invite_code=INVITE, clock=clock, wall=wall)
    wire.attach(hrt, grt, HOST_VID)
    wire.attach(grt, hrt, GUEST_VID)
    await through_gate(hrt)
    await hrt.on_transport_state({"state": "joined", "peer_present": False})
    await through_gate(grt)
    await grt.on_transport_state({"state": "joined", "peer_present": True})
    await hrt.on_transport_state({"state": "connected", "peer_present": True})
    await wait_for(lambda: hrt.peer is not None and grt.peer is not None)
    if accept:
        status, _body = await hrt.accept(True)
        assert status == 200
        await wait_for(lambda: grt.activated and hrt.activated)
    return host, guest, wire, clock, wall


def patch_admission(monkeypatch) -> None:
    from main_routers.visit_router import persona
    from main_routers.visit_router.persona import PersonaGate

    async def gate(name):
        return PersonaGate(ok=True, state="ok", character_uid=HOST_CHAR_UID if name == "Host" else GUEST_CHAR_UID,
                           text="一只活泼的猫娘。")

    async def account():
        return "acct"

    monkeypatch.setattr(persona, "persona_gate", gate)
    monkeypatch.setattr(rtm, "_local_account", account)


async def finish(rt: rtm.VisitRuntime, clock: Optional[FakeClock] = None, *, timeout: float = 20.0) -> None:
    """Wait for ``rt``'s exit flow, jumping ``clock`` forward so drain / leave windows pass quickly."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    jumped = 0.0
    while rt.exit_task is None or not rt.exit_task.done():
        if loop.time() > end:
            raise AssertionError("exit flow did not finish")
        # 只快进到足够跳过排空与 leave 补传窗口：不能把交还回调的兜底期限也跳过去
        if clock is not None and rt.exit_task is not None and jumped < 12.0:
            clock.advance(0.5)
            jumped += 0.5
        await asyncio.sleep(0.01)


async def teardown(*sides: Side, wire: Optional[Wire] = None, clock: Optional[FakeClock] = None) -> None:
    """Finish every runtime still alive (no files or tasks left behind)."""
    for side in sides:
        rt = side.rt
        if rt is None:
            continue
        if rt.exit_task is None and rt.phase != "ended":
            rt.request_finalize("route_end")
        if rt.exit_task is not None:
            try:
                await finish(rt, clock, timeout=30)
            except AssertionError:
                pass
    if wire is not None:
        await wire.close()
