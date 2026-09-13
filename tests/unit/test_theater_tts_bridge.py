"""验证 Numeric v2 逐块朗读复用 Project TTS。"""  # noqa: DOCSTRING_CJK

import pytest
from starlette.websockets import WebSocketState

from services.theater import tts_bridge


class _FakeManager:
    def __init__(self):
        self.websocket = type("WebSocket", (), {"client_state": WebSocketState.CONNECTED})()
        self.calls = []

    async def mirror_assistant_speech(self, line, **kwargs):
        self.calls.append((line, kwargs))
        return {"ok": True, "audio_queued": True, "speech_id": "speech-test"}


class _FakeRegistry:
    def __init__(self, manager):
        self.manager = manager

    def get(self, lanlan_name):
        return self.manager if lanlan_name == "测试猫娘" else None


@pytest.mark.asyncio
async def test_shared_tts_bridge_speaks_one_committed_numeric_dialogue():
    manager = _FakeManager()
    result = await tts_bridge.speak_committed_line(
        "谢谢你陪着我。",
        session_id="numeric_test",
        state_revision=1,
        lanlan_name="测试猫娘",
        resolve_current_catgirl=lambda: "测试猫娘",
        get_session_manager=lambda: _FakeRegistry(manager),
        metadata_kind="theater_numeric_dialogue_block",
        request_id="numeric_tts_test",
        interrupt_audio=False,
    )

    assert result["audio_queued"] is True
    assert manager.calls[0][0] == "谢谢你陪着我。"
    assert manager.calls[0][1]["metadata"]["kind"] == "theater_numeric_dialogue_block"
    assert manager.calls[0][1]["interrupt_audio"] is False


@pytest.mark.asyncio
async def test_shared_tts_bridge_skips_when_catgirl_resolution_fails():
    manager = _FakeManager()

    def fail_resolve():
        raise RuntimeError("profile_unavailable")

    result = await tts_bridge.speak_committed_line(
        "这句正文仍应返回给前端。",
        session_id="numeric_tts_resolver_failure",
        state_revision=1,
        lanlan_name="测试猫娘",
        resolve_current_catgirl=fail_resolve,
        get_session_manager=lambda: _FakeRegistry(manager),
        metadata_kind="theater_numeric_dialogue_block",
        request_id="numeric_tts_resolver_failure_request",
    )

    assert result == {"ok": True, "skipped": "project_tts_unavailable"}
    assert manager.calls == []
