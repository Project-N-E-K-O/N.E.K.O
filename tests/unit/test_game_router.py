import asyncio
from unittest.mock import AsyncMock

import pytest

from main_routers import game_router


@pytest.fixture(autouse=True)
def _reset_game_sessions():
    snapshot = dict(game_router._game_sessions)
    route_snapshot = dict(game_router._game_route_states)
    game_router._game_sessions.clear()
    game_router._game_route_states.clear()
    try:
        yield
    finally:
        game_router._game_sessions.clear()
        game_router._game_sessions.update(snapshot)
        game_router._game_route_states.clear()
        game_router._game_route_states.update(route_snapshot)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.unit
def test_parse_control_instructions_extracts_json_line():
    result = game_router._parse_control_instructions(
        '这球我拿下了喵\n{"mood":"happy","difficulty":"lv2"}'
    )

    assert result == {
        "line": "这球我拿下了喵",
        "control": {"mood": "happy", "difficulty": "lv2"},
    }


@pytest.mark.unit
def test_game_archive_memory_payload_uses_system_note_shape():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。最终/最近比分：主人 1 : 4 Lan。",
        "memory_highlights": {
            "important_records": ["主人要求温柔一点，你改成让球式回应。"],
            "important_game_events": ["猫娘大比分领先后开始放水。"],
        },
        "last_full_dialogues": [
            {"type": "user", "text": "温柔一点"},
            {"type": "assistant", "line": "好好好，让你踢。"},
        ],
        "key_events": [],
        "last_state": {"score": {"player": 1, "ai": 4}},
    }

    messages = game_router._build_game_archive_memory_messages(archive)

    assert [msg["role"] for msg in messages] == ["system"]
    assert "游戏模块归档，不是主人逐字发言" in messages[0]["content"][0]["text"]
    assert "soccer 小游戏结束" in messages[0]["content"][0]["text"]
    assert "重要互动：" in messages[0]["content"][0]["text"]
    assert "主人要求温柔一点，你改成让球式回应。" in messages[0]["content"][0]["text"]
    assert "猫娘记住的比赛事件：" in messages[0]["content"][0]["text"]
    assert "本局记录了" not in messages[0]["content"][0]["text"]
    assert "外部接管模式" not in messages[0]["content"][0]["text"]
    assert "主人最近在比赛里说：温柔一点" in messages[0]["content"][0]["text"]
    assert "你最后回应：好好好，让你踢。" in messages[0]["content"][0]["text"]


@pytest.mark.unit
def test_game_archive_summary_keeps_score_not_counters():
    summary = game_router._summarize_game_archive(
        {"game_type": "soccer", "lanlan_name": "Lan", "last_state": {"score": {"player": 0, "ai": 5}}},
        [
            {"type": "game_event"},
            {"type": "user"},
            {"type": "assistant"},
        ],
    )

    assert summary == "soccer 小游戏结束。最终/最近比分：主人 0 : 5 Lan。"
    assert "本局记录了" not in summary
    assert "外部接管模式" not in summary


@pytest.mark.unit
def test_memory_review_prompt_protects_soccer_archive_records():
    from config.prompts_memory import get_history_review_prompt

    prompt = get_history_review_prompt("zh")

    assert "足球小游戏赛后记录" in prompt
    assert "不同时间/会话的足球比分默认代表不同局比赛" in prompt
    assert "不要整条删除" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_highlight_selector_uses_full_dialogue_log(monkeypatch):
    calls = []

    class _FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            calls.append(messages)
            return type("Resp", (), {
                "content": '{"important_records":["保留了第一句互动"],"important_game_events":["记住了关键抢断"]}'
            })()

    def fake_create_chat_llm(*_args, **_kwargs):
        return _FakeLLM()

    monkeypatch.setattr(
        game_router,
        "_get_current_character_info",
        lambda: {
            "model": "test-model",
            "base_url": "http://llm.test",
            "api_key": "key",
            "api_type": "test",
        },
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 0, "ai": 5}},
        "full_dialogues": [
            {"type": "user", "text": "第一句也要参与筛选"},
            {"type": "assistant", "line": "我记着呢。"},
            {"type": "user", "text": "最后一句"},
        ],
        "last_full_dialogues": [
            {"type": "user", "text": "最后一句"},
        ],
        "key_events": [],
    }

    highlights = await game_router._select_game_archive_memory_highlights(archive)

    assert highlights["important_records"] == ["保留了第一句互动"]
    assert highlights["important_game_events"] == ["记住了关键抢断"]
    assert "第一句也要参与筛选" in calls[0][1].content


@pytest.mark.unit
def test_route_liveness_prefers_recent_activity_over_stale_heartbeat():
    state = {
        "created_at": 100.0,
        "last_heartbeat_at": 110.0,
        "last_activity": 125.0,
    }

    assert game_router._route_liveness_at(state) == 125.0


@pytest.mark.unit
def test_route_heartbeat_timeout_uses_hidden_grace_window():
    assert game_router._route_heartbeat_timeout_seconds({"page_visible": True}) == (
        game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert game_router._route_heartbeat_timeout_seconds({"page_visible": False}) == (
        game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert game_router._route_heartbeat_timeout_seconds({"visibility_state": "hidden"}) == (
        game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_and_remove_session_closes_client():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    game_router._game_sessions["soccer:test_sid"] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": 0,
        "lock": None,
    }

    closed = await game_router._close_and_remove_session("soccer", "test_sid")

    assert closed is True
    fake_session.close.assert_awaited_once()
    assert "soccer:test_sid" not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_returns_closed_flag_for_missing_session():
    result = await game_router.game_end("soccer", _FakeRequest({"session_id": "missing"}))

    assert result == {
        "ok": True,
        "closed": False,
        "session_id": "missing",
        "route_closed": False,
        "archive": None,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_closes_existing_session():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    game_router._game_sessions["soccer:match_1"] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": 0,
        "lock": None,
    }

    result = await game_router.game_end("soccer", _FakeRequest({"session_id": "match_1"}))

    assert result == {
        "ok": True,
        "closed": True,
        "session_id": "match_1",
        "route_closed": False,
        "archive": None,
    }
    fake_session.close.assert_awaited_once()


class _FakeRealtimeSession:
    def __init__(self, *, model_lower="qwen-realtime", delivered=True):
        self._model_lower = model_lower
        self.model = model_lower
        self.base_url = "https://generativelanguage.googleapis.com" if "gemini" in model_lower else "https://dashscope.aliyuncs.com"
        self._api_type = "openai"
        self._is_gemini = "gemini" in model_lower
        self._is_responding = False
        self._audio_delta_total = 0
        self._input_audio_committed_total = 0
        self._response_created_total = 0
        self._response_done_total = 0
        self._last_response_transcript = ""
        self._active_instructions = "base realtime instructions"
        self.delivered = delivered
        self.prime_context_calls = []
        self.update_session_calls = []
        self.prompt_calls = []
        self.create_response_calls = []

    async def prime_context(self, text, skipped=False):
        self.prime_context_calls.append((text, skipped))

    async def update_session(self, config):
        self.update_session_calls.append(config)
        if "instructions" in config:
            self._active_instructions = config["instructions"]

    async def prompt_ephemeral(self, *args, language="zh", qwen_manual_commit=False):
        call = {
            "language": language,
            "qwen_manual_commit": qwen_manual_commit,
        }
        if args:
            call["instruction"] = args[0]
        self.prompt_calls.append(call)
        if self.delivered:
            self._input_audio_committed_total += 1
            self._response_created_total += 1
            self._response_done_total += 1
        return self.delivered

    async def create_response(self, text):
        self.create_response_calls.append(text)


class _FakeRealtimeManager:
    def __init__(self, session):
        self.session = session
        self.is_active = True
        self.user_language = "zh-CN"
        self.current_speech_id = "previous-speech"
        self.lock = None
        self.use_tts = False
        self._speech_output_total = 0
        self.voice_nudge_calls = 0
        self.voice_nudge_kwargs = []

    async def trigger_voice_proactive_nudge(self, **kwargs):
        self.voice_nudge_calls += 1
        self.voice_nudge_kwargs.append(kwargs)
        return True


@pytest.fixture
def _fake_realtime(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    monkeypatch.setattr(realtime_mod, "OmniRealtimeClient", _FakeRealtimeSession)
    monkeypatch.setattr(
        game_router,
        "_get_current_character_info",
        lambda: {"lanlan_name": "Lan"},
    )
    monkeypatch.setattr(game_router, "uuid4", lambda: "new-speech")

    async def _audio_sent(*_args, **_kwargs):
        return True

    monkeypatch.setattr(game_router, "_wait_for_speech_output", _audio_sent)

    return _FakeRealtimeSession


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_speak_qwen_uses_audio_nudge(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    session._last_response_transcript = "这球我拿下了喵"
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_speak(
        "soccer",
        _FakeRequest({"line": "这球我拿下了喵", "language": "zh-CN"}),
    )

    assert result["ok"] is True
    assert result["method"] == "qwen_audio_nudge"
    assert result["language"] == "zh"
    assert result["line_match"] is True
    assert result["speech_id"] == "new-speech"
    assert mgr.current_speech_id == "new-speech"
    assert session.prompt_calls == [{"language": "zh", "qwen_manual_commit": True}]
    assert session.prime_context_calls == []
    assert len(session.update_session_calls) == 2
    assert "这球我拿下了喵" in session.update_session_calls[0]["instructions"]
    assert session.update_session_calls[-1]["instructions"] == "base realtime instructions"
    assert session._active_instructions == "base realtime instructions"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_skips_gemini_prime_to_avoid_hidden_response(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_context(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "source": "game_event",
            "currentState": {"score": {"player": 1, "ai": 2}},
            "pendingItems": [{"type": "game_event", "kind": "goal-scored"}],
        }),
    )

    assert result["ok"] is True
    assert result["action"] == "skip"
    assert result["reason"] == "gemini_no_session_update"
    assert session.prime_context_calls == []
    assert session.create_response_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_speak_qwen_restores_speech_id_when_nudge_skipped(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=False)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_speak(
        "soccer",
        _FakeRequest({"line": "这球我拿下了喵"}),
    )

    assert result["ok"] is False
    assert result["reason"] == "audio_nudge_skipped"
    assert result["method"] == "qwen_audio_nudge"
    assert mgr.current_speech_id == "previous-speech"
    assert len(session.update_session_calls) == 2
    assert session.update_session_calls[-1]["instructions"] == "base realtime instructions"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_speak_qwen_reports_transcript_mismatch(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    session._last_response_transcript = "别光嗯啊的，快出招"
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_speak(
        "soccer",
        _FakeRequest({"line": "这球我拿下了喵"}),
    )

    assert result["ok"] is False
    assert result["reason"] == "spoken_transcript_mismatch"
    assert result["audio_sent"] is True
    assert result["line_match"] is False
    assert "别光嗯啊" in result["spoken_transcript"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_speak_non_qwen_uses_text_response(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gpt-realtime", delivered=True)
    session.base_url = "https://api.openai.com"
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_speak(
        "soccer",
        _FakeRequest({"line": "换我进攻了"}),
    )

    assert result["ok"] is True
    assert result["method"] == "text_response"
    assert result["speech_id"] == "new-speech"
    assert len(session.create_response_calls) == 1
    assert "换我进攻了" in session.create_response_calls[0]
    assert result["voice_source"]["provider"] == "openai"


class _FakeGameRouteManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.input_mode = "audio"
        self.mirrored = []
        self.assistant_mirrored = []
        self.spoken = []
        self.statuses = []
        self.user_activity_count = 0

    async def mirror_game_user_text(self, text, **kwargs):
        self.mirrored.append((text, kwargs))

    async def mirror_game_assistant_text(self, text, **kwargs):
        self.assistant_mirrored.append((text, kwargs))
        return {"ok": True, "mirrored": True, "method": "project_text_mirror"}

    async def send_user_activity(self):
        self.user_activity_count += 1

    async def speak_game_line(self, line, **kwargs):
        self.spoken.append((line, kwargs))
        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": "game-speech",
            "audio_sent": True,
            "voice_source": {"provider": "project_tts"},
        }

    async def send_status(self, message):
        self.statuses.append(message)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_activates_stt_gate_when_audio_already_active(monkeypatch, _fake_realtime):
    mgr = _FakeGameRouteManager()
    mgr.is_active = True
    mgr.session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "match_1"}),
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["before_game_external_mode"] == "audio"
    assert state["before_game_external_active"] is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_text_to_game_llm_defers_voice_to_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {
        "round": 3,
        "mood": "happy",
        "difficulty": "lv2",
        "score": {"player": 1, "ai": 4},
    }

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "user-text"
        assert event["userText"] == "你是不是在放水？"
        assert event["scoreDiff"] == 3
        return {
            "line": "才没有放水呢。",
            "control": {"mood": "happy"},
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    handled = await game_router.route_external_stream_message(
        "Lan",
        {"input_type": "text", "data": "你是不是在放水？", "request_id": "req-1"},
    )

    assert handled is True
    assert state["game_external_text_route_active"] is True
    assert state["game_input_mode"] == "text"
    assert state["activation_source"] == "external_text_hijacked_by_game"
    assert mgr.mirrored == [("你是不是在放水？", {
        "request_id": "req-1",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "external_text_route",
        "input_type": "game_text",
        "send_to_frontend": False,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "你是不是在放水？"
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False
    assert state["pending_outputs"][1]["result"]["line"] == "才没有放水呢。"
    assert [item["type"] for item in state["game_dialog_log"]] == ["user", "assistant"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_audio_activates_game_stt_gate(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    handled = await game_router.route_external_stream_message("Lan", {"input_type": "audio", "data": [0, 1]})
    handled_again = await game_router.route_external_stream_message("Lan", {"input_type": "audio", "data": [2, 3]})

    assert handled is True
    assert handled_again is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert state["activation_source"] == "external_voice_hijacked_by_game"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]
    assert len(mgr.statuses) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_to_game_llm(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "user-voice"
        assert event["userVoiceText"] == "我马上要进球了"
        return {
            "line": "那我可要认真防你啦。",
            "control": {"difficulty": "max"},
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    handled = await game_router.route_external_voice_transcript(
        "Lan",
        "我马上要进球了",
        request_id="voice-1",
        game_type="soccer",
        session_id="match_1",
    )

    assert handled is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert mgr.mirrored == [("我马上要进球了", {
        "request_id": "voice-1",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "external_voice_route",
        "input_type": "game_voice_transcript",
        "send_to_frontend": True,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "我马上要进球了"
    assert state["pending_outputs"][1]["meta"]["kind"] == "user-voice"
    assert state["pending_outputs"][1]["meta"]["hasUserSpeech"] is True
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_refreshes_last_state(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    before = state["last_heartbeat_at"]

    result = await game_router.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "currentState": {"score": {"player": 3, "ai": 2}},
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert state["last_heartbeat_at"] >= before
    assert state["last_state"] == {"score": {"player": 3, "ai": 2}}
    assert result["heartbeat_timeout_seconds"] == game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is True
    assert state["visibility_state"] == "visible"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_records_hidden_visibility(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    result = await game_router.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "pageVisible": False,
            "visibilityState": "hidden",
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert result["heartbeat_timeout_seconds"] == game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is False
    assert state["visibility_state"] == "hidden"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_finalize_archives_and_closes_session(monkeypatch):
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    game_router._game_sessions["soccer:match_1"] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": 0,
        "lock": None,
    }
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=True,
    )

    assert state["game_route_active"] is False
    assert state["heartbeat_enabled"] is False
    assert state["exit_reason"] == "heartbeat_timeout"
    assert result["game_session_closed"] is True
    assert result["archive"]["exit_reason"] == "heartbeat_timeout"
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert submitted[0]["exit_reason"] == "heartbeat_timeout"
    fake_session.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_uses_manager_project_tts(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_speak(
        "soccer",
        _FakeRequest({"line": "换我进攻了", "session_id": "match_1", "request_id": "req-2"}),
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts"
    assert result["voice_source"]["provider"] == "project_tts"
    assert mgr.spoken == [("换我进攻了", {
        "request_id": "req-2",
        "game_type": "soccer",
        "session_id": "match_1",
        "mirror_text": True,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_can_skip_text_mirror_for_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_speak(
        "soccer",
        _FakeRequest({
            "line": "只播放语音",
            "session_id": "match_1",
            "request_id": "req-voice",
            "mirror_text": False,
        }),
    )

    assert result["ok"] is True
    assert mgr.spoken == [("只播放语音", {
        "request_id": "req-voice",
        "game_type": "soccer",
        "session_id": "match_1",
        "mirror_text": False,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_uses_text_only_mirror(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "文字先进入主聊天窗",
            "session_id": "match_1",
            "request_id": "req-mirror",
            "turn_id": "turn-mirror",
            "source": "game-llm-result",
        }),
    )

    assert result["ok"] is True
    assert result["method"] == "project_text_mirror"
    assert mgr.assistant_mirrored == [("文字先进入主聊天窗", {
        "request_id": "req-mirror",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "game-llm-result",
        "turn_id": "turn-mirror",
        "event": {},
    })]
    assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_archives_active_route_to_memory(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {
        "score": {"player": 2, "ai": 5},
    }
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "你是不是在放水？",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "才没有放水呢。",
        "control": {"mood": "happy"},
    })

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan"}),
    )

    assert result["route_closed"] is True
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["archive"]["summary"].startswith("soccer 小游戏结束")
    assert "待接入 memory_server" not in result["archive"]["summary"]
    assert submitted[0]["last_full_dialogues"][-1]["line"] == "才没有放水呢。"
    assert state["game_route_active"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_injects_postgame_context_into_active_realtime(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.0,))
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {"score": {"player": 1, "ai": 3}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "我是不是不适合玩这个？",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "别认输嘛，再来一脚。",
        "control": {"mood": "relaxed"},
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["context_injected"] is True
    assert result["postgame"]["nudge_scheduled"] is True
    await asyncio.sleep(0.01)
    assert mgr.voice_nudge_calls == 1
    assert mgr.voice_nudge_kwargs[0]["qwen_manual_commit"] is True
    assert "足球小游戏赛后主动搭话" in mgr.voice_nudge_kwargs[0]["instruction"]
    assert "不要继续扮演比赛仍在进行" in mgr.voice_nudge_kwargs[0]["instruction"]
    assert session.prime_context_calls
    context_text, skipped = session.prime_context_calls[0]
    assert skipped is True
    assert "足球小游戏赛后上下文" in context_text
    assert "我是不是不适合玩这个？" in context_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_uses_direct_response_for_gemini_postgame(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {"score": {"player": 3, "ai": 14}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "哇,你是笨蛋。",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "十二比三，帅的是我。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["action"] == "direct_response"
    assert result["postgame"]["reason"] == "gemini_direct_response"
    assert session.prime_context_calls == []
    assert session.prompt_calls == []
    assert mgr.voice_nudge_calls == 0
    assert len(session.create_response_calls) == 1
    assert "足球小游戏赛后上下文" in session.create_response_calls[0]
    assert "足球小游戏赛后主动搭话" in session.create_response_calls[0]
    assert "不要继续扮演比赛仍在进行" in session.create_response_calls[0]


class _FakePostgameState:
    def __init__(self):
        self.events = []

    async def fire(self, event, **kwargs):
        self.events.append((event, kwargs))


class _FakePostgameTextManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.current_speech_id = "postgame-sid"
        self.state = _FakePostgameState()
        self.prepare_calls = []
        self.feed_tts_calls = []
        self.finish_calls = []

    async def prepare_proactive_delivery(self, **kwargs):
        self.prepare_calls.append(kwargs)
        return True

    async def finish_proactive_delivery(self, text, **kwargs):
        self.finish_calls.append((text, kwargs))
        return True

    async def feed_tts_chunk(self, text, **kwargs):
        self.feed_tts_calls.append((text, kwargs))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_delivers_one_shot_postgame_text_bubble(monkeypatch):
    mgr = _FakePostgameTextManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {"score": {"player": 2, "ai": 4}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "我好像踢不进去。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "postgame"
        assert event["lastUserText"] == "我好像踢不进去。"
        assert event["scoreText"] == "主人 2 : 4 Lan"
        return {
            "line": "刚才那局不算，我下次慢点陪你踢。",
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "text"
    assert result["postgame"]["action"] == "chat"
    assert result["postgame"]["line"] == "刚才那局不算，我下次慢点陪你踢。"
    assert result["postgame"]["tts_fed"] is True
    assert mgr.prepare_calls == [{"min_idle_secs": 0.0}]
    assert mgr.feed_tts_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert mgr.finish_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert any(getattr(event, "name", "") == "PROACTIVE_PHASE2" for event, _ in mgr.state.events)
    assert any(getattr(event, "name", "") == "PROACTIVE_DONE" for event, _ in mgr.state.events)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_on_heartbeat_timeout(monkeypatch):
    mgr = _FakePostgameTextManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("postgame should not run during heartbeat timeout")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "heartbeat_timeout"}),
    )

    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert mgr.prepare_calls == []
    assert state["exit_reason"] == "heartbeat_timeout"
