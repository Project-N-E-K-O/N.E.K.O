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
from main_logic import music_playback, music_requests
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
from main_routers.system_router import break_reminders as break_reminder_adapter
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
    ("channel", "expected_marker"),
    (
        ("anti_slack", "mark_anti_slack_used"),
        ("work_break", "mark_work_break_used"),
        ("work_break_game_invite", "mark_work_break_used"),
    ),
)
def test_repeat_suppressed_break_reminder_consumes_pending_source(
    channel: str,
    expected_marker: str,
) -> None:
    tracker = SimpleNamespace(
        mark_anti_slack_used=MagicMock(),
        mark_work_break_used=MagicMock(),
    )
    mgr = SimpleNamespace(_activity_tracker=tracker)

    service._consume_repeat_suppressed_break_reminder(
        mgr,
        lanlan_name="Neko",
        channel=channel,
    )

    getattr(tracker, expected_marker).assert_called_once_with()
    other_marker = (
        "mark_work_break_used"
        if expected_marker == "mark_anti_slack_used"
        else "mark_anti_slack_used"
    )
    getattr(tracker, other_marker).assert_not_called()


def test_all_break_reminder_branches_consume_repeat_suppression() -> None:
    source = inspect.getsource(service.handle_proactive_chat)
    assert source.count("if reminder_delivery.repeat_suppressed:") == 3
    assert source.count("_consume_repeat_suppressed_break_reminder(") == 3


@pytest.mark.asyncio
async def test_break_reminder_router_adapter_preserves_tuple_contract(
    monkeypatch,
) -> None:
    domain_delivery = AsyncMock(
        return_value=break_reminders.BreakReminderDeliveryResult(
            delivered_text="起来活动一下吧。",
            proactive_sid="break-sid",
            repeat_suppressed=True,
        )
    )
    config_manager = object()
    monkeypatch.setattr(
        break_reminder_adapter,
        "_deliver_break_reminder_via_llm_domain",
        domain_delivery,
    )
    monkeypatch.setattr(
        break_reminder_adapter,
        "get_config_manager",
        lambda: config_manager,
    )

    result = await break_reminder_adapter._deliver_break_reminder_via_llm(
        lanlan_name="Neko",
        mgr=object(),
        system_prompt="prompt",
        channel="work_break",
        lang="zh",
    )

    assert result == ("起来活动一下吧。", "break-sid")
    assert isinstance(result, tuple)
    assert domain_delivery.await_args.kwargs["config_manager"] is config_manager


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


@pytest.mark.parametrize(
    ("value", "keyword", "song", "artist", "playlist", "source", "strict"),
    (
        ("source:liked", "", "", "", "", "liked", True),
        ("source：daily", "", "", "", "", "daily", True),
        ("playlist:夜间循环", "", "", "", "夜间循环", "auto", True),
        ("song:晴天|周杰伦", "晴天 周杰伦", "晴天", "周杰伦", "", "auto", True),
        ("personalized", "", "", "", "", "auto", False),
        ("周杰伦", "周杰伦", "", "", "", "auto", False),
    ),
)
def test_parse_music_request_directives(
    value,
    keyword,
    song,
    artist,
    playlist,
    source,
    strict,
) -> None:
    request = music_recommendation._parse_music_request(value)

    assert request.keyword == keyword
    assert request.song_name == song
    assert request.song_artist == artist
    assert request.playlist_name == playlist
    assert request.personalization_source == source
    assert request.strict is strict


@pytest.mark.asyncio
async def test_strict_music_request_does_not_fall_back(monkeypatch) -> None:
    fetch = AsyncMock(return_value={"success": False, "data": []})
    monkeypatch.setattr(music_recommendation, "fetch_music_content", fetch)

    result = await music_recommendation._fetch_music_with_fallback("source:liked")

    assert result is None
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_recent_user_query_skips_proactive_search(monkeypatch) -> None:
    scope = "YUI-query-dedupe"
    user_request = music_requests.MusicRequest(
        keyword="童年",
        song_name="童年",
    )
    music_requests.mark_music_request_query(scope, user_request)
    fetch = AsyncMock()
    monkeypatch.setattr(music_recommendation, "fetch_music_content", fetch)

    result = await music_recommendation._fetch_music_with_fallback(
        "童年",
        lanlan_name=scope,
    )

    assert result is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_proactive_query_is_remembered(monkeypatch) -> None:
    scope = "YUI-proactive-query"
    fetch = AsyncMock(
        return_value={
            "success": True,
            "data": [{"name": "童年", "url": "/music/childhood"}],
        }
    )
    monkeypatch.setattr(music_recommendation, "fetch_music_content", fetch)

    result = await music_recommendation._fetch_music_with_fallback(
        "童年",
        lanlan_name=scope,
    )

    assert result is not None
    assert result["_strict_song_request"] is False
    assert music_requests.was_music_request_recent(
        scope,
        music_requests.MusicRequest(keyword="童年"),
    )


@pytest.mark.asyncio
async def test_music_failsafe_only_applies_to_strict_song_request(
    monkeypatch,
) -> None:
    fuzzy_content = {
        "raw_data": {
            "best_match": {"status": "fuzzy"},
        }
    }

    normal_context = music_recommendation._build_music_dynamic_context(
        selected_music_link={"title": "Track"},
        music_content=fuzzy_content,
        is_playing_music=False,
        master_name="User",
        lang="zh",
    )
    fetch = AsyncMock(
        return_value={
            "success": True,
            "data": [{"name": "童年", "url": "/music/childhood"}],
            "best_match": {"status": "fuzzy"},
        }
    )
    monkeypatch.setattr(music_recommendation, "fetch_music_content", fetch)
    strict_content, _ = await generation._fetch_phase1_followups(
        parsed={"music_keyword": "song:童年", "music_pass": False},
        has_music_task=True,
        has_meme_task=False,
        music_content=None,
        meme_content=None,
        proactive_lang="zh",
        lanlan_name="YUI-fuzzy-data-flow",
    )
    strict_context = music_recommendation._build_music_dynamic_context(
        selected_music_link={"title": "Track"},
        music_content=strict_content,
        is_playing_music=False,
        master_name="User",
        lang="zh",
    )

    assert strict_content["raw_data"]["_strict_song_request"] is True
    assert "未找到与关键词精准匹配" not in normal_context
    assert "未找到与关键词精准匹配" in strict_context


@pytest.mark.asyncio
async def test_controlled_music_request_preserves_failure_reason() -> None:
    fetch = AsyncMock(
        return_value={
            "success": False,
            "error_code": "cookie_invalid",
            "data": [],
        }
    )

    result = await music_requests.fetch_music_request(
        music_requests.MusicRequest(personalization_source="liked"),
        fetcher=fetch,
        include_failure=True,
    )

    assert result["error_code"] == "cookie_invalid"


def test_music_playback_keeps_core_entrypoints_thin() -> None:
    core_dir = Path(__file__).parents[2] / "main_logic" / "core"
    streaming_source = (core_dir / "streaming.py").read_text(encoding="utf-8")
    turn_source = (core_dir / "turn.py").read_text(encoding="utf-8")
    tool_source = (core_dir / "tool_calling.py").read_text(encoding="utf-8")

    assert "music_request" not in streaming_source
    assert "music_request" not in turn_source
    assert "_execute_music_request" not in tool_source
    assert "music_playback" not in tool_source
    assert "play_music" not in tool_source


def test_confirmed_music_playback_stays_passive() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )
    event = {
        "state": "playing",
        "playback_id": "player:1",
        "playback_window_id": "window:1",
        "playback_started_at": 100,
        "source": "proactive",
        "track": {"name": "大喜", "artist": "泠鸢yousa"},
    }

    assert music_playback.handle_music_playback_state(manager, event) is True
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["delivery_mode"] == "passive"
    assert callback["channel"] == "music_playback"
    assert "播放器已确认开始播放《大喜》（泠鸢yousa）" in callback["detail"]
    assert "不要再次调用音乐播放工具" not in callback["detail"]
    manager.submit_proactive_callback.assert_not_called()

    assert music_playback.handle_music_playback_state(manager, event) is False
    manager.enqueue_agent_callback.assert_called_once()


def test_non_user_music_state_is_passive_and_coalesced() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )

    assert music_playback.handle_music_playback_state(
        manager,
        {
            "state": "playing",
            "playback_id": "player:2",
            "playback_window_id": "window:2",
            "playback_started_at": 200,
            "source": "proactive",
            "track": {"name": "勾指起誓", "artist": "洛天依"},
        },
    ) is True

    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["delivery_mode"] == "passive"
    assert callback["coalesce_key"] == "music-playback-state:YUI"
    manager.submit_proactive_callback.assert_not_called()


@pytest.mark.parametrize(
    ("reported_reason", "expected_reason"),
    (("load_timeout", "load_timeout"), ("secret conversation", "unknown")),
)
def test_music_playback_error_logs_only_sanitized_reason(
    monkeypatch,
    reported_reason: str,
    expected_reason: str,
) -> None:
    warning = MagicMock()
    monkeypatch.setattr(music_playback.logger, "warning", warning)
    manager = SimpleNamespace(
        lanlan_name="YUI",
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )

    assert music_playback.handle_music_playback_state(
        manager,
        {
            "state": "error",
            "reason": reported_reason,
            "playback_id": "player:error",
            "playback_window_id": "window:error",
            "playback_started_at": 250,
            "source": "proactive",
            "track": {"name": "Track", "artist": "Artist"},
        },
    ) is True

    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["metadata"]["failure_reason"] == expected_reason
    warning.assert_called_once_with(
        "[%s] 音乐播放器报告失败: reason=%s",
        "YUI",
        expected_reason,
    )
    if expected_reason == "unknown":
        assert reported_reason not in repr(warning.call_args)


def test_music_playback_keeps_current_owner_until_track_finishes() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )
    event = {
        "state": "playing",
        "playback_id": "player:current",
        "playback_window_id": "window:current",
        "playback_started_at": 100,
        "source": "proactive",
    }

    assert music_playback.handle_music_playback_state(manager, event) is True

    event["state"] = "ended"
    assert music_playback.handle_music_playback_state(manager, event) is True
    assert manager.enqueue_agent_callback.call_count == 2
    manager.submit_proactive_callback.assert_not_called()


def test_music_playback_rejects_stale_windows() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )

    current_event = {
        "state": "playing",
        "playback_id": "player:new",
        "playback_window_id": "window:new",
        "playback_started_at": 200,
        "source": "proactive",
    }
    stale_window_event = {
        "state": "ended",
        "playback_id": "player:old",
        "playback_window_id": "window:old",
        "playback_started_at": 100,
        "source": "music_play_url",
    }
    assert music_playback.handle_music_playback_state(manager, current_event) is True
    assert music_playback.handle_music_playback_state(manager, stale_window_event) is False
    manager.enqueue_agent_callback.assert_called_once()


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


def test_music_dedupe_is_recorded_only_after_delivery_commit() -> None:
    source = inspect.getsource(service.handle_proactive_chat)

    commit = source.index("delivery_commit = await _commit_proactive_delivery(")
    committed = source.index("committed_delivery = delivery_commit.delivery")
    mark_played = source.index("mark_music_as_played(")
    record = source.index("recorded_result = await _record_committed_delivery(")
    assert commit < committed < mark_played < record
    assert "committed_delivery.is_music_used" in source
    assert "committed_delivery.delivered_music_link" in source
    assert "mark_music_as_played(track)" not in inspect.getsource(
        music_recommendation._select_music_recommendation
    )


def test_music_selection_trims_skipped_tracks_before_delivery_fallback() -> None:
    tracks = [
        {
            "name": "easy hiphop",
            "artist": "VibeDepot",
            "url": "https://freemusicarchive.org/track/easy-hiphop/stream/",
        },
        {
            "name": "Nocturne in B flat minor",
            "artist": "Pianist A",
            "url": "https://dl.musopen.org/nocturne-flat.mp3",
        },
        {
            "name": "Left Track",
            "artist": "Singer",
            "url": "/api/music/play/netease/123",
        },
        {
            "name": "calm hiphop",
            "artist": "VibeDepot",
            "url": "https://freemusicarchive.org/track/calm-hiphop/stream/",
        },
        {
            "name": "Nocturne in B minor",
            "artist": "Pianist B",
            "url": "https://dl.musopen.org/nocturne-minor.mp3",
        },
    ]
    skipped_urls = {track["url"] for track in tracks[:3]}
    music_content = {
        "formatted_content": "music candidates",
        "raw_data": {"success": True, "data": tracks},
    }

    selection = music_recommendation._select_music_recommendation(
        music_content,
        lang="en",
        source_hash=lambda url, _title: url,
        should_skip_source=lambda key: key in skipped_urls,
        lanlan_name="YUI",
    )

    assert selection.link["title"] == "calm hiphop"
    assert [
        track["name"] for track in selection.content["raw_data"]["data"]
    ] == ["calm hiphop", "Nocturne in B minor"]

    source_links = [selection.link]
    appended = music_recommendation._append_music_recommendations(
        source_links,
        selection.content,
    )

    assert appended == 1
    assert [link["title"] for link in source_links] == [
        "calm hiphop",
        "Nocturne in B minor",
    ]


def test_proactive_command_parses_music_occupied() -> None:
    command = contracts.ProactiveChatCommand.from_payload(
        {"is_music_occupied": True}
    )

    assert command.is_music_occupied is True


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "request_language",
        "render_language",
        "manager_language",
        "manager_explicit",
        "expected_language",
    ),
    (
        (None, "ja", "en", False, "ja"),
        (None, "ja", "zh-TW", True, "zh-TW"),
        ("pt", "ja", "zh-TW", True, "pt"),
    ),
)
async def test_voice_fast_path_passes_request_locale_without_mutating_manager(
    monkeypatch,
    request_language,
    render_language,
    manager_language,
    manager_explicit,
    expected_language,
) -> None:
    class _VoiceSession:
        pass

    monkeypatch.setattr(service, "OmniRealtimeClient", _VoiceSession)
    monkeypatch.setattr(
        service,
        "_advance_mini_game_invite_entry",
        lambda *_args, **_kwargs: None,
    )
    trigger = AsyncMock(return_value=False)
    manager = SimpleNamespace(
        is_active=True,
        session=_VoiceSession(),
        is_goodbye_silent=lambda: False,
        last_user_message_time=None,
        state=SimpleNamespace(can_start_proactive=lambda *, session: True),
        trigger_voice_proactive_nudge=trigger,
        user_language=manager_language,
        _user_language_explicit=manager_explicit,
        _conversation_render_language=None,
    )
    original_language_state = (
        manager.user_language,
        manager._user_language_explicit,
        manager._conversation_render_language,
    )

    await service.handle_proactive_chat(
        contracts.ProactiveChatCommand(
            lanlan_name="Yui",
            voice_mode=True,
            i18n_language=request_language,
            render_language=render_language,
        ),
        config_manager=SimpleNamespace(),
        session_manager=SimpleNamespace(get=lambda _lanlan_name: manager),
        character_data=_CHARACTER_DATA,
        game_route_active_for=lambda _lanlan_name: False,
        break_config_manager_provider=lambda: SimpleNamespace(),
        run_mini_game_invite_short_circuit=AsyncMock(),
        push_mini_game_invite_options=AsyncMock(),
        push_mini_game_invite_resolved=AsyncMock(),
    )

    trigger.assert_awaited_once_with(language=expected_language)
    assert (
        manager.user_language,
        manager._user_language_explicit,
        manager._conversation_render_language,
    ) == original_language_state


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
        assert resolver(
            data={"render_language": "zh-CN"},
            mgr=mgr,
            fmt="prompt",
        ) == "zh"
        assert resolver(
            data={"render_language": "zh-TW"},
            mgr=mgr,
            fmt="prompt",
        ) == "zh-TW"

    for resolver in (
        service._resolve_topic_hook_locale,
        proactive_chat_flow._resolve_topic_hook_locale,
        system_router_facade._resolve_topic_hook_locale,
    ):
        assert resolver(data={"language": "zh-TW"}, mgr=mgr, fallback="zh") == "zh-TW"


def test_topic_hook_locale_uses_render_language_without_declaring_it(monkeypatch) -> None:
    mgr = SimpleNamespace(user_language="en", _user_language_explicit=False)
    data = {"render_language": "zh-TW"}
    monkeypatch.setattr(service, "get_global_language_full", lambda: "en")

    assert service._resolve_topic_hook_locale(data, mgr, fallback="zh-CN") == "zh-TW"
    assert service._resolve_declared_topic_hook_locale(data, mgr) is None
    assert service._new_dialog_locale_params(data, mgr) == {
        "render_language": "zh-TW",
    }


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
