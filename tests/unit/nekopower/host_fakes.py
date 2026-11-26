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

"""Test doubles for the NekoPower host: a scripted decision model and runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.unit.nekopower.fakes import ScriptedLLM

# How the stand-in reads a request against the work there is, unless a test says what it reads
# (``FakeDecisionLLM.under_way``): the plain sentences tests dispatch. A bare answer (or anything after
# "answer:") answers the one question that waits; a sentence with a stop word stops the one piece of work
# under way, or the one its words name; one with a word that adds something adds to the one piece of
# work under way, or else to the newest that ended. Anything else is new work.
_BARE_ANSWERS = {
    "yes": "once", "ok": "once", "sure": "once", "go ahead": "once", "yes, go ahead": "once",
    "allow it for this session": "session", "for this session": "session",
    "always": "always", "yes, always": "always", "always, from now on": "always", "always allow": "always",
    "no": "deny", "deny": "deny",
}
_ANSWER_MARKS = ("answer:", "回答：", "回答:")
_STOP_WORDS = ("stop", "cancel", "never mind", "abort", "叫停", "停下", "停掉", "停止", "取消", "算了", "不用做了", "别做了")
_ADD_WORDS = ("also", "by the way", "make sure", "instead", "one more thing", "另外", "对了", "顺便", "还有", "补充", "记得", "改成", "再加")


def _read_like_a_model(messages: list[dict[str, Any]]) -> dict[str, Any]:
    lines = str(messages[-1]["content"]).splitlines()
    said = next(line for line in lines if line.startswith("REQUEST: "))[len("REQUEST: ") :].strip()
    lowered = said.lower()
    items: dict[str, list[tuple[int, str]]] = {"under_way": [], "asked": [], "ended": []}
    place = ""
    for line in lines:
        if line.startswith("UNDER WAY"):
            place = "under_way"
        elif line.startswith("WAITING FOR THE USER"):
            place = "asked"
        elif line.startswith("ENDED LATELY"):
            place = "ended"
        elif place and line[:1].isdigit() and ". [" in line:
            items[place].append((int(line.split(".", 1)[0]), line))
    waiting = [number for number, line in [*items["under_way"], *items["asked"]] if "; it waits for the user" in line]
    marked = next((mark for mark in _ANSWER_MARKS if lowered.startswith(mark)), None)
    answer = lowered[len(marked) :].strip() if marked else lowered
    if waiting and (marked or answer in _BARE_ANSWERS):
        if len(waiting) > 1:
            return {"does": "unsure", "items": waiting}
        return {"does": "answer", "items": waiting, "choice": _BARE_ANSWERS.get(answer, "unclear")}

    def named(candidates: list[tuple[int, str]]) -> list[int]:
        words = {word for word in lowered.replace(",", " ").split() if len(word) > 3}
        return [number for number, line in candidates if words & set(line.split('"')[1].lower().split())]

    under_way = items["under_way"]
    if any(word in lowered for word in _STOP_WORDS) and under_way:
        if len(under_way) == 1:
            return {"does": "stop", "items": [under_way[0][0]], "named": False}
        picked = named(under_way)
        if len(picked) == 1:
            return {"does": "stop", "items": picked, "named": True}
        return {"does": "unsure", "items": [number for number, _line in under_way]}
    if any(word in lowered for word in _ADD_WORDS):
        workers = [(number, line) for number, line in under_way if "by the plugin" not in line]
        if len(workers) == 1:
            return {"does": "add", "items": [workers[0][0]]}
        if workers:
            picked = named(workers)
            return {"does": "add", "items": picked} if len(picked) == 1 else {"does": "new"}
        if items["ended"] and not under_way:
            picked = named(items["ended"]) or [items["ended"][0][0]]
            return {"does": "add", "items": picked[:1]}
    return {"does": "new"}


def _choose_like_a_model(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """What a bare answer chose, as tests dispatch them (``_BARE_ANSWERS``); anything else chose nothing that is read."""
    lines = str(messages[-1]["content"]).splitlines()
    said = next(line for line in lines if line.startswith("REQUEST: "))[len("REQUEST: ") :].strip().lower()
    marked = next((mark for mark in _ANSWER_MARKS if said.startswith(mark)), None)
    said = said[len(marked) :].strip() if marked else said
    return {"answers": 1, "choice": _BARE_ANSWERS.get(said, "unclear")}


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
        # Answers for short descriptions (strings or exceptions), in order.
        self.short: list[Any] = []
        # Answers of whether a commission is a side question, like ``fine``
        # (the last one repeats); kept apart from what the other tests count.
        self.side: list[Any] = [{"side_question": True}]
        self.side_calls: list[list[dict[str, Any]]] = []
        # Answers of whether work told apart from what a busy member is at goes on with it
        # all the same, like ``side`` (the last one repeats); kept apart as well.
        self.beside: list[Any] = [{"goes_on": False}]
        self.beside_calls: list[list[dict[str, Any]]] = []
        # Answers of where a new commission belongs (the orchestrator's one
        # request), like ``fine``: the last one repeats. Kept apart as well:
        # asked only where a commission's words match something that is
        # there without naming it, which most tests have nothing of.
        self.belongs: list[Any] = [{"goes_to": 0, "relation": "new"}]
        self.belongs_calls: list[list[dict[str, Any]]] = []
        # Readings of what a request does with the work there is, like ``fine``: the last one
        # repeats. Without any, the plain sentences tests dispatch are read as they mean
        # (``_read_like_a_model``). Kept apart as well: asked only while there is such work.
        self.under_way: list[Any] = []
        self.under_way_calls: list[list[dict[str, Any]]] = []
        # What an answer to a request for approval chose, read apart, like ``under_way`` (without any,
        # the bare answers tests dispatch: ``_choose_like_a_model``).
        self.choices: list[Any] = []
        self.choice_calls: list[list[dict[str, Any]]] = []
        self.short_calls: list[list[dict[str, Any]]] = []
        # Answers of the task agent space's own calls (the review of a
        # finished commission, the summary of an archived project): dicts,
        # strings or exceptions, in order; the last one repeats.
        self.space: dict[str, list[Any]] = {"review": [], "archive": []}
        self.space_calls: list[tuple[str, list[dict[str, Any]]]] = []

    def count(self, tier: str | None = None) -> int:
        return sum(1 for called, _ in self.calls if tier is None or called == tier)

    def tiers(self) -> list[str]:
        return [tier for tier, _ in self.calls]

    def last(self, tier: str) -> list[dict[str, Any]]:
        return next(messages for called, messages in reversed(self.calls) if called == tier)

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        if tier == "short_description":
            # Written in the background whenever a plugin without one is
            # listed; kept apart so it does not change what the other tests
            # count. Answers nothing usable unless a test sets ``short``.
            self.short_calls.append([dict(message) for message in messages])
            answer = self.short.pop(0) if self.short else ""
            if isinstance(answer, BaseException):
                raise answer
            return answer
        if tier == "side_question":
            self.side_calls.append([dict(message) for message in messages])
            answer = self.side[min(len(self.side_calls) - 1, len(self.side) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if tier == "beside":
            self.beside_calls.append([dict(message) for message in messages])
            answer = self.beside[min(len(self.beside_calls) - 1, len(self.beside) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if tier == "answer_choice":
            self.choice_calls.append([dict(message) for message in messages])
            if not self.choices:
                return json.dumps(_choose_like_a_model(messages))
            answer = self.choices[min(len(self.choice_calls) - 1, len(self.choices) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if tier == "under_way":
            self.under_way_calls.append([dict(message) for message in messages])
            if not self.under_way:
                return json.dumps(_read_like_a_model(messages))
            answer = self.under_way[min(len(self.under_way_calls) - 1, len(self.under_way) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if tier == "orchestrate":
            self.belongs_calls.append([dict(message) for message in messages])
            answer = self.belongs[min(len(self.belongs_calls) - 1, len(self.belongs) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if tier in self.space:
            # Made in the background after a commission ended; kept apart
            # like the short descriptions. Answers nothing usable unless a
            # test sets ``space``.
            self.space_calls.append((tier, [dict(message) for message in messages]))
            answers = self.space[tier]
            made = sum(1 for called, _ in self.space_calls if called == tier)
            answer = answers[min(made - 1, len(answers) - 1)] if answers else ""
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
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
    # The built-in free key: requests go against the free daily quota, as a
    # meter given to the host counts them (``role_models.is_free_key``).
    return {"model": "fake-model", "api_key": "free-access"}


async def no_model() -> None:
    return None


def _copy_news_head() -> str:
    from config.prompts.prompts_nekopower_brain import TASK_AGENT_FORKED_WORK

    return TASK_AGENT_FORKED_WORK.split("{items}")[0][:60]


_COPY_NEWS = _copy_news_head()


class _ScriptOfTheCommission:
    """A session's model that answers from the script of the commission the session is at work on."""

    def __init__(self, scripts: dict[str, ScriptedLLM], seen: list[str] | None) -> None:
        self._scripts = scripts
        self._seen = seen
        self._current: str | None = None

    def _key(self, messages: list[dict[str, Any]]) -> str:
        # The last message that names a commission decides: what a commission
        # is stands in the message that opens it, which stays in the conversation.
        for message in reversed(messages):
            if message.get("role") == "system":
                continue
            text = json.dumps(message.get("content"), ensure_ascii=False)
            if _COPY_NEWS in text and "Commission " not in text:
                # What copies of the session did, told to it while it is at
                # another commission: it quotes their words, and is no
                # commission's.
                continue
            found = [key for key in self._scripts if key in text]
            if found:
                # The one named first: a commission's opening message begins with
                # what was dispatched, and may go on to name the one it follows up.
                return min(found, key=text.index)
        raise AssertionError(f"no script for a session asked: {messages[-1].get('content')!r}")

    async def astream(self, messages: list[dict[str, Any]], **overrides: Any):  # noqa: ANN201
        key = self._key(messages)
        if key != self._current:
            self._current = key
            if self._seen is not None:
                self._seen.append(key)
        async for chunk in self._scripts[key].astream(messages, **overrides):
            yield chunk

    async def aclose(self) -> None:
        return None


def scripts_by_commission(scripts: dict[str, ScriptedLLM], seen: list[str] | None = None) -> Callable[[Any], Any]:
    """Return a ``pick`` for ``scripted_runtime``: every session answers from the script of its commission.

    ``scripts`` maps a text that occurs in what was dispatched to the model
    that commission talks to. A session's spec says nothing of a commission
    (its system prompt is the same whichever commission is worked on in
    it), so the script is chosen when a request arrives, by the commission
    the request is about. ``seen`` collects the keys in the order the
    commissions were first asked about.
    """

    def pick(spec: Any) -> Any:
        del spec
        return _ScriptOfTheCommission(scripts, seen)

    return pick


def scripted_runtime(pick: Callable[[Any], ScriptedLLM]) -> Callable[[Any, Any], Any]:
    """Return a runtime factory: the in-process runtime whose sessions use scripted models.

    ``pick`` receives a session's spec and returns the model that session talks to.
    """
    created: list[Any] = []

    def factory(on_event: Any, host: Any) -> Any:
        from nekopower.runtime.core import InProcessRuntime
        from nekopower.runtime.model import LLMClientProvider
        from nekopower.runtime.session import TaskAgentSession

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
    from nekopower.host.service import NekoPowerHost

    kwargs.setdefault("root", tmp_path / "nekopower")
    kwargs.setdefault("task_agent_model", no_model)
    if server is not None:
        kwargs.setdefault("client_factory", server.client)
    else:

        def unreachable(**_kwargs: Any) -> Any:
            raise RuntimeError("no plugin server in this test")

        kwargs.setdefault("client_factory", unreachable)
    return NekoPowerHost(**kwargs)


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


async def let_go(host: Any) -> None:
    """Wait until no commission holds a session and no background work started by ``_spawn`` is left.

    A session is let go in a task of its own (``_settle_hold``) that goes on
    after the hold is gone: it reads the model the session is sent to, to
    set its idle timer (``_schedule_idle_compact``). A test that swaps
    ``_model_config`` right after this, to hold a start up while it reads
    the model, would otherwise catch that read first on a slow machine,
    before the start it waits for has taken its place.

    This waits for every task in ``_starting``, not only the ones letting a
    session go set off (a retry, an idle summary, a copy being closed are
    there too), and for both to be true at the same moment: such a task may
    start the next queued commission, which holds its session again.
    """
    tasks = host.task_agents
    await wait_until(
        lambda: tasks._held_by == {} and not tasks._starting,
        what="every session being let go and every task started by _spawn being done",
    )


async def outbox(host: Any, kind: str | None = None) -> list[Any]:
    return [item for item in await host.ledger.outbox_pending() if kind is None or item.kind == kind]
