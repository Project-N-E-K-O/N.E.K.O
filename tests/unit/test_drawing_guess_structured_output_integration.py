"""Drawing rules use the SDK retry lifecycle without sharing failed output."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from main_routers import game_router
from main_routers.game_router import drawing_guess as game
from utils import llm_client


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def drawing_plan():
    return {
        "version": 1, "width": 800, "height": 600, "background": "#eef8ff",
        "elements": [{"type": "circle", "cx": 400, "cy": 300, "r": 100,
                      "fill": "#ff0", "stroke": "#000", "opacity": 0.5}],
    }


async def generate(revision):
    kwargs = {"word": game._WORD_BY_ID["banana"], "locale": "en", "lanlan_name": "YUI"}
    if revision:
        return await game._generate_model_drawing_revision(
            **kwargs, original_plan=drawing_plan(),
            review={"guess_id": "apple", "confidence": 0.6, "issues": ["ambiguous shape"]},
        )
    return await game._generate_model_drawing(**kwargs)


def install_model(monkeypatch, outcomes, provider="openai"):
    clients, sdk_calls = [], []
    monkeypatch.setattr(game_router, "_get_character_info", lambda name: {
        "lanlan_name": name, "master_name": "Player", "lanlan_prompt": "A playful companion.",
        "model": "test-drawing", "base_url": "https://example.invalid/v1",
        "api_key": "test-key", "provider_type": provider,
    })

    class LLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, *_args):
            self.closed = True
            self.exit_type = exc_type

        async def ainvoke(self, messages):
            self.messages = messages
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            value = await self.outcome() if callable(self.outcome) else self.outcome
            return SimpleNamespace(content=value)

    async def create(*_args, **kwargs):
        assert all(client.closed for client in clients), "a retry reused an open provider client"
        client = LLM()
        client.closed = False
        client.outcome = outcomes[min(len(clients), len(outcomes) - 1)]
        client.options = kwargs
        clients.append(client)
        return client

    shared_runner = game.run_isolated_structured_output

    async def observe_sdk(*args, **kwargs):
        sdk_calls.append(kwargs)
        return await shared_runner(*args, **kwargs)

    monkeypatch.setattr(llm_client, "create_chat_llm_async", create)
    monkeypatch.setattr(game, "run_isolated_structured_output", observe_sdk)
    return clients, sdk_calls


@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("rejection", ["empty", "unparseable", "unsafe_plan"])
async def test_content_retry_uses_sdk_fresh_clients_and_isolated_messages(monkeypatch, revision, provider, rejection):
    rejected = "private-rejected-response"
    bad_plan = drawing_plan()
    bad_plan["elements"][0]["text"] = rejected
    first = {"empty": "", "unparseable": rejected, "unsafe_plan": json.dumps({"plan": bad_plan})}[rejection]
    clients, sdk_calls = install_model(monkeypatch, [first, json.dumps({"plan": drawing_plan()})], provider)

    result = await generate(revision)

    assert result["source"] == ("model_plan_revision" if revision else "model_plan")
    assert result["sanitizer"] == {"ok": True, "attempt": 2, **({"revision": 1} if revision else {})}
    assert result["plan"]["background"] == "#eef8ff"
    assert result["plan"]["elements"][0]["opacity"] == 0.5
    assert sdk_calls == [{"content_retries": 1}]
    assert len(clients) == 2
    assert all(client.closed for client in clients)
    assert clients[0].messages is not clients[1].messages
    assert clients[0].messages[0] is not clients[1].messages[0]
    assert clients[0].messages[0].content == clients[1].messages[0].content
    payloads = [json.loads(client.messages[1].content) for client in clients]
    assert [payload["structuredOutputAttempt"] for payload in payloads] == [1, 2]
    assert payloads[0]["structuredOutputIsolationId"] != payloads[1]["structuredOutputIsolationId"]
    assert all(payload["structuredOutputIsolationId"] for payload in payloads)
    assert "previous_rejection_reason" not in payloads[0]
    assert payloads[1]["previous_rejection_reason"]
    assert rejected not in clients[1].messages[1].content
    if revision:
        assert payloads[1]["original_plan"] == drawing_plan()
    for client in clients:
        assert len(client.messages) == 2
        assert client.messages[0].content.endswith("======以上为绘画游戏系统提示======")
        assert client.options["max_retries"] == 0
        assert client.options["max_completion_tokens"] == 4000
        assert client.options["timeout"] == 30
        assert client.options["provider_type"] == provider


@pytest.mark.parametrize("revision", [False, True])
async def test_sdk_stops_after_two_invalid_drawings(monkeypatch, revision):
    clients, sdk_calls = install_model(monkeypatch, ["unparseable"])

    assert await generate(revision) is None
    assert sdk_calls == [{"content_retries": 1}]
    assert len(clients) == 2
    assert all(client.closed for client in clients)


@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
async def test_sdk_does_not_retry_provider_failure(monkeypatch, revision, failure):
    clients, sdk_calls = install_model(monkeypatch, [failure("provider failed")])

    assert await generate(revision) is None
    assert sdk_calls == [{"content_retries": 1}]
    assert len(clients) == 1
    assert clients[0].closed


@pytest.mark.parametrize("revision", [False, True])
async def test_cancellation_closes_provider_without_starting_retry(monkeypatch, revision):
    started = asyncio.Event()

    async def pending():
        started.set()
        await asyncio.Future()

    clients, _ = install_model(monkeypatch, [pending])
    task = asyncio.create_task(generate(revision))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(clients) == 1
    assert clients[0].closed
    assert clients[0].exit_type is asyncio.CancelledError
