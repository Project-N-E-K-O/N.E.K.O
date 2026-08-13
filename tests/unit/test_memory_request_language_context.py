from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.memory_server.routes as routes
from utils import language_utils
from utils.language_utils import get_global_language_full


pytestmark = pytest.mark.unit


@pytest.fixture
def foreground_route_runtime(monkeypatch):
    observed = []
    spawn = AsyncMock()
    allocate = MagicMock(
        side_effect=AssertionError("request-only locale must not allocate an order")
    )

    async def update_history(*_args, **_kwargs):
        observed.append(get_global_language_full())

    monkeypatch.setattr(
        routes.runtime,
        "_config_manager",
        SimpleNamespace(
            aload_characters=AsyncMock(return_value={"猫娘": {"小天": {}}}),
        ),
    )
    monkeypatch.setattr(
        routes.runtime,
        "recent_history_manager",
        SimpleNamespace(update_history=update_history),
    )
    monkeypatch.setattr(
        routes.runtime,
        "time_manager",
        SimpleNamespace(astore_conversation=AsyncMock()),
    )
    monkeypatch.setattr(routes.runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(routes.runtime, "_get_settle_lock", lambda _name: asyncio.Lock())
    monkeypatch.setattr(routes.gates, "_touch_activity", lambda: None)
    monkeypatch.setattr(routes.gates, "_aclear_review_clean", AsyncMock())
    monkeypatch.setattr(routes.post_turn, "_spawn_outbox_post_turn_signals", spawn)
    monkeypatch.setattr(routes.review, "maybe_spawn_review", AsyncMock())
    monkeypatch.setattr(
        routes.locale_state,
        "allocate_character_prompt_locale_order",
        allocate,
    )

    def use_durable(language):
        monkeypatch.setattr(
            routes.locale_state,
            "get_character_prompt_locale",
            lambda _name: language,
        )

    return SimpleNamespace(
        observed=observed,
        spawn=spawn,
        allocate=allocate,
        use_durable=use_durable,
    )


def test_request_language_selection_does_not_mutate_process_default(monkeypatch):
    monkeypatch.setattr(language_utils, "_global_language", "zh")
    monkeypatch.setattr(language_utils, "_global_language_full", "zh-TW")
    monkeypatch.setattr(language_utils, "_global_language_initialized", True)

    assert routes._activate_request_language("ja") == "ja"
    assert get_global_language_full() == "zh-TW"
    assert routes._activate_request_language("not-a-locale") == "zh-TW"


@pytest.mark.asyncio
async def test_process_requests_keep_language_task_local_across_awaits(monkeypatch):
    both_requests_entered = asyncio.Event()
    entered_count = 0
    observed: dict[str, str] = {}

    async def aload_characters():
        nonlocal entered_count
        entered_count += 1
        if entered_count == 2:
            both_requests_entered.set()
        await both_requests_entered.wait()
        return {"猫娘": {"EnglishNeko": {}, "JapaneseNeko": {}}}

    async def update_history(_history, lanlan_name, **_kwargs):
        observed[lanlan_name] = get_global_language_full()

    monkeypatch.setattr(
        routes.runtime,
        "_config_manager",
        SimpleNamespace(aload_characters=aload_characters),
    )
    monkeypatch.setattr(
        routes.runtime,
        "recent_history_manager",
        SimpleNamespace(update_history=update_history),
    )
    monkeypatch.setattr(
        routes.runtime,
        "time_manager",
        SimpleNamespace(astore_conversation=AsyncMock()),
    )
    monkeypatch.setattr(routes.runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(routes.gates, "_touch_activity", lambda: None)
    monkeypatch.setattr(
        routes.post_turn,
        "_spawn_outbox_post_turn_signals",
        AsyncMock(),
    )
    monkeypatch.setattr(routes.review, "maybe_spawn_review", AsyncMock())

    english_result, japanese_result = await asyncio.wait_for(
        asyncio.gather(
            routes.process_conversation(
                routes.HistoryRequest(input_history="[]", language="en"),
                "EnglishNeko",
            ),
            routes.process_conversation(
                routes.HistoryRequest(input_history="[]", language="ja"),
                "JapaneseNeko",
            ),
        ),
        timeout=2,
    )

    assert english_result == {"status": "processed"}
    assert japanese_result == {"status": "processed"}
    assert observed == {
        "EnglishNeko": "en",
        "JapaneseNeko": "ja",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_name",
    [
        "process_conversation",
        "process_conversation_for_renew",
        "settle_conversation",
    ],
)
async def test_locale_less_foreground_routes_restore_durable_locale(
    foreground_route_runtime,
    endpoint_name,
):
    foreground_route_runtime.use_durable("zh-TW")

    with language_utils.language_context("en"):
        result = await getattr(routes, endpoint_name)(
            routes.HistoryRequest(
                input_history="[]",
                language=None,
                render_language="ja",
            ),
            "小天",
        )

    assert result["status"] in {"processed", "settled"}
    assert foreground_route_runtime.observed == ["zh-TW"]
    foreground_route_runtime.allocate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_name",
    [
        "cache_conversation",
        "process_conversation",
        "process_conversation_for_renew",
        "settle_conversation",
    ],
)
async def test_render_only_foreground_routes_keep_render_locale_out_of_durable_state(
    foreground_route_runtime,
    endpoint_name,
):
    foreground_route_runtime.use_durable(None)

    history = json.dumps(
        [{"type": "human", "data": {"content": "render this turn"}}]
    )
    request = routes.HistoryRequest(
        input_history=history,
        language=None,
        render_language="ja",
    )
    with language_utils.language_context("en"):
        result = await getattr(routes, endpoint_name)(request, "小天")

    assert result["status"] in {"cached", "processed", "settled"}
    assert foreground_route_runtime.observed == ["ja"]
    foreground_route_runtime.spawn.assert_awaited_once()
    assert foreground_route_runtime.spawn.await_args.kwargs["language"] is None
    assert (
        foreground_route_runtime.spawn.await_args.kwargs["render_language"]
        == "ja"
    )
    assert (
        foreground_route_runtime.spawn.await_args.kwargs["locale_admission_order"]
        is None
    )
    foreground_route_runtime.allocate.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_foreground_language_skips_durable_and_render_fallbacks(
    monkeypatch,
):
    monkeypatch.setattr(
        routes.locale_state,
        "get_character_prompt_locale",
        MagicMock(side_effect=AssertionError("explicit locale must short-circuit")),
    )

    assert await routes._resolve_foreground_memory_language(
        "Neko",
        "en",
        render_language="ja",
    ) == "en"


@pytest.mark.asyncio
async def test_cache_hands_outbox_the_undeclared_language_as_none(
    foreground_route_runtime,
):
    """An undeclared request locale must reach the outbox as None, not as a guess."""
    foreground_route_runtime.use_durable(None)
    spawn = foreground_route_runtime.spawn

    history = '[{"type": "human", "data": {"content": "喵"}}]'
    await routes.cache_conversation(
        routes.HistoryRequest(input_history=history, language=None), "小天"
    )

    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["language"] is None
    assert spawn.await_args.kwargs["render_language"] is None

    spawn.reset_mock()
    foreground_route_runtime.allocate.side_effect = None
    foreground_route_runtime.allocate.return_value = 1
    await routes.cache_conversation(
        routes.HistoryRequest(input_history=history, language="ja"), "小天"
    )
    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["language"] == "ja"
    assert spawn.await_args.kwargs["render_language"] is None
