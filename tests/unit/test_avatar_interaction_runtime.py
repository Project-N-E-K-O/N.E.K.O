import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from main_logic.core import (
    LLMSessionManager,
    _build_avatar_interaction_instruction,
    _normalize_avatar_interaction_payload,
)
from main_logic.omni_offline_client import OmniOfflineClient


class _DummyQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FakeChunk:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = None
        self.response_metadata = None


class _FakeLLM:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
def mock_manager_and_session():
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Neko"
    manager.master_name = "Master"
    manager.user_language = "zh-CN"
    manager.is_active = True
    manager.websocket = None
    manager.sync_message_queue = _DummyQueue()
    manager.lock = asyncio.Lock()
    manager._proactive_write_lock = asyncio.Lock()
    manager.current_speech_id = None
    manager._tts_done_queued_for_turn = False
    manager._recent_avatar_interaction_ids = deque(maxlen=32)
    manager._recent_avatar_interaction_id_set = set()
    manager._last_avatar_interaction_at = 0
    manager._last_avatar_interaction_speak_at = 0
    manager.avatar_interaction_cooldown_ms = 0
    manager.avatar_interaction_speak_cooldown_ms = 0
    manager._get_text_guard_max_length = lambda: 350

    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session._is_responding = False
    session.update_max_response_length = lambda _max_length: None

    return manager, session


@pytest.mark.unit
def test_normalize_avatar_interaction_payload_parses_boolean_variants():
    fist_false = _normalize_avatar_interaction_payload({
        "interaction_id": "int-fist",
        "tool_id": "fist",
        "action_id": "poke",
        "target": "avatar",
        "rewardDrop": "false",
    })
    hammer_false = _normalize_avatar_interaction_payload({
        "interaction_id": "int-hammer-false",
        "tool_id": "hammer",
        "action_id": "bonk",
        "target": "avatar",
        "intensity": "normal",
        "easterEgg": "0",
    })
    hammer_true = _normalize_avatar_interaction_payload({
        "interaction_id": "int-hammer-true",
        "tool_id": "hammer",
        "action_id": "bonk",
        "target": "avatar",
        "intensity": "normal",
        "easter_egg": 1,
    })

    assert fist_false is not None
    assert fist_false["reward_drop"] is False

    assert hammer_false is not None
    assert hammer_false["easter_egg"] is False
    assert hammer_false["intensity"] == "normal"

    assert hammer_true is not None
    assert hammer_true["easter_egg"] is True
    assert hammer_true["intensity"] == "easter_egg"


@pytest.mark.unit
def test_normalize_avatar_interaction_payload_falls_back_to_camelcase_text_context_when_snake_is_none():
    payload = _normalize_avatar_interaction_payload({
        "interaction_id": "int-text-context",
        "tool_id": "lollipop",
        "action_id": "offer",
        "target": "avatar",
        "text_context": None,
        "textContext": "  hello neko  ",
    })

    assert payload is not None
    assert payload["text_context"] == '"hello neko"'


@pytest.mark.unit
def test_normalize_avatar_interaction_payload_falls_back_when_timestamp_overflows():
    payload = _normalize_avatar_interaction_payload({
        "interaction_id": "int-overflow-ts",
        "tool_id": "hammer",
        "action_id": "bonk",
        "target": "avatar",
        "timestamp": "inf",
    })

    assert payload is not None
    assert isinstance(payload["timestamp"], int)
    assert payload["timestamp"] > 0


@pytest.mark.unit
def test_build_avatar_interaction_instruction_uses_locale_specific_fallback():
    manager = SimpleNamespace(
        user_language="en-US",
        lanlan_name="Neko",
        master_name="Master",
    )

    instruction = _build_avatar_interaction_instruction(manager, {
        "tool_id": "hammer",
        "action_id": "unknown",
        "intensity": "normal",
        "reward_drop": False,
        "easter_egg": False,
        "touch_zone": "",
        "text_context": "",
    })

    assert "Event fact: Keep the reaction immediate and in character." in instruction
    assert "Expression tendency: Short, natural, and grounded in the moment." in instruction


@pytest.mark.unit
def test_build_avatar_interaction_instruction_omits_touch_zone_for_lollipop():
    manager = SimpleNamespace(
        user_language="zh-CN",
        lanlan_name="Neko",
        master_name="Master",
    )

    lollipop_instruction = _build_avatar_interaction_instruction(manager, {
        "tool_id": "lollipop",
        "action_id": "tease",
        "intensity": "normal",
        "reward_drop": False,
        "easter_egg": False,
        "touch_zone": "head",
        "text_context": "",
    })
    fist_instruction = _build_avatar_interaction_instruction(manager, {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
        "reward_drop": False,
        "easter_egg": False,
        "touch_zone": "head",
        "text_context": "",
    })

    assert "接触位置" not in lollipop_instruction
    assert "接触位置：头顶" in fist_instruction


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_avatar_interaction_ack_reports_error_when_prompt_ephemeral_raises(mock_manager_and_session):
    manager, session = mock_manager_and_session
    event_log = []

    async def prompt_ephemeral(
        _instruction,
        *,
        completion_mode: str = "proactive",
        persist_response: bool = True,
    ):
        event_log.append(("prompt", completion_mode, persist_response, manager.current_speech_id))
        raise RuntimeError("boom")

    session.prompt_ephemeral = prompt_ephemeral
    manager.session = session

    async def send_avatar_interaction_ack(interaction_id: str, accepted: bool, reason: str = "", turn_id: str = ""):
        event_log.append(("ack", interaction_id, accepted, reason, turn_id))

    manager.send_avatar_interaction_ack = send_avatar_interaction_ack

    result = await manager.handle_avatar_interaction({
        "interaction_id": "int-err",
        "tool_id": "hammer",
        "action_id": "bonk",
        "target": "avatar",
        "intensity": "normal",
    })

    assert event_log[0][0:3] == ("prompt", "response", False)
    assert event_log[1] == ("ack", "int-err", False, "error", "")
    assert result == {"accepted": False, "reason": "error", "interaction_id": "int-err"}
    assert manager._last_avatar_interaction_speak_at == 0


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("chunk_content", "expected_committed"),
    [
        ("hello there", True),
        ("", False),
        ("[play_music:some_song]", False),
    ],
)
async def test_prompt_ephemeral_forwards_content_committed_to_proactive_callback(chunk_content, expected_committed):
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client.llm = _FakeLLM([_FakeChunk(chunk_content)])
    client._conversation_history = []
    client._is_responding = False
    client._prefix_buffer_size = 0
    client.master_name = ""
    client.lanlan_name = ""
    client.on_text_delta = None
    client.on_status_message = None
    client.on_response_done = None
    callback_events = []

    async def proactive_done(content_committed: bool):
        callback_events.append(content_committed)

    client.on_proactive_done = proactive_done

    delivered = await client.prompt_ephemeral("ephemeral instruction", completion_mode="proactive")

    assert delivered is expected_committed
    assert callback_events == [expected_committed]
    assert len(client._conversation_history) == (1 if expected_committed else 0)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("delivered", "expected_reason"),
    [
        (False, "empty_response"),
        (True, "delivered"),
    ],
)
async def test_handle_avatar_interaction_ack_follows_prompt_result(
    delivered,
    expected_reason,
    mock_manager_and_session,
):
    manager, session = mock_manager_and_session
    event_log = []

    async def prompt_ephemeral(
        _instruction,
        *,
        completion_mode: str = "proactive",
        persist_response: bool = True,
    ):
        event_log.append(("prompt", completion_mode, persist_response, manager.current_speech_id))
        return delivered

    session.prompt_ephemeral = prompt_ephemeral
    manager.session = session

    async def send_avatar_interaction_ack(interaction_id: str, accepted: bool, reason: str = "", turn_id: str = ""):
        event_log.append(("ack", interaction_id, accepted, reason, turn_id))

    manager.send_avatar_interaction_ack = send_avatar_interaction_ack

    result = await manager.handle_avatar_interaction({
        "interaction_id": "int-001",
        "tool_id": "hammer",
        "action_id": "bonk",
        "target": "avatar",
        "intensity": "normal",
    })

    assert event_log[0][0:3] == ("prompt", "response", False)
    assert event_log[1] == (
        "ack",
        "int-001",
        delivered,
        expected_reason,
        event_log[0][3] if delivered else "",
    )
    if delivered:
        assert result == {"accepted": True, "interaction_id": "int-001"}
        assert manager._last_avatar_interaction_speak_at > 0
    else:
        assert result == {"accepted": False, "reason": "empty_response", "interaction_id": "int-001"}
        assert manager._last_avatar_interaction_speak_at == 0
