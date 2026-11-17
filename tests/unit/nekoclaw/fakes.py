"""Deterministic stand-ins for the model layer, shared by the runtime tests.

``ScriptedLLM`` replaces the client ``utils.llm_client`` would create: each
model request replays the next scripted list of ``LLMStreamChunk``. No test in
this directory reads real configuration, uses an API key or opens a connection
to a model provider.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nekoclaw.runtime.host import HostBridge
from nekoclaw.runtime.model import LLMClientProvider
from nekoclaw.runtime.protocol import TaskAgentSpec, ModelConfig
from nekoclaw.runtime.session import TaskAgentSession
from utils.llm_client.messages import LLMStreamChunk

Step = Any  # an LLMStreamChunk, an exception to raise, or an async callable to await


def say(text: str) -> LLMStreamChunk:
    return LLMStreamChunk(content=text)


def think(text: str) -> LLMStreamChunk:
    return LLMStreamChunk(content="", reasoning_content=text)


def call(
    name: str,
    arguments: dict[str, Any] | str,
    call_id: str = "call-1",
    *,
    index: int = 0,
    extra_content: dict[str, Any] | None = None,
) -> LLMStreamChunk:
    """One complete tool call in a single fragment."""
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    fragment: dict[str, Any] = {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": text},
    }
    if extra_content is not None:
        fragment["extra_content"] = extra_content
    return LLMStreamChunk(content="", tool_call_deltas=[fragment])


def finish(reason: str = "stop") -> LLMStreamChunk:
    return LLMStreamChunk(content="", finish_reason=reason)


def usage(prompt: int, completion: int = 10) -> LLMStreamChunk:
    data = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    return LLMStreamChunk(content="", usage_metadata=data)


class ScriptedLLM:
    """Replays one scripted stream per model request and records the requests."""

    def __init__(self, turns: list[list[Step]] | None = None) -> None:
        self.turns: list[list[Step]] = list(turns or [])
        self.requests: list[list[dict[str, Any]]] = []
        self.overrides: list[dict[str, Any]] = []
        self.closed_streams = 0

    def add(self, *steps: Step) -> None:
        self.turns.append(list(steps))

    async def astream(self, messages: list[dict[str, Any]], **overrides: Any):  # noqa: ANN201
        self.requests.append(copy.deepcopy(messages))
        self.overrides.append(dict(overrides))
        steps = self.turns.pop(0) if self.turns else [say("(script exhausted)"), finish()]
        if callable(steps):
            # A turn may be computed when its request arrives, e.g. to use an id
            # that only exists by then.
            steps = steps()
        try:
            for step in steps:
                if isinstance(step, BaseException):
                    raise step
                if callable(step):
                    await step()
                    continue
                yield step
        finally:
            self.closed_streams += 1

    async def aclose(self) -> None:
        return None

    def last_request(self) -> list[dict[str, Any]]:
        return self.requests[-1]


def make_spec(tmp_path: Path, session_id: str = "s1", **overrides: Any) -> TaskAgentSpec:
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    model = overrides.pop("model", None) or ModelConfig(model="fake-model", api_key="test-key")
    return TaskAgentSpec(
        session_id=session_id,
        workdir=str(workdir),
        session_dir=str(tmp_path / "sessions" / session_id),
        model=model,
        **overrides,
    )


class Recorder:
    """Collects runtime events and lets a test wait for one."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._changed.set()

    def of(self, kind: str, session_id: str | None = None) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if event.get("type") == kind
            and (session_id is None or event.get("session_id") == session_id)
        ]

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 20.0,
        *,
        start: int = 0,
    ) -> dict[str, Any]:
        """Return the first event at or after ``start`` that satisfies ``predicate``."""
        deadline = asyncio.get_running_loop().time() + timeout
        seen = start
        while True:
            for event in self.events[seen:]:
                if predicate(event):
                    return event
            seen = len(self.events)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                kinds = [event.get("type") for event in self.events]
                raise AssertionError(f"no matching event within {timeout}s; saw {kinds}")
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def wait_ended(
        self, session_id: str | None = None, timeout: float = 20.0, *, start: int = 0
    ) -> dict[str, Any]:
        return await self.wait_for(
            lambda event: event.get("type") == "ended"
            and (session_id is None or event.get("session_id") == session_id),
            timeout,
            start=start,
        )


async def open_session(
    spec: TaskAgentSpec,
    llm: ScriptedLLM,
    host: HostBridge | None = None,
    **kwargs: Any,
) -> tuple[TaskAgentSession, Recorder]:
    """Open a session whose model is ``llm``."""
    from nekoclaw.runtime.host import NoHost

    recorder = Recorder()

    async def factory() -> ScriptedLLM:
        return llm

    provider = LLMClientProvider(spec.model, client_factory=factory)
    session = await TaskAgentSession.open(
        spec, host if host is not None else NoHost(), recorder, model=provider, **kwargs
    )
    return session, recorder


def allow_all() -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """A ``decide_permission`` callback that approves everything."""

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id, payload
        return {"allow": True}

    return decide
