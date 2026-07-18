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
        claim = {
            "ok": True,
            "line": "我会陪你把这一幕演完喵。",
            "lanlan_name": "测试猫娘",
            "session_id": "theater_test",
            "state_revision": 3,
        }
        # 真实 Runtime 会在角色锁内调用播放器；测试同样走这条回调链而非绕过它。
        return await kwargs["play"](claim)

    manager = _FakeTheaterTtsManager()
    monkeypatch.setattr(theater_router.runtime, "claim_dialogue_speech", _claim_dialogue)
    monkeypatch.setattr(theater_router, "get_session_manager", lambda: _FakeSessionRegistry(manager))
    monkeypatch.setattr(theater_router, "_theater_root", lambda: None)
    monkeypatch.setattr(theater_router, "_resolve_lanlan_name", lambda _raw=None: "测试猫娘")

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


@pytest.mark.asyncio
async def test_theater_tts_rechecks_current_catgirl_before_queue(monkeypatch):
    """认领后若当前猫娘已经切换，旧角色对白不得进入 TTS 管线。"""  # noqa: DOCSTRING_CJK
    async def _claim_dialogue(*args, **kwargs):
        """模拟切换前已经完成 Runtime 认领、随后进入播放回调。"""  # noqa: DOCSTRING_CJK
        claim = {
            "ok": True,
            "line": "这句旧角色对白不应播放喵。",
            "lanlan_name": "旧猫娘",
            "session_id": "theater_old_character",
            "state_revision": 4,
        }
        return await kwargs["play"](claim)

    manager = _FakeTheaterTtsManager()
    monkeypatch.setattr(theater_router.runtime, "claim_dialogue_speech", _claim_dialogue)
    monkeypatch.setattr(theater_router, "get_session_manager", lambda: _FakeSessionRegistry(manager))
    monkeypatch.setattr(theater_router, "_theater_root", lambda: None)
    monkeypatch.setattr(theater_router, "_resolve_lanlan_name", lambda _raw=None: "新猫娘")

    result = await theater_router._speak_committed_dialogue(
        {"ok": True, "session_id": "theater_old_character", "state_revision": 4}
    )

    assert result == {"ok": True, "skipped": "character_changed"}
    assert manager.calls == []


@pytest.mark.asyncio
async def test_start_router_passes_current_config_without_scanning_sessions(monkeypatch):
    """开场只转交当前配置做锁内重验，不得再扫描其他小剧场 Session。"""  # noqa: DOCSTRING_CJK
    captured = {"cleanup_calls": 0}

    class _FakeConfigManager:
        """提供开场所需的当前猫娘和数据目录。"""  # noqa: DOCSTRING_CJK

        app_docs_dir = None
        config_dir = None

        @staticmethod
        def load_characters():
            """返回测试当前猫娘。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": "测试猫娘"}

    async def _start_session(*args, **kwargs):
        """记录 Router 转交给 Runtime 的开场参数。"""  # noqa: DOCSTRING_CJK
        captured.update(kwargs)
        return {"ok": False, "reason": "session_character_mismatch"}

    async def _record_cleanup(_root):
        """记录旧休眠扫描；方案 A 完成后开场不得再进入这个兼容写入点。"""  # noqa: DOCSTRING_CJK
        captured["cleanup_calls"] += 1

    async def _noop_speech(_response):
        """跳过与本测试无关的开场对白播放。"""  # noqa: DOCSTRING_CJK
        return None

    config_manager = _FakeConfigManager()
    monkeypatch.setattr(theater_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(theater_router.runtime, "start_session", _start_session)
    monkeypatch.setattr(
        theater_router.runtime,
        "cleanup_expired_sessions",
        _record_cleanup,
        raising=False,
    )
    monkeypatch.setattr(theater_router, "_validate_theater_local_mutation", lambda *_args: None)
    monkeypatch.setattr(theater_router, "_speak_committed_dialogue", _noop_speech)

    class _FakeRequest:
        """提供最小合法开场 JSON。"""  # noqa: DOCSTRING_CJK

        async def json(self):
            """返回测试开场载荷。"""  # noqa: DOCSTRING_CJK
            return {"story_id": "test_story", "client_start_id": "start_router_recheck"}

    await theater_router.start_theater_session(_FakeRequest())

    assert captured["lanlan_name"] == "测试猫娘"
    assert captured["config_manager"] is config_manager
    assert captured["cleanup_calls"] == 0
