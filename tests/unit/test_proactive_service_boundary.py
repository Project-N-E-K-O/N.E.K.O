# -*- coding: utf-8 -*-

"""Boundary contracts for the proactive-chat service and HTTP adapter."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import APP_NAME
from main_logic.proactive_chat import (
    break_reminders,
    contracts,
    decisions,
    delivery,
    generation,
    mini_game_invite,
    music_recommendation,
    service,
    state,
)
from main_routers import system_router as system_router_facade
from main_routers.system_router import proactive_chat_flow

_CHARACTER_DATA = (
    "博士",
    "Yui",
    None,
    None,
    None,
    {},
    None,
    None,
    None,
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_service_has_no_http_or_router_dependency() -> None:
    modules = _imported_modules(Path(service.__file__))

    forbidden = {
        module
        for module in modules
        if module == "fastapi"
        or module.startswith("fastapi.")
        or module == "main_routers"
        or module.startswith("main_routers.")
    }
    assert forbidden == set()


@pytest.mark.parametrize(
    "module",
    (
        break_reminders,
        decisions,
        delivery,
        generation,
        mini_game_invite,
        music_recommendation,
        service,
        state,
    ),
)
def test_proactive_domain_logs_to_main_service(module) -> None:
    assert module.logger.name == f"{APP_NAME}.Main.{module.__name__}"


def test_proactive_router_is_a_thin_ordered_adapter() -> None:
    source = inspect.getsource(proactive_chat_flow.proactive_chat)

    required_in_order = (
        "_validate_local_mutation_request",
        "aget_character_data",
        "request.json",
        "ProactiveChatCommand.from_payload",
        "handle_proactive_chat",
        "_adapt_result",
    )
    positions = [source.index(anchor) for anchor in required_in_order]
    assert positions == sorted(positions)

    orchestration_anchors = (
        "try_start_proactive",
        "_generate_phase2_stream",
        "_guard_phase2_output",
        "_commit_proactive_delivery",
        "_record_committed_delivery",
        "finish_proactive_delivery",
        "fetch_trending_content",
        "fetch_window_context_content",
    )
    assert not [anchor for anchor in orchestration_anchors if anchor in source]
    assert "JSONResponse" in inspect.getsource(proactive_chat_flow._adapt_result)
    service_source = inspect.getsource(service.handle_proactive_chat)
    assert ".websocket" not in service_source
    assert ".send_json(" not in service_source
    assert service_source.count("push_mini_game_invite_options(") >= 2


def _wire_router_dependencies(monkeypatch, handle_result) -> tuple[object, object]:
    config_manager = SimpleNamespace(
        aget_character_data=AsyncMock(return_value=_CHARACTER_DATA),
    )
    session_manager = SimpleNamespace()
    monkeypatch.setattr(
        proactive_chat_flow,
        "_validate_local_mutation_request",
        lambda request: None,
    )
    monkeypatch.setattr(
        proactive_chat_flow,
        "get_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setattr(
        proactive_chat_flow,
        "get_session_manager",
        lambda: session_manager,
    )
    monkeypatch.setattr(
        proactive_chat_flow.proactive_service,
        "handle_proactive_chat",
        handle_result,
    )
    return config_manager, session_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (200, 409, 500, 504))
async def test_router_adapts_service_status_and_body_verbatim(
    monkeypatch,
    status_code: int,
) -> None:
    body = {"status_marker": status_code, "nested": {"preserved": True}}
    handle = AsyncMock(
        return_value=contracts.ProactiveChatResult(
            body=body,
            status_code=status_code,
        )
    )
    config_manager, session_manager = _wire_router_dependencies(
        monkeypatch,
        handle,
    )
    request = SimpleNamespace(
        json=AsyncMock(return_value={"lanlan_name": "Yui"}),
    )

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == status_code
    assert json.loads(response.body) == body
    command = handle.await_args.args[0]
    kwargs = handle.await_args.kwargs
    assert command == contracts.ProactiveChatCommand.from_payload(
        {"lanlan_name": "Yui"}
    )
    assert kwargs["config_manager"] is config_manager
    assert kwargs["session_manager"] is session_manager
    assert kwargs["character_data"] == _CHARACTER_DATA
    assert (
        kwargs["break_config_manager_provider"]
        is proactive_chat_flow.get_config_manager
    )
    assert (
        kwargs["meme_proxy_candidate_fetchable"]
        is proactive_chat_flow._meme_proxy_candidate_fetchable
    )


@pytest.mark.asyncio
async def test_router_snapshots_character_data_before_reading_payload(
    monkeypatch,
) -> None:
    handle = AsyncMock(
        return_value=contracts.ProactiveChatResult(body={"success": True})
    )
    config_manager, _ = _wire_router_dependencies(monkeypatch, handle)
    mutable_character_data = list(_CHARACTER_DATA)
    config_manager.aget_character_data.return_value = mutable_character_data

    async def _read_payload():
        mutable_character_data[0] = "mutated-during-body-read"
        return {"lanlan_name": "Yui"}

    request = SimpleNamespace(json=AsyncMock(side_effect=_read_payload))

    await proactive_chat_flow.proactive_chat(request)

    assert handle.await_args.kwargs["character_data"][0] == "博士"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("character_data", "expected_detail"),
    (
        ([None] * 8, "not enough values to unpack (expected 9, got 8)"),
        ([None] * 10, "too many values to unpack (expected 9)"),
    ),
)
async def test_router_unpacks_character_data_before_reading_payload(
    monkeypatch,
    character_data,
    expected_detail: str,
) -> None:
    handle = AsyncMock()
    config_manager, _ = _wire_router_dependencies(monkeypatch, handle)
    config_manager.aget_character_data.return_value = character_data
    request = SimpleNamespace(json=AsyncMock(return_value={"lanlan_name": "Yui"}))

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == 500
    assert json.loads(response.body)["detail"] == expected_detail
    request.json.assert_not_awaited()
    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_maps_pre_service_timeout_to_504(monkeypatch) -> None:
    handle = AsyncMock()
    _wire_router_dependencies(monkeypatch, handle)
    request = SimpleNamespace(json=AsyncMock(side_effect=asyncio.TimeoutError))

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == 504
    assert json.loads(response.body) == {
        "success": False,
        "reason_code": contracts.PROACTIVE_REASON_ERROR_TIMEOUT,
        "stage": contracts.PROACTIVE_STAGE_RUNTIME_ERROR,
        "error": "AI处理超时",
    }
    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_maps_pre_service_failure_to_500(monkeypatch) -> None:
    handle = AsyncMock()
    _wire_router_dependencies(monkeypatch, handle)
    request = SimpleNamespace(json=AsyncMock(side_effect=RuntimeError("bad json")))

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == 500
    assert json.loads(response.body) == {
        "success": False,
        "reason_code": contracts.PROACTIVE_REASON_ERROR_INTERNAL,
        "stage": contracts.PROACTIVE_STAGE_RUNTIME_ERROR,
        "error": "服务器内部错误",
        "detail": "bad json",
    }
    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_preserves_malformed_list_error_detail(monkeypatch) -> None:
    handle = AsyncMock()
    _wire_router_dependencies(monkeypatch, handle)
    request = SimpleNamespace(json=AsyncMock(return_value=[]))

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["reason_code"] == contracts.PROACTIVE_REASON_ERROR_INTERNAL
    assert body["detail"] == "'list' object has no attribute 'get'"
    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_missing_manager_returns_domain_result() -> None:
    result = await service.handle_proactive_chat(
        contracts.ProactiveChatCommand(lanlan_name="Missing"),
        config_manager=SimpleNamespace(),
        session_manager=SimpleNamespace(get=lambda lanlan_name: None),
        character_data=_CHARACTER_DATA,
        game_route_active_for=lambda lanlan_name: False,
        break_config_manager_provider=lambda: SimpleNamespace(),
        run_mini_game_invite_short_circuit=AsyncMock(),
        push_mini_game_invite_options=AsyncMock(),
        push_mini_game_invite_resolved=AsyncMock(),
    )

    assert type(result) is contracts.ProactiveChatResult
    assert result.status_code == 404
    assert result.body == {
        "success": False,
        "reason_code": contracts.PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
        "stage": contracts.PROACTIVE_STAGE_ENTRY_GUARD,
        "error": "角色 Missing 不存在",
    }


@pytest.mark.parametrize(
    ("name", "canonical"),
    (
        ("build_proactive_response", decisions.build_proactive_response),
        ("_open_threads_for_activity_state", service._open_threads_for_activity_state),
        ("_render_followup_topic_hooks", service._render_followup_topic_hooks),
        ("_resolve_proactive_locale", service._resolve_proactive_locale),
        ("_resolve_topic_hook_locale", service._resolve_topic_hook_locale),
    ),
)
def test_compatibility_helpers_preserve_object_identity(name, canonical) -> None:
    assert getattr(proactive_chat_flow, name) is canonical
    assert getattr(system_router_facade, name) is canonical


def test_locale_helpers_accept_legacy_data_keyword_from_all_import_paths() -> None:
    mgr = SimpleNamespace(user_language="zh-CN")

    for resolver in (
        service._resolve_proactive_locale,
        proactive_chat_flow._resolve_proactive_locale,
        system_router_facade._resolve_proactive_locale,
    ):
        assert resolver(data={"language": "en"}, mgr=mgr) == "en"

    for resolver in (
        service._resolve_topic_hook_locale,
        proactive_chat_flow._resolve_topic_hook_locale,
        system_router_facade._resolve_topic_hook_locale,
    ):
        assert resolver(data={"language": "zh-TW"}, mgr=mgr, fallback="zh") == "zh-TW"


def test_safe_fire_proactive_done_is_exported_from_legacy_paths() -> None:
    assert (
        system_router_facade._safe_fire_proactive_done
        is proactive_chat_flow._safe_fire_proactive_done
    )


@pytest.mark.asyncio
async def test_safe_fire_proactive_done_preserves_legacy_scope_contract() -> None:
    done_event = object()
    fire = AsyncMock()
    scope = {
        "mgr": SimpleNamespace(state=SimpleNamespace(fire=fire)),
        "_SE": SimpleNamespace(PROACTIVE_DONE=done_event),
    }

    await proactive_chat_flow._safe_fire_proactive_done(scope)

    fire.assert_awaited_once_with(done_event)


@pytest.mark.asyncio
async def test_safe_fire_proactive_done_noops_before_start_or_after_done() -> None:
    fire = AsyncMock()
    populated_scope = {
        "mgr": SimpleNamespace(state=SimpleNamespace(fire=fire)),
        "_SE": SimpleNamespace(PROACTIVE_DONE=object()),
        "_proactive_done_emitted": True,
    }

    await proactive_chat_flow._safe_fire_proactive_done({})
    await proactive_chat_flow._safe_fire_proactive_done(populated_scope)

    fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_fire_proactive_done_swallows_and_logs_fire_errors(
    monkeypatch,
) -> None:
    warning = MagicMock()
    monkeypatch.setattr(proactive_chat_flow.logger, "warning", warning)
    scope = {
        "mgr": SimpleNamespace(
            state=SimpleNamespace(
                fire=AsyncMock(side_effect=RuntimeError("done failed")),
            )
        ),
        "_SE": SimpleNamespace(PROACTIVE_DONE=object()),
    }

    await proactive_chat_flow._safe_fire_proactive_done(scope)

    warning.assert_called_once()
    assert warning.call_args.args[0] == "safe_fire_proactive_done 异常: %s"
