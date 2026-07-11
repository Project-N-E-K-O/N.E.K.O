"""验证小剧场只通过现有 Project TTS 朗读公开猫娘对白。"""  # noqa: DOCSTRING_CJK

import pytest

from main_routers import theater_router


class _FakeTheaterTtsManager:
    """记录 Project TTS 调用参数，不启动真实供应商或音频线程。"""  # noqa: DOCSTRING_CJK

    def __init__(self):
        self.calls = []

    async def mirror_assistant_speech(self, line, **kwargs):
        """保存本次朗读请求供隔离边界断言。"""  # noqa: DOCSTRING_CJK
        self.calls.append((line, kwargs))
        return {"ok": True, "audio_queued": True}


class _FakeSessionRegistry:
    """只向测试暴露指定猫娘的 Session Manager。"""  # noqa: DOCSTRING_CJK

    def __init__(self, manager):
        self.manager = manager

    def get(self, lanlan_name):
        """角色名匹配时返回测试 Manager。"""  # noqa: DOCSTRING_CJK
        return self.manager if lanlan_name == "测试猫娘" else None


def test_theater_character_name_always_comes_from_current_catgirl(monkeypatch):
    """开场请求即使伪造角色名，也只能使用配置中的当前猫娘。"""  # noqa: DOCSTRING_CJK
    class _FakeConfigManager:
        """提供当前猫娘配置，不读取真实用户角色文件。"""  # noqa: DOCSTRING_CJK

        @staticmethod
        def load_characters():
            """返回测试所需的最小角色配置。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": "测试猫娘", "猫娘": {"测试猫娘": {}}}

    monkeypatch.setattr(theater_router, "get_config_manager", lambda: _FakeConfigManager())

    assert theater_router._resolve_lanlan_name("伪造猫娘") == "测试猫娘"


@pytest.mark.asyncio
async def test_theater_tts_bridge_does_not_mirror_chat_or_game_route(monkeypatch):
    """桥接只朗读公开对白，并关闭普通聊天镜像与 turn-end。"""  # noqa: DOCSTRING_CJK
    async def _claim_dialogue(*args, **kwargs):
        """模拟 Runtime 已原子认领本轮公开对白。"""  # noqa: DOCSTRING_CJK
        return {
            "ok": True,
            "line": "我会陪你把这一幕演完喵。",
            "lanlan_name": "测试猫娘",
            "session_id": "theater_test",
            "state_revision": 3,
        }

    manager = _FakeTheaterTtsManager()
    monkeypatch.setattr(theater_router.runtime, "claim_dialogue_speech", _claim_dialogue)
    monkeypatch.setattr(theater_router, "get_session_manager", lambda: _FakeSessionRegistry(manager))
    monkeypatch.setattr(theater_router, "_theater_root", lambda: None)

    result = await theater_router._speak_committed_dialogue(
        {"ok": True, "session_id": "theater_test", "state_revision": 3}
    )
    assert result["audio_queued"] is True
    assert len(manager.calls) == 1
    line, options = manager.calls[0]
    assert line == "我会陪你把这一幕演完喵。"
    assert options["mirror_text"] is False
    assert options["emit_turn_end_after"] is False
    assert options["interrupt_audio"] is True
    assert options["metadata"]["source"] == "theater"
