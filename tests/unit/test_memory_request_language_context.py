from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.memory_server.routes as routes
from utils import language_utils
from utils.language_utils import get_global_language_full


pytestmark = pytest.mark.unit


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


def test_all_memory_write_routes_install_request_language_context():
    source = routes.__file__
    assert source is not None
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    expected_handlers = {
        "cache_conversation",
        "process_conversation",
        "process_conversation_for_renew",
        "settle_conversation",
    }
    scoped_handlers: set[str] = set()

    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in expected_handlers:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.With):
                continue
            for item in child.items:
                context_expr = item.context_expr
                if (
                    isinstance(context_expr, ast.Call)
                    and isinstance(context_expr.func, ast.Name)
                    and context_expr.func.id == "language_context"
                    and len(context_expr.args) == 1
                    and isinstance(context_expr.args[0], ast.Name)
                    and context_expr.args[0].id == "memory_language"
                ):
                    scoped_handlers.add(node.name)

    assert scoped_handlers == expected_handlers
