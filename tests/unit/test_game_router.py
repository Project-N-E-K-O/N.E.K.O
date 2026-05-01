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
        self.base_url = "https://dashscope.aliyuncs.com"
        self._api_type = "openai"
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

    async def prompt_ephemeral(self, *, language="zh", qwen_manual_commit=False):
        self.prompt_calls.append({
            "language": language,
            "qwen_manual_commit": qwen_manual_commit,
        })
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
