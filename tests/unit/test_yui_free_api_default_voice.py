from __future__ import annotations

from copy import deepcopy
import json

import pytest

import main_routers.characters_router.voice_registry as characters_router
from utils.config_manager import ensure_default_yui_voice_for_free_api, get_reserved


YUI_FREE_VOICE_ID = "voice-tone-RcH2svtsrw"


class _FakeConfigManager:
    def __init__(self, characters: dict, core_config: dict | None = None, non_mainland: bool = False):
        self.characters = deepcopy(characters)
        # 生产里 aget_core_config() 返回的是**组装后**的配置——URL 由 get_core_config
        # 按 profile 填，持久化的 raw 里只有 coreApi / assistApi。替身默认也给一份带
        # URL 的免费快照，否则「这条线路是否依赖区域判定」那道门会失真（拿不到 URL
        # 就恒判为不依赖，把海外用户绑成大陆音色）。
        if core_config is None:
            core_config = {
                'coreApi': 'free',
                'assistApi': 'free',
                'CORE_URL': (
                    'wss://www.lanlan.app/core' if non_mainland
                    else 'wss://www.lanlan.tech/core'
                ),
            }
        self.core_config = deepcopy(core_config)
        self.saved_characters = None
        self._non_mainland = non_mainland

    def _check_non_mainland(self) -> bool:
        return self._non_mainland

    def _region_verdict_is_provisional(self, cfg=None) -> bool:
        # 这些用例考的是绑定逻辑本身，前提是区域判定已落定——未落定时
        # ensure_default_yui_voice_for_free_api 会跳过绑定（避免把猜出来的区域
        # 写成持久音色，之后不会被覆盖）。这里把该前提写明。
        return False

    async def aload_characters(self):
        return deepcopy(self.characters)

    async def asave_characters(self, characters):
        self.saved_characters = deepcopy(characters)
        self.characters = deepcopy(characters)

    async def aget_core_config(self):
        return deepcopy(self.core_config)


def _parse_json_response(response):
    body = getattr(response, "body", b"{}") or b"{}"
    return json.loads(body.decode("utf-8"))


def _characters_with_current_yui(*, voice_id: str = "", model_path: str = "yui-origin/yui-origin.model3.json") -> dict:
    return {
        "当前猫娘": "YUI",
        "猫娘": {
            "YUI": {
                "昵称": "YUI",
                "_reserved": {
                    "voice_id": voice_id,
                    "avatar": {
                        "model_type": "live2d",
                        "live2d": {
                            "model_path": model_path,
                        },
                    },
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_free_api_binds_empty_current_default_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id=""))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is True
    assert config_manager.saved_characters is not None
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == YUI_FREE_VOICE_ID


@pytest.mark.asyncio
async def test_free_api_bind_can_use_current_core_config_when_reader_entry_calls_without_payload(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id=""),
        core_config={"coreApi": "free", "assistApi": "free"},
    )

    changed = await ensure_default_yui_voice_for_free_api(config_manager)

    assert changed is True
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == YUI_FREE_VOICE_ID


@pytest.mark.asyncio
async def test_overseas_free_binds_yui_sentinel_voice(monkeypatch):
    """Overseas free (free + *.lanlan.app) binds the branded ``yui`` voice.

    ``non_mainland=True`` was added later: the fake used to hold a self
    contradictory state — ``CORE_URL`` already rewritten to ``.app`` while
    ``_check_non_mainland()`` reported mainland. Production cannot produce that
    combination (``.app`` only ever comes from an overseas verdict), and the
    contradiction is exactly what hid the bug PR #2454 fixes: when Steam
    provisionally says overseas the snapshot already carries ``.app``, and if the
    authoritative IP verdict then resolves mainland, inferring the region from
    that URL would bind ``yui`` to a mainland user forever.
    """
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id=""),
        non_mainland=True,
    )

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free", "CORE_URL": "wss://www.lanlan.app/realtime"},
    )

    assert changed is True
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == "yui"


@pytest.mark.asyncio
async def test_overseas_free_binds_yui_with_raw_lanlan_tech_url(monkeypatch):
    """回归 Codex P2：update_core_config 传入的 raw core_cfg 里 CORE_URL 仍是
    lanlan.tech（区域改写在 get_core_config 才发生），此时海外用户应靠
    _check_non_mainland 兜底判海外、绑 yui，而不是落到国内 voice-tone-* 预设。"""
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id=""),
        non_mainland=True,
    )

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free", "CORE_URL": "wss://www.lanlan.tech/core"},
    )

    assert changed is True
    yui = config_manager.saved_characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == "yui"


@pytest.mark.asyncio
async def test_non_free_api_does_not_bind_empty_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id=""))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "qwen", "assistApi": "qwen"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == ""


@pytest.mark.asyncio
async def test_free_api_does_not_overwrite_existing_yui_voice(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(_characters_with_current_yui(voice_id="custom-voice"))

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == "custom-voice"


@pytest.mark.asyncio
async def test_free_api_does_not_bind_non_default_yui_model(monkeypatch):
    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    config_manager = _FakeConfigManager(
        _characters_with_current_yui(voice_id="", model_path="custom-yui/custom-yui.model3.json")
    )

    changed = await ensure_default_yui_voice_for_free_api(
        config_manager,
        {"coreApi": "free", "assistApi": "free"},
    )

    assert changed is False
    assert config_manager.saved_characters is None
    yui = config_manager.characters["猫娘"]["YUI"]
    assert get_reserved(yui, "voice_id", default="") == ""


@pytest.mark.asyncio
async def test_clear_voice_ids_rebinds_default_yui_for_free_api(monkeypatch):
    config_manager = _FakeConfigManager(
        {
            "当前猫娘": "YUI",
            "猫娘": {
                "YUI": {
                    "昵称": "YUI",
                    "_reserved": {
                        "voice_id": "old-provider-voice",
                        "avatar": {
                            "model_type": "live2d",
                            "live2d": {
                                "model_path": "yui-origin/yui-origin.model3.json",
                            },
                        },
                    },
                },
                "别的角色": {
                    "_reserved": {
                        "voice_id": "other-provider-voice",
                    },
                },
            },
        },
        core_config={"coreApi": "free", "assistApi": "free"},
    )

    async def _noop_initialize():
        return None

    monkeypatch.setattr(
        "utils.api_config_loader.get_free_voices",
        lambda: {"yui_cn": YUI_FREE_VOICE_ID},
    )
    monkeypatch.setattr(characters_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(characters_router, "get_initialize_character_data", lambda: _noop_initialize)

    response = await characters_router.clear_voice_ids()
    body = _parse_json_response(response)

    assert body["success"] is True
    yui = config_manager.characters["猫娘"]["YUI"]
    other = config_manager.characters["猫娘"]["别的角色"]
    assert get_reserved(yui, "voice_id", default="") == YUI_FREE_VOICE_ID
    assert get_reserved(other, "voice_id", default="") == ""


@pytest.mark.asyncio
async def test_provisional_region_defers_default_voice_binding():
    """Binding on a guessed region writes data that later cannot be corrected.

    The mainland binding is ``yui_cn`` and the overseas one the literal ``yui``; the
    two are not interchangeable, and this helper refuses to overwrite any nonempty
    voice afterwards. So while the verdict is provisional it must not bind at all —
    not even read the character data. Waiting one round costs nothing.
    """
    mgr = _FakeConfigManager({"猫娘": {"YUI": {"昵称": "YUI", "_reserved": {"voice_id": ""}}}},
                             core_config={"coreApi": "free"})
    mgr._region_verdict_is_provisional = lambda *_a: True

    async def _boom():
        raise AssertionError('区域未落定时不应读取角色数据')

    mgr.aload_characters = _boom

    assert await ensure_default_yui_voice_for_free_api(mgr) is False
    assert mgr.saved_characters is None, '未落定时不应写入任何角色数据'
