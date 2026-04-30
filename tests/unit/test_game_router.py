from unittest.mock import AsyncMock

import pytest

from main_routers import game_router


@pytest.fixture(autouse=True)
def _reset_game_sessions():
    snapshot = dict(game_router._game_sessions)
    game_router._game_sessions.clear()
    try:
        yield
    finally:
        game_router._game_sessions.clear()
        game_router._game_sessions.update(snapshot)


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
