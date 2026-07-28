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


@pytest.mark.asyncio
async def test_cache_hands_outbox_the_undeclared_language_as_none(monkeypatch):
    """请求未声明 locale 时，交给 outbox 的必须是 None 而不是本进程的探测值。"""
    spawn = AsyncMock()
    monkeypatch.setattr(
        routes.runtime,
        "recent_history_manager",
        SimpleNamespace(update_history=AsyncMock()),
    )
    monkeypatch.setattr(
        routes.runtime,
        "time_manager",
        SimpleNamespace(astore_conversation=AsyncMock()),
    )
    monkeypatch.setattr(routes.runtime, "_get_settle_lock", lambda _name: asyncio.Lock())
    monkeypatch.setattr(routes.gates, "_touch_activity", lambda: None)
    monkeypatch.setattr(routes.gates, "_aclear_review_clean", AsyncMock())
    monkeypatch.setattr(routes.post_turn, "_spawn_outbox_post_turn_signals", spawn)

    history = '[{"type": "human", "data": {"content": "喵"}}]'
    await routes.cache_conversation(
        routes.HistoryRequest(input_history=history, language=None), "小天"
    )

    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["language"] is None

    spawn.reset_mock()
    await routes.cache_conversation(
        routes.HistoryRequest(input_history=history, language="ja"), "小天"
    )
    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["language"] == "ja"


def test_outbox_enqueue_persists_only_client_declared_language():
    """四个写路由必须把 request.language 原值交给 outbox，而不是回落后的 memory_language。

    memory_language 在请求未声明 locale 时等于 get_global_language_full() 的探测
    结果。把它写进 outbox.ndjson 会让这个「猜测」被永久冻结：重启 replay 一直复用
    它，即使探测本身后来修好也不会自愈。省掉该键才能让 replay 按当时的进程语言
    重新解析（即 outbox 引入之前的行为）。

    用 AST 盯调用点而非只测 _spawn_outbox_post_turn_signals 自身：后者只能证明
    「传 None 就不写 payload」，证明不了路由真的传了 None。
    """
    source = routes.__file__
    assert source is not None
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    expected_handlers = {
        "cache_conversation",
        "process_conversation",
        "process_conversation_for_renew",
        "settle_conversation",
    }
    passed_expr: dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in expected_handlers:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called != "_spawn_outbox_post_turn_signals":
                continue
            for keyword in child.keywords:
                if keyword.arg == "language":
                    passed_expr[node.name] = ast.dump(keyword.value)

    missing = expected_handlers - set(passed_expr)
    assert not missing, f"这些写路由没有把 language 交给 outbox: {sorted(missing)}"
    for handler, dumped in sorted(passed_expr.items()):
        assert "attr='language'" in dumped and "id='request'" in dumped, (
            f"{handler} 应把 request.language 原值交给 outbox（不要传回落后的 "
            f"memory_language，那会把探测值冻进 outbox.ndjson），实际传的是: {dumped}"
        )


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
