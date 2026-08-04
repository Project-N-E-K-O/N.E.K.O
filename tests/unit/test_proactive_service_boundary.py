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


@pytest.mark.parametrize(
    ("text", "keyword", "song", "artist", "playlist", "source"),
    (
        ("我想听邓紫棋的歌", "邓紫棋", "", "邓紫棋", "", "auto"),
        ("我想听轻松的音乐", "轻松", "", "", "", "auto"),
        ("来点舒缓的音乐", "舒缓", "", "", "", "auto"),
        ("播放《晴天》", "晴天", "晴天", "", "", "auto"),
        ("播放周杰伦的晴天", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("播放一首周杰伦的晴天", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("听一首周杰伦的晴天", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("放首周杰伦的晴天", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("播放周杰伦的晴天这首歌", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("听周杰伦的晴天歌曲", "晴天 周杰伦", "晴天", "周杰伦", "", "auto"),
        ("播放张学友的情歌", "情歌 张学友", "情歌", "张学友", "", "auto"),
        ("播放邓紫棋", "邓紫棋", "", "", "", "auto"),
        ("播放轻音乐", "轻音乐", "", "", "", "auto"),
        ("换成歌曲：大喜", "大喜", "大喜", "", "", "auto"),
        ("播放一首丑马", "丑马", "丑马", "", "", "auto"),
        ("听一首晴天", "晴天", "晴天", "", "", "auto"),
        ("请听一首晴天", "晴天", "晴天", "", "", "auto"),
        ("听首晴天", "晴天", "晴天", "", "", "auto"),
        ("放一下晴天", "晴天", "", "", "", "auto"),
        ("来一首丑马", "丑马", "丑马", "", "", "auto"),
        ("来一首邓紫棋的歌曲，下午好", "邓紫棋", "", "邓紫棋", "", "auto"),
        ("来一首歌曲：21", "21", "21", "", "", "auto"),
        ("来一首周杰伦的歌", "周杰伦", "", "周杰伦", "", "auto"),
        ("听一首周杰伦的歌", "周杰伦", "", "周杰伦", "", "auto"),
        ("听首周杰伦的歌", "周杰伦", "", "周杰伦", "", "auto"),
        ("放首周杰伦的歌", "周杰伦", "", "周杰伦", "", "auto"),
        ("从夜间循环里放一首", "", "", "", "夜间循环", "auto"),
        ("我想从夜间循环里放一首", "", "", "", "夜间循环", "auto"),
        ("我要从夜间循环歌单里听一首", "", "", "", "夜间循环", "auto"),
        ("播放夜间循环歌单", "", "", "", "夜间循环", "auto"),
        ("播放我的夜间循环歌单", "", "", "", "夜间循环", "auto"),
        ("来点我喜欢的", "", "", "", "", "liked"),
        ("来首歌", "", "", "", "", "auto"),
        ("放我的歌", "", "", "", "", "auto"),
        ("别放日推，只听红心", "", "", "", "", "liked"),
        ("我想听红心", "", "", "", "", "liked"),
        ("我要听日推", "", "", "", "", "daily"),
        ("听我的日推", "", "", "", "", "daily"),
        ("我想听我的日推", "", "", "", "", "daily"),
        ("我想听红心，别放日推", "", "", "", "", "liked"),
        ("播放晴天，别放日推", "晴天", "", "", "", "auto"),
        ("播放《别听慢歌》", "别听慢歌", "别听慢歌", "", "", "auto"),
        ("play Don't Stop the Music", "Don't Stop the Music", "", "", "", "auto"),
        ("play Yellow, don't play daily recommendations", "Yellow", "", "", "", "auto"),
        ("play Yellow and don't play daily recommendations", "Yellow", "", "", "", "auto"),
        ("play Yellow, don't play games", "Yellow", "", "", "", "auto"),
        ("播放晴天，别播放视频", "晴天", "", "", "", "auto"),
        ("play a song by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play music by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play a track by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play tunes by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play something by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play anything by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play any song by Coldplay", "Coldplay", "", "Coldplay", "", "auto"),
        ("play the song Yellow by Coldplay", "Yellow Coldplay", "Yellow", "Coldplay", "", "auto"),
        ("play a song Yellow by Coldplay", "Yellow Coldplay", "Yellow", "Coldplay", "", "auto"),
        ("play song Yellow by Coldplay", "Yellow Coldplay", "Yellow", "Coldplay", "", "auto"),
        ("play Song 2 by Blur", "Song 2 Blur", "Song 2", "Blur", "", "auto"),
        ("play my favorites", "", "", "", "", "liked"),
        ("play favorites", "", "", "", "", "liked"),
        ("play my favourites", "", "", "", "", "liked"),
        ("play a song from my liked songs", "", "", "", "", "liked"),
        ("play a track from daily recommendations", "", "", "", "", "daily"),
        ("play music from my liked songs", "", "", "", "", "liked"),
        ("play tunes from daily recommendations", "", "", "", "", "daily"),
        ("play me a song", "", "", "", "", "auto"),
        ("please play me a song", "", "", "", "", "auto"),
        ("play some music for me", "", "", "", "", "auto"),
        ("play Mr. Brightside", "Mr. Brightside", "", "", "", "auto"),
        ('play "Me, Myself and I"', "Me, Myself and I", "", "", "", "auto"),
        ("play Me and You", "Me and You", "", "", "", "auto"),
        ("play Waiting for Me", "Waiting for Me", "", "", "", "auto"),
        ("播放一下晴天", "晴天", "", "", "", "auto"),
        ("播放下晴天", "晴天", "", "", "", "auto"),
        ("Can you play Yellow?", "Yellow", "", "", "", "auto"),
        ("Could you play Yellow?", "Yellow", "", "", "", "auto"),
        ("Would you play Yellow?", "Yellow", "", "", "", "auto"),
        ("play my Night Loop playlist", "", "", "", "Night Loop", "auto"),
        (
            "play a song from my Night Loop playlist",
            "",
            "",
            "",
            "Night Loop",
            "auto",
        ),
        ("play a track from my Night Loop playlist", "", "", "", "Night Loop", "auto"),
        ("play a tune from my Night Loop playlist", "", "", "", "Night Loop", "auto"),
        ("play anything from my Night Loop playlist", "", "", "", "Night Loop", "auto"),
        (
            "Could you please play Yellow by Coldplay?",
            "Yellow Coldplay",
            "Yellow",
            "Coldplay",
            "",
            "auto",
        ),
    ),
)
def test_parse_explicit_user_music_request(
    text,
    keyword,
    song,
    artist,
    playlist,
    source,
) -> None:
    request = music_requests.parse_explicit_user_music_request(text)

    assert request is not None
    assert request.keyword == keyword
    assert request.song_name == song
    assert request.song_artist == artist
    assert request.playlist_name == playlist
    assert request.personalization_source == source


@pytest.mark.parametrize(
    "text",
    (
        "不要放歌",
        "放首歌，算了别放了",
        "刚才听了晴天",
        "你喜欢邓紫棋吗？",
        "我想换成红色",
        "Could you play a game?",
        "Can you listen to me?",
        "play Yellow. don't play music",
        "play Yellow but don't play music",
        "播放一下视频",
        "播放一下游戏",
    ),
)
def test_non_music_commands_do_not_trigger_immediate_playback(text) -> None:
    assert music_requests.parse_explicit_user_music_request(text) is None


@pytest.mark.parametrize(
    ("text", "keyword", "song"),
    (
        ("点首歌", "", ""),
        ("点首歌晴天", "晴天", "晴天"),
        ("来首B站的晴天", "晴天", "晴天"),
        ("放首哔哩哔哩周杰伦", "周杰伦", "周杰伦"),
        ("BV1xx411c7mD", "BV1xx411c7mD", "BV1xx411c7mD"),
        (
            "播一下 https://www.bilibili.com/video/BV1xx411c7mD",
            "BV1xx411c7mD",
            "BV1xx411c7mD",
        ),
        (
            "https://b23.tv/abcdefg",
            "https://b23.tv/abcdefg",
            "https://b23.tv/abcdefg",
        ),
    ),
)
def test_voice_music_phrases_include_diange_and_bilibili(text, keyword, song) -> None:
    request = music_requests.parse_explicit_user_music_request(text)
    assert request is not None
    assert request.keyword == keyword
    assert request.song_name == song


@pytest.mark.parametrize(
    "text",
    (
        "don't play games with me",
        "stop playing games",
        "pause playing video",
        "cancel video playback",
        "别播放视频",
    ),
)
def test_non_music_commands_do_not_cancel_pending_music(text) -> None:
    assert music_requests.is_explicit_music_cancellation(text) is False


@pytest.mark.parametrize(
    "text",
    (
        "don't play this music",
        "don't play that song",
        "stop that music",
        "pause playback",
        "play Yellow, pause playback",
    ),
)
def test_explicit_music_targets_override_non_music_pronouns(text) -> None:
    assert music_requests.is_explicit_music_cancellation(text) is True


def test_new_user_music_request_cancels_previous_search(monkeypatch) -> None:
    previous_task = MagicMock()
    previous_task.done.return_value = False
    next_task = MagicMock()
    pending_coroutines = []

    def fire_task(coro):
        pending_coroutines.append(coro)
        return next_task

    manager = SimpleNamespace(
        lanlan_name="YUI",
        user_language="zh",
        _music_request_task=previous_task,
        _fire_task=fire_task,
        enqueue_agent_callback=MagicMock(),
    )
    monkeypatch.setattr(
        music_playback,
        "_session_manager_getter",
        lambda _: manager,
    )

    try:
        music_playback._on_user_utterance(
            "YUI",
            {"lanlan": "YUI", "content": "播放《大喜》"},
        )
    finally:
        for coro in pending_coroutines:
            coro.close()

    previous_task.cancel.assert_called_once_with()
    assert manager._music_request_task is next_task
    assert manager._music_request_epoch == 1
    pending_context = manager.enqueue_agent_callback.call_args.args[0]
    assert pending_context["delivery_mode"] == "passive"
    assert pending_context["context_type"] == "music_request_pending"
    assert "不要询问版本" in pending_context["detail"]
    assert "不要声称已经开始播放" in pending_context["detail"]

    playback_source = Path(music_playback.__file__).read_text(encoding="utf-8")
    assert "不要询问版本" not in playback_source
    assert "do not ask which version" in (
        music_playback.get_music_request_pending_prompt("en-US")
    )
    traditional_prompt = music_playback.get_music_request_pending_prompt("zh-TW")
    assert "音樂模組已接管" in traditional_prompt
    assert "音乐模块已接管" not in traditional_prompt


@pytest.mark.asyncio
async def test_explicit_music_cancellation_invalidates_pending_search(
    monkeypatch,
) -> None:
    previous_task = MagicMock()
    previous_task.done.return_value = False
    pushed = []

    async def send_json(payload):
        pushed.append(payload)

    scheduled = []

    def fire_task(coro):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=4,
        _music_request_task=previous_task,
        _fire_task=fire_task,
        enqueue_agent_callback=MagicMock(),
        websocket=SimpleNamespace(client_state=None, send_json=send_json),
        sync_message_queue=MagicMock(),
    )
    monkeypatch.setattr(
        music_playback,
        "_session_manager_getter",
        lambda _: manager,
    )

    music_playback._on_user_utterance(
        "YUI",
        {"lanlan": "YUI", "content": "不要放歌"},
    )
    await asyncio.gather(*scheduled)

    previous_task.cancel.assert_called_once_with()
    assert manager._music_request_epoch == 5
    assert pushed == [{"type": "music_request_cancelled", "request_id": 5}]
    manager.enqueue_agent_callback.assert_not_called()


def test_source_exclusion_does_not_cancel_unrelated_pending_search(
    monkeypatch,
) -> None:
    previous_task = MagicMock()
    previous_task.done.return_value = False
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=4,
        _music_request_task=previous_task,
        _fire_task=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )
    monkeypatch.setattr(
        music_playback,
        "_session_manager_getter",
        lambda _: manager,
    )

    music_playback._on_user_utterance(
        "YUI",
        {"lanlan": "YUI", "content": "不要日推"},
    )

    previous_task.cancel.assert_not_called()
    assert manager._music_request_epoch == 4
    manager._fire_task.assert_not_called()


@pytest.mark.asyncio
async def test_music_invalidations_reach_all_registered_windows() -> None:
    current_payloads = []
    owner_payloads = []

    async def send_current(payload):
        current_payloads.append(payload)

    async def send_owner(payload):
        owner_payloads.append(payload)

    current = SimpleNamespace(send_json=send_current)
    owner = SimpleNamespace(send_json=send_owner)
    manager = SimpleNamespace(
        lanlan_name="YUI",
        websocket=current,
        _music_playback_websockets=(current, owner),
        sync_message_queue=MagicMock(),
    )
    started = {"type": "music_request_started", "request_id": 2}
    candidates = {"type": "music_play_candidates", "request_id": 2}

    assert await music_playback._push_music_payload(manager, started) is True
    assert current_payloads == [started]
    assert owner_payloads == [started]

    assert await music_playback._push_music_payload(manager, candidates) is True
    assert current_payloads == [started, candidates]
    assert owner_payloads == [started]
    assert manager.sync_message_queue.put.call_count == 2


@pytest.mark.asyncio
async def test_fast_music_search_waits_for_current_reply_before_player(
    monkeypatch,
) -> None:
    order = []

    async def fetch(*args, **kwargs):
        order.append("search")
        return {
            "success": True,
            "data": [{
                "name": "21",
                "artist": "Polo G",
                "url": "/api/music/play/netease/21",
            }],
        }

    async def wait_for_reply(manager, epoch, elapsed):
        order.append("reply_end")

    async def push(manager, payload):
        order.append(payload["type"])
        return True

    monkeypatch.setattr(music_playback, "fetch_music_request", fetch)
    mark_query = MagicMock()
    monkeypatch.setattr(music_playback, "mark_music_request_query", mark_query)
    monkeypatch.setattr(
        music_playback,
        "_wait_for_current_reply",
        wait_for_reply,
    )
    monkeypatch.setattr(music_playback, "_push_music_payload", push)
    websocket = object()
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=1,
        user_language="zh",
        websocket=websocket,
    )

    result = await music_playback._execute_music_request(
        manager,
        music_requests.MusicRequest(keyword="21", song_name="21"),
        1,
    )

    assert result == {"status": "queued", "candidates": 1}
    assert order == [
        "music_request_started",
        "search",
        "reply_end",
        "music_play_candidates",
    ]
    mark_query.assert_called_once()


@pytest.mark.asyncio
async def test_music_search_continues_across_websocket_reconnect(
    monkeypatch,
) -> None:
    origin_websocket = object()
    replacement_websocket = object()
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=1,
        user_language="zh",
        websocket=origin_websocket,
    )

    async def fetch(*args, **kwargs):
        manager.websocket = replacement_websocket
        return {
            "success": True,
            "data": [{
                "name": "Yellow",
                "artist": "Coldplay",
                "url": "/api/music/play/netease/yellow",
            }],
        }

    deliveries = []

    async def push(manager, payload):
        deliveries.append((payload["type"], manager.websocket))
        return True

    mark_query = MagicMock()
    monkeypatch.setattr(music_playback, "fetch_music_request", fetch)
    monkeypatch.setattr(music_playback, "_push_music_payload", push)
    monkeypatch.setattr(music_playback, "mark_music_request_query", mark_query)
    monkeypatch.setattr(
        music_playback,
        "_wait_for_current_reply",
        AsyncMock(),
    )

    result = await music_playback._execute_music_request(
        manager,
        music_requests.MusicRequest(keyword="Yellow", song_name="Yellow"),
        1,
    )

    assert result == {"status": "queued", "candidates": 1}
    assert deliveries == [
        ("music_request_started", origin_websocket),
        ("music_play_candidates", replacement_websocket),
    ]
    mark_query.assert_called_once()


@pytest.mark.asyncio
async def test_music_request_failure_carries_request_id(
    monkeypatch,
) -> None:
    websocket = object()
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=7,
        user_language="zh",
        websocket=websocket,
    )
    monkeypatch.setattr(
        music_playback,
        "fetch_music_request",
        AsyncMock(
            return_value={
                "success": False,
                "error_code": "track_not_found",
                "data": [],
            }
        ),
    )
    send_failure = AsyncMock()
    monkeypatch.setattr(
        music_playback,
        "_send_music_request_failure",
        send_failure,
    )

    result = await music_playback._execute_music_request(
        manager,
        music_requests.MusicRequest(keyword="missing"),
        7,
    )

    assert result["status"] == "failed"
    send_failure.assert_awaited_once_with(
        manager,
        "missing",
        "track_not_found",
        7,
    )


@pytest.mark.asyncio
async def test_music_dispatch_waits_until_current_reply_finishes(
    monkeypatch,
) -> None:
    websocket = object()
    manager = SimpleNamespace(
        _music_request_epoch=1,
        _active_text_request_id="turn-1",
        _voice_playback_active=False,
        session=None,
        websocket=websocket,
    )
    sleep_calls = []

    async def finish_reply_on_sleep(delay):
        sleep_calls.append(delay)
        manager._active_text_request_id = None

    monkeypatch.setattr(music_playback.asyncio, "sleep", finish_reply_on_sleep)

    await music_playback._wait_for_current_reply(manager, 1, 1.25)

    assert sleep_calls == [music_playback._REPLY_WAIT_POLL_SECONDS]


@pytest.mark.asyncio
async def test_direct_music_request_preserves_failure_reason() -> None:
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


@pytest.mark.asyncio
async def test_play_music_tool_queues_same_playback_payload(monkeypatch) -> None:
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.music_requests import MusicRequest

    queued = []

    def fake_queue(manager, request):
        queued.append((manager, request))
        return 9

    monkeypatch.setattr(
        "main_logic.music_playback.queue_user_music_request",
        fake_queue,
    )

    class Dummy(ToolCallingMixin):
        def __init__(self):
            self.lanlan_name = "YUI"
            self.user_language = "zh"

    helper = Dummy()
    message = await helper._handle_play_music_call(
        {"query": "晴天", "song": "晴天", "artist": "周杰伦"}
    )

    assert "request_id=9" in message
    assert len(queued) == 1
    assert queued[0][0] is helper
    assert isinstance(queued[0][1], MusicRequest)
    assert queued[0][1].keyword == "晴天 周杰伦"
    assert queued[0][1].song_name == "晴天"
    assert queued[0][1].song_artist == "周杰伦"

    queued.clear()
    await helper._handle_play_music_call({"bvid": "bv1xx411c7md"})
    assert queued[0][1].keyword == "BV1xx411c7md"


@pytest.mark.asyncio
async def test_control_music_tool_stop_and_unsupported_next(monkeypatch) -> None:
    from main_logic.core.tool_calling import ToolCallingMixin

    calls = []

    async def fake_control(manager, action):
        calls.append(action)
        if action == "stop":
            return {"status": "ok", "action": "stop", "request_id": 3}
        if action == "next":
            return {
                "status": "unsupported",
                "action": "next",
                "message": "next track is not supported",
            }
        return {"status": "ok", "action": action}

    monkeypatch.setattr(
        "main_logic.music_playback.control_user_music_playback",
        fake_control,
    )

    class Dummy(ToolCallingMixin):
        def __init__(self):
            self.lanlan_name = "YUI"
            self.user_language = "zh"

    helper = Dummy()
    stop_msg = await helper._handle_control_music_call({"action": "stop"})
    next_msg = await helper._handle_control_music_call({"action": "next"})

    assert calls == ["stop", "next"]
    assert "stop" in stop_msg
    assert "暂不支持" in next_msg or "下一首" in next_msg


def test_music_playback_keeps_core_entrypoints_thin() -> None:
    core_dir = Path(__file__).parents[2] / "main_logic" / "core"
    streaming_source = (core_dir / "streaming.py").read_text(encoding="utf-8")
    turn_source = (core_dir / "turn.py").read_text(encoding="utf-8")
    tool_source = (core_dir / "tool_calling.py").read_text(encoding="utf-8")

    assert "music_request" not in streaming_source
    assert "music_request" not in turn_source
    # Builtin play/control tools live in tool_calling but must reuse the
    # music_playback helper — never embed the fetch/push loop themselves.
    assert "_execute_music_request" not in tool_source
    assert "play_music" in tool_source
    assert "control_music" in tool_source
    assert "queue_user_music_request" in tool_source
    assert "control_user_music_playback" in tool_source


def test_confirmed_user_music_playback_uses_existing_callback_delivery() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=7,
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )
    event = {
        "state": "playing",
        "playback_id": "player:1",
        "playback_window_id": "window:1",
        "playback_started_at": 100,
        "request_id": 7,
        "source": "user",
        "track": {"name": "大喜", "artist": "泠鸢yousa"},
    }

    assert music_playback.handle_music_playback_state(manager, event) is True
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["delivery_mode"] == "proactive"
    assert callback["channel"] == "music_playback"
    assert "播放器已确认开始播放《大喜》（泠鸢yousa）" in callback["detail"]
    assert "不要再次调用音乐播放工具" in callback["detail"]
    manager.enqueue_agent_callback.assert_not_called()

    assert music_playback.handle_music_playback_state(manager, event) is False
    manager.submit_proactive_callback.assert_called_once()


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


def test_music_playback_keeps_current_owner_during_replacement_search() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=7,
        submit_proactive_callback=MagicMock(),
        enqueue_agent_callback=MagicMock(),
    )
    event = {
        "state": "playing",
        "playback_id": "player:current",
        "playback_window_id": "window:current",
        "playback_started_at": 100,
        "request_id": 7,
        "source": "user",
    }

    assert music_playback.handle_music_playback_state(manager, event) is True

    manager._music_request_epoch = 8
    event["state"] = "ended"
    assert music_playback.handle_music_playback_state(manager, event) is True
    manager.enqueue_agent_callback.assert_called_once()


def test_music_playback_rejects_stale_windows_and_request_epochs() -> None:
    manager = SimpleNamespace(
        lanlan_name="YUI",
        _music_request_epoch=8,
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
    stale_request_event = {
        "state": "error",
        "playback_id": "player:request",
        "playback_window_id": "window:new",
        "playback_started_at": 300,
        "request_id": 7,
        "source": "music_play_url",
    }

    assert music_playback.handle_music_playback_state(manager, current_event) is True
    assert music_playback.handle_music_playback_state(manager, stale_window_event) is False
    assert music_playback.handle_music_playback_state(manager, stale_request_event) is False
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
