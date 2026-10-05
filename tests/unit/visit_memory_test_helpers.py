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

"""Shared fixtures of the visit memory tests (PR-08): spools on disk and a fake memory_server.

Everything lives under the test's ``tmp_path``; the fake memory_server is an
``httpx.MockTransport`` behind a real :class:`ScopedMemoryClient`, so request
bodies are the exact wire bytes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from main_logic.visit.spool import VisitSpool, new_state
from main_logic.visit.subjects import (
    PeerRoster,
    derive_pair_id,
    derive_peer_char_id,
)
from memory.scoped_client import ScopedMemoryClient

OWN_A = "a" * 24
OWN_B = "b" * 24
PEER_X = "1" * 24
PEER_Y = "2" * 24
TAG_X = "f" * 32
TAG_Y = "e" * 32
CHAR_UID_A = "c" * 32
CHAR_UID_B = "d" * 32
BASE_URL = "http://memory.test"


def vid(n: int) -> str:
    """A valid 22-character visit id."""
    return f"visit{n:017d}"


def ln(lp: int, text: str = "hello", speaker: str = "own_cat", *, role: str = "host",
       ts: float | None = None) -> dict:
    """One spool line; own speakers sit on ``role``'s side, peer speakers on the other side."""
    other = "guest" if role == "host" else "host"
    side = role if speaker.startswith("own_") else other
    return {"lp": lp, "side": side, "ts": float(1000 + lp) if ts is None else ts,
            "from": speaker, "text": text}


async def seed_roster(config_dir: Path, *, own_uid: str = OWN_A, peer_uid: str = PEER_X,
                      own_char: str = "A", tag: str = TAG_X, now: float = 100.0,
                      peer_display: str = "Xiaoming", cat_display: str = "Mimi") -> PeerRoster:
    roster = PeerRoster(config_dir, own_uid=own_uid)
    await roster.upsert(peer_uid, own_char, pair_id=derive_pair_id(own_uid, peer_uid),
                        peer_char_id=derive_peer_char_id(peer_uid, tag), char_tag=tag,
                        char_display_name=cat_display, display_name=peer_display, now=now)
    return roster


async def make_visit(
    config_dir: Path,
    visit_id: str,
    lines: list[dict],
    *,
    memory_enabled: bool = True,
    finalized: str | None = "wrap_up",
    own_uid: str = OWN_A,
    peer_uid: str = PEER_X,
    tag: str = TAG_X,
    own_char: str = "A",
    own_char_uid: str = CHAR_UID_A,
    role: str = "host",
    lang: str = "zh",
    write_jsonl: bool | None = None,
    **state_changes: Any,
) -> VisitSpool:
    """Write one visit's ``.jsonl`` (only with memory on) and ``state.json`` under ``config_dir``."""
    pair_id = derive_pair_id(own_uid, peer_uid)
    peer_char_id = derive_peer_char_id(peer_uid, tag)
    spool = VisitSpool(config_dir, visit_id)
    if write_jsonl is None:
        write_jsonl = memory_enabled
    if write_jsonl:
        header = {
            "v": 1, "visit_id": visit_id, "role": role, "own_uid": own_uid,
            "own_char": own_char, "own_char_uid": own_char_uid, "pair_id": pair_id,
            "peer_uid": peer_uid, "peer_char_id": peer_char_id, "peer_char_tag": tag,
            "started_at": 1000.0, "lang": lang,
        }
        await spool.open(header, now=0.0)
        for line in lines:
            await spool.append(line)
        await spool.close()
    state = new_state(
        own_uid=own_uid, own_char=own_char, own_char_uid=own_char_uid, pair_id=pair_id,
        peer_uid=peer_uid, peer_char_id=peer_char_id, memory_enabled=memory_enabled,
    )
    state["finalized"] = finalized
    state.update(state_changes)
    await spool.write_state(state)
    return spool


def resolver(mapping: dict[str, str] | None = None) -> Callable:
    """``own_char_uid -> name`` resolver for tests (default: A and B)."""
    table = mapping if mapping is not None else {CHAR_UID_A: "A", CHAR_UID_B: "B"}

    async def resolve(uid: str) -> str | None:
        return table.get(uid)

    return resolve


class FakeMemoryServer:
    """A fake memory_server for the scoped endpoints.

    ``requests`` keeps ``(endpoint, body)`` of every call. ``fail`` maps an
    idempotency key (or an endpoint name) to the number of times to answer
    502 for it; ``hang`` maps the same to an ``asyncio.Event`` the request
    waits on. ``done_keys`` emulates the server-side idempotency record: a
    repeated completed key answers ``duplicate: true`` without extracting.
    ``key_fingerprints`` mirrors the server's keyed request hash: the first
    request of a key stores a fingerprint of its whole body (everything but
    the key itself), and any later request reusing the key with a different
    body answers 422, whatever state the key is in.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.fail: dict[str, int] = {}
        self.fail_always: set[str] = set()
        self.hang: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}
        self.done_keys: set[str] = set()
        self.extractions = 0
        self.context_text = "SCOPED CONTEXT"
        self.subjects: list[dict] = []
        self.completed_at: dict[str, float] = {}
        # 服务端墓碑代数（subject key -> forget_epoch），由带代数的 scoped_forget 抬高
        self.tombstones: dict[str, int] = {}
        # 开轮 / 清除前的只读代数查询单独记，不混进 requests（各测试按 requests 数写入请求）
        self.epoch_reads: list[list[str]] = []
        self.epoch_reads_fail = False
        # 幂等键 -> 首次请求体指纹：同键不同请求体一律 422（与服务端 keyed request hash 同口径）
        self.key_fingerprints: dict[str, str] = {}

    @staticmethod
    def _fingerprint(body: dict) -> str:
        rest = {k: v for k, v in body.items() if k != "idempotency_key"}
        return json.dumps(rest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def calls(self, endpoint: str) -> list[dict]:
        return [body for name, body in self.requests if name == endpoint]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "forget_epochs":
            keys = request.url.params.get_list("subject")
            self.epoch_reads.append(keys)
            if self.epoch_reads_fail:
                return httpx.Response(503, json={"detail": "down"})
            return httpx.Response(200, json={"epochs": {k: self.tombstones[k] for k in keys if k in self.tombstones}})
        body = json.loads(request.content) if request.content else {}
        self.requests.append((endpoint, body))
        key = body.get("idempotency_key") or endpoint
        if body.get("idempotency_key"):
            fingerprint = self._fingerprint(body)
            if self.key_fingerprints.setdefault(key, fingerprint) != fingerprint:
                return httpx.Response(422, json={
                    "detail": "idempotency_key was already used for a different request",
                })
        for name in (key, endpoint):
            if name in self.entered:
                self.entered[name].set()
            if name in self.hang:
                await self.hang[name].wait()
        for name in (key, endpoint):
            if name in self.fail_always:
                return httpx.Response(502, json={"detail": "down"})
            if self.fail.get(name, 0) > 0:
                self.fail[name] -= 1
                return httpx.Response(502, json={"detail": "retry later"})
        self.completed_at[key] = asyncio.get_running_loop().time()
        if endpoint == "scoped_context":
            return httpx.Response(200, text=self.context_text)
        if endpoint == "scoped_subjects":
            return httpx.Response(200, json={"subjects": self.subjects})
        if endpoint == "scoped_forget":
            subject = body.get("subject") or {}
            epoch = body.get("forget_epoch")
            if isinstance(epoch, int) and subject.get("subject_kind"):
                key = f"{subject['subject_kind']}:{subject['subject_id']}"
                self.tombstones[key] = max(self.tombstones.get(key, 0), epoch)
            return httpx.Response(200, json={"status": "forgotten"})

        if endpoint == "scoped_history":
            duplicate = key in self.done_keys
            if not duplicate:
                self.extractions += 1
                if body.get("idempotency_key"):
                    self.done_keys.add(key)
            trust = {"persisted": None}
            if "segments" in body:
                return httpx.Response(200, json={
                    "status": "processed", "duplicate": duplicate,
                    "segments": [{"status": "ok", "trust": trust} for _ in body["segments"]],
                })
            return httpx.Response(200, json={"status": "processed", "duplicate": duplicate,
                                             "trust": trust})
        return httpx.Response(404)

    def client(self, *, retry_delays=()) -> ScopedMemoryClient:
        async def no_sleep(_delay: float) -> None:
            return None

        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return ScopedMemoryClient(base_url=BASE_URL, http=http, retry_delays=retry_delays,
                                  sleep=no_sleep)
