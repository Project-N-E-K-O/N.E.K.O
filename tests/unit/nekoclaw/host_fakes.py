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

"""Test doubles for the NekoClaw host: a scripted decision model and runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.unit.nekoclaw.fakes import ScriptedLLM


class FakeDecisionLLM:
    """Stands in for both tiers of the decision model and counts every request."""

    def __init__(
        self,
        *,
        coarse: list[str] | None = None,
        fine: list[Any] | None = None,
        turn: list[Any] | None = None,
    ) -> None:
        self.coarse = list(coarse or [])
        # Answers of the turn check, like ``fine``: dicts, strings or exceptions.
        self.turn = list(turn or [{"verdict": "none", "task": ""}])
        # Each item is a dict (dumped as JSON), a string (sent as is) or an
        # exception (raised). The last one repeats.
        self.fine = list(fine or [{"route": "task_agent"}])
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.gate: asyncio.Event | None = None

    def count(self, tier: str | None = None) -> int:
        return sum(1 for called, _ in self.calls if tier is None or called == tier)

    def tiers(self) -> list[str]:
        return [tier for tier, _ in self.calls]

    def last(self, tier: str) -> list[dict[str, Any]]:
        return next(messages for called, messages in reversed(self.calls) if called == tier)

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        self.calls.append((tier, [dict(message) for message in messages]))
        if self.gate is not None:
            await self.gate.wait()
        if tier == "coarse":
            return json.dumps(self.coarse)
        answers = self.turn if tier == "turn_check" else self.fine
        index = min(self.count(tier) - 1, len(answers) - 1)
        answer = answers[index]
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def pick_plugin(plugin_id: str, entry_id: str, args: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """Return a fine-selection answer that calls a plugin entry."""
    return {"route": "plugin", "plugin_id": plugin_id, "entry_id": entry_id, "plugin_args": args or {}, **extra}


async def fake_model() -> dict[str, Any]:
    return {"model": "fake-model", "api_key": "test-key"}


async def no_model() -> None:
    return None


def scripted_runtime(pick: Callable[[Any], ScriptedLLM]) -> Callable[[Any, Any], Any]:
    """Return a runtime factory: the in-process runtime whose sessions use scripted models.

    ``pick`` receives a session's spec and returns the model that session talks to.
    """
    created: list[Any] = []

    def factory(on_event: Any, host: Any) -> Any:
        from nekoclaw.runtime.core import InProcessRuntime
        from nekoclaw.runtime.model import LLMClientProvider
        from nekoclaw.runtime.session import TaskAgentSession

        async def opener(spec: Any, host_bridge: Any, emit: Any) -> Any:
            llm = pick(spec)

            async def client_factory() -> ScriptedLLM:
                return llm

            provider = LLMClientProvider(spec.model, client_factory=client_factory)
            return await TaskAgentSession.open(spec, host_bridge, emit, model=provider)

        runtime = InProcessRuntime(on_event, host, open_session=opener)
        created.append(runtime)
        return runtime

    factory.created = created  # type: ignore[attr-defined]
    return factory


def make_host(tmp_path: Path, server: Any = None, **kwargs: Any) -> Any:
    """Build a host whose files live under ``tmp_path``, talking to a fake plugin server."""
    from nekoclaw.host.service import NekoClawHost

    kwargs.setdefault("root", tmp_path / "nekoclaw")
    kwargs.setdefault("task_agent_model", no_model)
    if server is not None:
        kwargs.setdefault("client_factory", server.client)
    else:

        def unreachable(**_kwargs: Any) -> Any:
            raise RuntimeError("no plugin server in this test")

        kwargs.setdefault("client_factory", unreachable)
    return NekoClawHost(**kwargs)


async def wait_status(host: Any, commission_id: str, *statuses: str, timeout: float = 15.0) -> Any:
    """Wait until a commission has one of ``statuses`` and return it."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        commission = await host.ledger.get(commission_id)
        if commission is not None and commission.status in statuses:
            return commission
        if asyncio.get_running_loop().time() > deadline:
            status = commission.status if commission else None
            events = [(event.type, event.payload) for event in await host.ledger.events(commission_id)]
            raise AssertionError(f"commission stayed {status!r}, wanted one of {statuses}; events: {events[-12:]}")
        await asyncio.sleep(0.02)


async def wait_until(predicate: Callable[[], Any], timeout: float = 15.0, what: str = "condition") -> None:
    """Wait until ``predicate()`` is true; it may be a coroutine function."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what} did not happen within {timeout}s")
        await asyncio.sleep(0.02)


async def outbox(host: Any, kind: str | None = None) -> list[Any]:
    return [item for item in await host.ledger.outbox_pending() if kind is None or item.kind == kind]
