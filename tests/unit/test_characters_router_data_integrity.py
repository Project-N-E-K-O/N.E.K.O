import asyncio
import copy
import importlib
import json

import pytest

from config import CHARACTER_RESERVED_FIELDS
from utils.cloudsave_runtime import MaintenanceModeError
from utils.config_manager import get_reserved, set_reserved

live2d_models_module = importlib.import_module("main_routers.characters_router.live2d_models")
crud_module = importlib.import_module("main_routers.characters_router.crud")
voice_registry_module = importlib.import_module("main_routers.characters_router.voice_registry")
cards_module = importlib.import_module("main_routers.characters_router.cards")


class DummyRequest:
    def __init__(self, payload, query_params=None):
        self._payload = payload
        self.query_params = query_params or {}
        self.parsed_event = asyncio.Event()

    async def json(self):
        self.parsed_event.set()
        return self._payload


class DummyConfigManager:
    def __init__(self, characters, *, voices=None):
        self.characters = copy.deepcopy(characters)
        self.voices = dict(voices or {"v1": {"name": "Voice 1"}, "v2": {"name": "Voice 2"}})
        self.save_started = asyncio.Event()
        self.allow_save_finish = asyncio.Event()
        self.allow_save_finish.set()
        self.raise_maintenance_on_save = False
        self.deleted_voices = []

    def load_characters(self):
        return copy.deepcopy(self.characters)

    async def aload_characters(self, character_json_path=None):
        return copy.deepcopy(self.characters)

    def save_characters(self, characters, character_json_path=None):
        if self.raise_maintenance_on_save:
            raise MaintenanceModeError("maintenance")
        self.characters = copy.deepcopy(characters)

    async def asave_characters(self, characters, character_json_path=None):
        self.save_started.set()
        await self.allow_save_finish.wait()
        self.save_characters(characters, character_json_path)

    def validate_voice_id(self, voice_id):
        return voice_id in self.voices

    def get_voices_for_current_api(self):
        return dict(self.voices)

    def voice_id_to_storage_value(self, voice_id):
        return {"source": "clone", "provider": "cosyvoice", "ref": voice_id}

    def delete_voice_for_current_api(self, voice_id):
        if voice_id in self.voices:
            self.voices.pop(voice_id)
            self.deleted_voices.append(voice_id)
            return True
        return False


class DummySessionMgr:
    def __init__(self, *, is_active=False, is_starting=False, input_mode="text"):
        self.is_active = is_active
        self.is_starting = is_starting
        self.input_mode = input_mode
        self.starting_input_mode = input_mode
        self.notified = False
        self.ended = False
        self.reset_called = False

    async def send_session_ended_by_server(self):
        self.notified = True

    async def end_session(self, by_server=False):
        self.ended = True

    def reset_session_start_circuit(self):
        self.reset_called = True


def _base_characters():
    return {
        "当前猫娘": "测试猫娘",
        "当前麦克风": "mic-old",
        "主人": {"档案名": "主人", "描述": "old"},
        "猫娘": {
            "测试猫娘": {
                "性格": "温柔",
                "_reserved": {
                    "character_id": "cid-1",
                    "character_uid": "0123456789abcdef0123456789abcdef",
                    "voice_id": {"source": "clone", "provider": "cosyvoice", "ref": "v1"},
                    "avatar": {
                        "model_type": "live3d",
                        "live3d_sub_type": "vrm",
                        "vrm": {"model_path": "/user_vrm/test.vrm"},
                    },
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_f0016_touch_set_lighting_mmd_wait_for_lock_and_reload(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(live2d_models_module, "character_config_mutation_lock", lock)

    # 1. update_catgirl_touch_set
    cm = DummyConfigManager(_base_characters())
    init_lock_states = []

    async def _init_one(name, is_new=False):
        init_lock_states.append(lock.locked())

    monkeypatch.setattr(live2d_models_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(live2d_models_module, "get_init_one_catgirl", lambda: _init_one)

    req = DummyRequest({"model_name": "mao", "touch_set": {"default": {"motions": ["m1"]}}})
    async with lock:
        task = asyncio.create_task(live2d_models_module.update_catgirl_touch_set("测试猫娘", req))
        await req.parsed_event.wait()
        assert not task.done()
        chars = cm.load_characters()
        set_reserved(chars["猫娘"]["测试猫娘"], "character_origin", "model_ref", "/concurrent/touch")
        cm.save_characters(chars)

    resp = await task
    assert resp.status_code == 200
    saved = cm.load_characters()["猫娘"]["测试猫娘"]
    assert get_reserved(saved, "character_origin", "model_ref") == "/concurrent/touch"
    assert get_reserved(saved, "touch_set")["mao"] == {"motions": ["m1"]} or get_reserved(saved, "touch_set")["mao"] == {"default": {"motions": ["m1"]}}
    assert init_lock_states == [False]

    # 2. update_catgirl_lighting
    cm2 = DummyConfigManager(_base_characters())
    init_lock_states.clear()
    monkeypatch.setattr(live2d_models_module, "get_config_manager", lambda: cm2)
    req2 = DummyRequest({"lighting": {"ambient": 0.9}, "apply_runtime": True})
    async with lock:
        task2 = asyncio.create_task(live2d_models_module.update_catgirl_lighting("测试猫娘", req2))
        await req2.parsed_event.wait()
        assert not task2.done()
        chars = cm2.load_characters()
        set_reserved(chars["猫娘"]["测试猫娘"], "character_origin", "model_ref", "/concurrent/light")
        cm2.save_characters(chars)

    resp2 = await task2
    assert resp2.status_code == 200
    saved2 = cm2.load_characters()["猫娘"]["测试猫娘"]
    assert get_reserved(saved2, "character_origin", "model_ref") == "/concurrent/light"
    assert get_reserved(saved2, "avatar", "vrm", "lighting", "ambient") == 0.9
    assert init_lock_states == [False]

    # 3. update_catgirl_mmd_settings
    cm3 = DummyConfigManager(_base_characters())
    monkeypatch.setattr(live2d_models_module, "get_config_manager", lambda: cm3)
    req3 = DummyRequest({"lighting": {"ambient": 0.75}, "physics": {"enabled": False}})
    async with lock:
        task3 = asyncio.create_task(live2d_models_module.update_catgirl_mmd_settings("测试猫娘", req3))
        await req3.parsed_event.wait()
        assert not task3.done()
        chars = cm3.load_characters()
        set_reserved(chars["猫娘"]["测试猫娘"], "character_origin", "model_ref", "/concurrent/mmd")
        cm3.save_characters(chars)

    resp3 = await task3
    assert resp3.status_code == 200
    saved3 = cm3.load_characters()["猫娘"]["测试猫娘"]
    assert get_reserved(saved3, "character_origin", "model_ref") == "/concurrent/mmd"
    assert get_reserved(saved3, "avatar", "mmd", "physics", "enabled") is False


@pytest.mark.asyncio
async def test_f0016_maintenance_mode_and_cancellation_retirement(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(live2d_models_module, "character_config_mutation_lock", lock)
    cm = DummyConfigManager(_base_characters())
    cm.raise_maintenance_on_save = True
    monkeypatch.setattr(live2d_models_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(live2d_models_module, "get_init_one_catgirl", lambda: None)

    with pytest.raises(MaintenanceModeError):
        await live2d_models_module.update_catgirl_touch_set(
            "测试猫娘", DummyRequest({"model_name": "mao", "touch_set": {}})
        )
    with pytest.raises(MaintenanceModeError):
        await live2d_models_module.update_catgirl_lighting(
            "测试猫娘", DummyRequest({"lighting": {"ambient": 0.5}})
        )
    with pytest.raises(MaintenanceModeError):
        await live2d_models_module.update_catgirl_mmd_settings(
            "测试猫娘", DummyRequest({"physics": {"enabled": True}})
        )

    # Verify await_retirement keeps lock held until in-flight save completes on cancellation
    cm.raise_maintenance_on_save = False
    cm.save_started.clear()
    cm.allow_save_finish.clear()
    task = asyncio.create_task(
        live2d_models_module.update_catgirl_mmd_settings(
            "测试猫娘", DummyRequest({"physics": {"enabled": False}})
        )
    )
    await cm.save_started.wait()
    assert lock.locked()
    task.cancel()
    await asyncio.sleep(0)
    assert lock.locked()
    cm.allow_save_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()


@pytest.mark.asyncio
async def test_f0190_update_master_and_set_microphone_serialize_with_lock(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(crud_module, "character_config_mutation_lock", lock)
    cm = DummyConfigManager(_base_characters())
    init_lock_states = []

    async def _init_all():
        init_lock_states.append(lock.locked())

    monkeypatch.setattr(crud_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(crud_module, "get_initialize_character_data", lambda: _init_all)

    # 1. update_master
    req_master = DummyRequest({"档案名": "主人", "性格": "冷静"})
    async with lock:
        task = asyncio.create_task(crud_module.update_master(req_master))
        await req_master.parsed_event.wait()
        assert not task.done()
        chars = cm.load_characters()
        chars["猫娘"]["并发新猫娘"] = {"性格": "活泼"}
        cm.save_characters(chars)

    res = await task
    assert res == {"success": True}
    saved = cm.load_characters()
    assert "并发新猫娘" in saved["猫娘"]
    assert saved["主人"]["性格"] == "冷静"
    assert init_lock_states == [False]

    # 2. set_microphone
    req_mic = DummyRequest({"microphone_id": "mic-new"})
    async with lock:
        task2 = asyncio.create_task(crud_module.set_microphone(req_mic))
        await req_mic.parsed_event.wait()
        assert not task2.done()
        chars = cm.load_characters()
        chars["猫娘"]["并发新猫娘"]["备注"] = "kept"
        cm.save_characters(chars)

    res2 = await task2
    assert res2 == {"success": True}
    saved2 = cm.load_characters()
    assert saved2["当前麦克风"] == "mic-new"
    assert saved2["猫娘"]["并发新猫娘"]["备注"] == "kept"

    # MaintenanceModeError propagation for set_microphone
    cm.raise_maintenance_on_save = True
    with pytest.raises(MaintenanceModeError):
        await crud_module.set_microphone(DummyRequest({"microphone_id": "mic-x"}))


@pytest.mark.asyncio
async def test_f0196_voice_registry_routes_serialize_with_lock(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(voice_registry_module, "character_config_mutation_lock", lock)

    cm = DummyConfigManager(_base_characters())
    mgr = DummySessionMgr(is_active=True)
    session_mgrs = {"测试猫娘": mgr}
    observed_lock_states = []

    async def _init_one(name, is_new=False):
        observed_lock_states.append(("init_one", lock.locked()))

    async def _init_all():
        observed_lock_states.append(("init_all", lock.locked()))

    async def _fake_yui(_cm):
        observed_lock_states.append(("yui", lock.locked()))
        chars = await _cm.aload_characters()
        await _cm.asave_characters(chars)

    monkeypatch.setattr(voice_registry_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(voice_registry_module, "get_session_manager", lambda: session_mgrs)
    monkeypatch.setattr(voice_registry_module, "get_init_one_catgirl", lambda: _init_one)
    monkeypatch.setattr(voice_registry_module, "get_initialize_character_data", lambda: _init_all)
    monkeypatch.setattr(voice_registry_module, "ensure_default_yui_voice_for_free_api", _fake_yui)

    # 1. update_catgirl_voice_id
    req = DummyRequest({"voice_id": "v2"})
    async with lock:
        t1 = asyncio.create_task(voice_registry_module.update_catgirl_voice_id("测试猫娘", req))
        await req.parsed_event.wait()
        assert not t1.done()
        chars = cm.load_characters()
        chars["猫娘"]["测试猫娘"]["备注"] = "concurrent-1"
        cm.save_characters(chars)

    r1 = await t1
    assert r1["success"] is True and r1["voice_id_changed"] is True
    assert cm.load_characters()["猫娘"]["测试猫娘"]["备注"] == "concurrent-1"
    assert get_reserved(cm.load_characters()["猫娘"]["测试猫娘"], "voice_id") == {
        "source": "clone",
        "provider": "cosyvoice",
        "ref": "v2",
    }
    assert mgr.ended is True
    assert observed_lock_states == [("init_one", False)]

    # 2. unregister_voice
    observed_lock_states.clear()
    mgr.ended = False
    async with lock:
        t2 = asyncio.create_task(voice_registry_module.unregister_voice("测试猫娘"))
        await asyncio.sleep(0)
        assert not t2.done()
        chars = cm.load_characters()
        chars["猫娘"]["测试猫娘"]["备注"] = "concurrent-2"
        cm.save_characters(chars)

    r2 = await t2
    assert r2["success"] is True
    assert cm.load_characters()["猫娘"]["测试猫娘"]["备注"] == "concurrent-2"
    assert get_reserved(cm.load_characters()["猫娘"]["测试猫娘"], "voice_id") == ""
    assert observed_lock_states == [("init_one", False)]

    # 3. clear_voice_ids (holds lock through ensure_default_yui_voice_for_free_api, releases before initialize_character_data)
    observed_lock_states.clear()
    chars = cm.load_characters()
    set_reserved(chars["猫娘"]["测试猫娘"], "voice_id", {"source": "clone", "provider": "cosyvoice", "ref": "v2"})
    cm.save_characters(chars)
    async with lock:
        t3 = asyncio.create_task(voice_registry_module.clear_voice_ids())
        await asyncio.sleep(0)
        assert not t3.done()
        chars2 = cm.load_characters()
        chars2["猫娘"]["测试猫娘"]["备注"] = "concurrent-3"
        cm.save_characters(chars2)

    r3 = await t3
    assert r3.status_code == 200
    assert json.loads(r3.body)["cleared_count"] == 1
    assert cm.load_characters()["猫娘"]["测试猫娘"]["备注"] == "concurrent-3"
    assert observed_lock_states == [("yui", True), ("init_all", False)]

    # 4. delete_voice
    observed_lock_states.clear()
    mgr.ended = False
    chars = cm.load_characters()
    set_reserved(chars["猫娘"]["测试猫娘"], "voice_id", {"source": "clone", "provider": "cosyvoice", "ref": "v2"})
    cm.save_characters(chars)
    async with lock:
        t4 = asyncio.create_task(voice_registry_module.delete_voice("v2"))
        await asyncio.sleep(0)
        assert not t4.done()
        chars2 = cm.load_characters()
        chars2["猫娘"]["测试猫娘"]["备注"] = "concurrent-4"
        cm.save_characters(chars2)

    r4 = await t4
    assert r4["success"] is True
    assert cm.load_characters()["猫娘"]["测试猫娘"]["备注"] == "concurrent-4"
    assert get_reserved(cm.load_characters()["猫娘"]["测试猫娘"], "voice_id") == ""
    assert observed_lock_states == [("init_all", False)]

    # MaintenanceModeError propagation on voice_registry routes
    cm.raise_maintenance_on_save = True
    with pytest.raises(MaintenanceModeError):
        await voice_registry_module.clear_voice_ids()


@pytest.mark.asyncio
async def test_b11_004_save_character_card_preserves_reserved_fields_and_ignores_forged(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(cards_module, "character_config_mutation_lock", lock)

    existing_characters = {
        "当前猫娘": "Mimi",
        "猫娘": {
            "Mimi": {
                "性格": "傲娇",
                "旧字段": "should_be_replaced",
                "_field_order": ["性格", "旧字段"],
                "创意工坊物品ID": "ws-999",
                "_reserved": {
                    "character_id": "cid-mimi",
                    "character_uid": "abcdef0123456789abcdef0123456789",
                    "voice_id": {"source": "clone", "provider": "cosyvoice", "ref": "v-mimi"},
                    "avatar": {
                        "model_type": "live3d",
                        "live3d_sub_type": "vrm",
                        "live2d": {"model_path": "mao/mao.model3.json"},
                        "vrm": {"model_path": "/user_vrm/mimi.vrm", "lighting": {"ambient": 0.7}},
                    },
                    "touch_set": {"mao": {"default": {"motions": ["tap"]}}},
                    "system_prompt": "Keep this prompt",
                    "persona_override": "Custom persona",
                    "ai_context": {"rename_events": [{"from": "A", "to": "Mimi"}]},
                    "character_origin": {"source": "workshop"},
                    "field_order": ["性格", "旧字段"],
                },
            }
        },
    }
    cm = DummyConfigManager(existing_characters)

    async def _noop_refresh(*args, **kwargs):
        return {
            "context_refreshed": True,
            "recent_history_cleared": False,
            "reload_notified": False,
            "session_restarted": False,
        }

    async def _noop_init_one(name, is_new=False):
        return None

    monkeypatch.setattr(cards_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(cards_module, "_refresh_catgirl_context_after_profile_change", _noop_refresh)
    monkeypatch.setattr(cards_module, "get_init_one_catgirl", lambda: _noop_init_one)

    payload = {
        "character_card_name": "Mimi",
        "charaData": {
            "档案名": "Mimi",
            "性格": "元气",
            "爱好": "吃鱼",
            # Forged reserved fields that MUST be ignored
            "_reserved": {"character_id": "forged", "voice_id": "forged", "system_prompt": "forged"},
            "voice_id": "forged-voice",
            "live2d": "forged-live2d",
            "model_type": "forged-type",
            "system_prompt": "forged-prompt",
            "character_id": "forged-id",
            "创意工坊物品ID": "forged-ws",
        },
    }
    res = await cards_module.save_character_card(DummyRequest(payload))
    assert res["success"] is True

    saved_mimi = cm.load_characters()["猫娘"]["Mimi"]
    # Ordinary fields updated
    assert saved_mimi["性格"] == "元气"
    assert saved_mimi["爱好"] == "吃鱼"
    assert "旧字段" not in saved_mimi
    # All reserved fields preserved intact and forged values rejected
    assert saved_mimi["创意工坊物品ID"] == "ws-999"
    assert saved_mimi["_reserved"]["character_id"] == "cid-mimi"
    assert saved_mimi["_reserved"]["character_uid"] == "abcdef0123456789abcdef0123456789"
    assert saved_mimi["_reserved"]["voice_id"] == {"source": "clone", "provider": "cosyvoice", "ref": "v-mimi"}
    assert saved_mimi["_reserved"]["avatar"]["vrm"]["model_path"] == "/user_vrm/mimi.vrm"
    assert saved_mimi["_reserved"]["touch_set"] == {"mao": {"default": {"motions": ["tap"]}}}
    assert saved_mimi["_reserved"]["system_prompt"] == "Keep this prompt"
    assert saved_mimi["_reserved"]["persona_override"] == "Custom persona"
    assert saved_mimi["_reserved"]["ai_context"] == {"rename_events": [{"from": "A", "to": "Mimi"}]}
    assert saved_mimi["_reserved"]["character_origin"] == {"source": "workshop"}
    assert saved_mimi["_reserved"]["field_order"] == ["性格", "爱好"]
    for key in CHARACTER_RESERVED_FIELDS:
        if key not in {"_reserved", "_field_order", "创意工坊物品ID"}:
            assert key not in saved_mimi

    # New character save still strips all incoming reserved fields and mints fresh IDs
    async def _noop_pending(*args, **kwargs):
        return True, ""

    async def _noop_reload(*args, **kwargs):
        return True

    async def _noop_activate(cm_arg, chars_arg, name_arg):
        await cm_arg.asave_characters(chars_arg)
        return False

    monkeypatch.setattr(cards_module, "_mark_new_character_greeting_pending_safe", _noop_pending)
    monkeypatch.setattr(cards_module, "notify_memory_server_reload", _noop_reload)
    monkeypatch.setattr(cards_module, "asave_characters_with_recent_activation", _noop_activate)
    monkeypatch.setattr(cards_module, "_visit_name_refusal", lambda *a, **kw: asyncio.sleep(0, result=None))

    new_payload = {
        "character_card_name": "NewCat",
        "charaData": {
            "档案名": "NewCat",
            "性格": "好奇",
            "_reserved": {"character_id": "forged", "system_prompt": "forged"},
            "voice_id": "forged-voice",
        },
    }
    res_new = await cards_module.save_character_card(DummyRequest(new_payload))
    assert res_new["success"] is True
    saved_new = cm.load_characters()["猫娘"]["NewCat"]
    assert saved_new["性格"] == "好奇"
    assert "voice_id" not in saved_new
    assert saved_new["_reserved"]["character_id"] != "forged"
    assert "system_prompt" not in saved_new["_reserved"]
    assert saved_new["_reserved"].get("character_uid")
