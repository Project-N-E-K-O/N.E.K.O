import json

import pytest

# Importing the heavy worker package registers the hosted TTS providers
# (incl. MiMo's TTSProvider + preset_catalog) into tts_provider_registry.
from main_logic import tts_client  # noqa: F401
from utils import tts_provider_registry
from utils.mimo_tts_voices import MIMO_PRESET_CATALOG, normalize_mimo_tts_voice
from utils.native_voice_registry import get_provider, is_native_voice, list_providers
from utils.tts.providers import mimo as mimo_provider
from utils.voice_config import read_legacy_voice_id


class _CM:
    """Minimal ConfigManager stub for registry selection queries.

    selected_provider iterates every registered provider's is_selected, so the
    stub must answer the config reads they do (core_config, the raw
    core_config.json for vLLM/GPT-SoVITS dropdown, the tts_custom slot for the
    GPT-SoVITS legacy gate)."""

    def __init__(self, core_config, tts_custom_config=None, raw_core_config=None, stored_voice_ids=None):
        self._core_config = core_config
        self._tts_custom_config = tts_custom_config or {"is_custom": False}
        self._raw = raw_core_config if raw_core_config is not None else {}
        self._stored_voice_ids = set(stored_voice_ids or ())

    def get_core_config(self):
        return self._core_config

    def voice_id_exists_in_any_storage(self, voice_id):
        return voice_id in self._stored_voice_ids

    def load_json_config(self, filename, default):
        if filename == "core_config.json":
            return self._raw
        return default

    def get_model_api_config(self, model_type):
        if model_type == "tts_custom":
            return self._tts_custom_config
        if model_type == "realtime":
            return {"api_type": "qwen", "base_url": ""}
        return {}

    def get_tts_api_key(self, provider):
        return "test-key"


@pytest.mark.unit
def test_mimo_registered_as_hosted_with_preset_catalog():
    provider = tts_provider_registry.get("mimo")
    assert provider is not None
    assert provider.kind == "hosted"
    assert "preset" in provider.capabilities
    # 预制目录是 MiMo 内置音色的单一真相，挂在统一 provider 上
    assert provider.preset_catalog is MIMO_PRESET_CATALOG


@pytest.mark.unit
def test_mimo_no_longer_registered_as_native():
    # mimo 已从 native_voice_registry 摘除（不再 bootstrap、不挂 _PROVIDERS）
    assert get_provider("mimo") is None
    assert "mimo" not in list_providers()
    assert is_native_voice("Milo") is False


@pytest.mark.unit
def test_mimo_voice_aliases_normalize_to_catalog_ids():
    assert normalize_mimo_tts_voice("默认") == ("mimo_default", True)
    assert normalize_mimo_tts_voice("中文女") == ("冰糖", True)
    assert normalize_mimo_tts_voice("english male") == ("Milo", True)
    assert tts_provider_registry.is_preset_voice("mimo", "冰糖") is True
    assert tts_provider_registry.is_preset_voice("mimo", "not-a-mimo-voice") is False


@pytest.mark.unit
def test_partial_config_default_drives_mimo_fallback_aliases(monkeypatch):
    catalog = {"configured-default": "Configured"}
    monkeypatch.setattr(
        mimo_provider,
        "_CFG",
        {"voices": catalog, "default_voice": "configured-default"},
    )
    monkeypatch.setattr(mimo_provider, "MIMO_TTS_VOICE_GENDERS", catalog)

    preset_catalog = mimo_provider._create_preset_catalog()

    assert preset_catalog.normalize("default") == ("configured-default", True)
    assert preset_catalog.normalize("默认") == ("configured-default", True)


@pytest.mark.unit
def test_mimo_preset_catalog_for_ui_shape_matches_native():
    catalog = tts_provider_registry.preset_catalog_for_ui("mimo")
    assert catalog
    milo = catalog["Milo"]
    # 与 NativeVoiceProvider.voice_catalog_for_ui 同形态：前端按这些字段分组/渲染
    assert milo["provider"] == "mimo"
    assert milo["provider_label"] == "MiMo"
    assert milo["builtin"] is True
    assert set(milo) == {"prefix", "provider", "provider_label", "gender", "display_name", "builtin"}


def _selected_catalog(core_config, cm):
    # 模拟 /voices 取目录的两步：先拿赢家 key，再按 key 取其静态预制目录
    key = tts_provider_registry.selected_provider_key(core_config, cm)
    return tts_provider_registry.preset_catalog_for_ui(key) if key else None


@pytest.mark.unit
@pytest.mark.parametrize(
    "core_config",
    [
        {"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"},
        {"CORE_API_TYPE": "qwen", "assistApi": "mimo"},
    ],
)
def test_mimo_selected_exposes_preset_catalog_for_ui(core_config):
    catalog = _selected_catalog(core_config, _CM(core_config))
    assert catalog and "Milo" in catalog


@pytest.mark.unit
def test_mimo_assist_api_selects_catalog_even_over_core_native():
    # core=gemini 但 assistApi=mimo：mimo（priority 60）在 dispatch 里先于 native 命中，
    # UI 目录也应给 mimo（UI/校验与 dispatch 同一优先级判定）。
    core_config = {"CORE_API_TYPE": "gemini", "ttsProvider": "step", "assistApi": "mimo"}
    catalog = _selected_catalog(core_config, _CM(core_config))
    assert catalog and "Milo" in catalog


@pytest.mark.unit
def test_mimo_catalog_hidden_when_gptsovits_custom_tts_wins():
    # GPT-SoVITS（priority 10）先命中且无静态预制目录 → 不出 mimo 预制目录，也不算合法预制
    core_config = {"CORE_API_TYPE": "qwen", "assistApi": "mimo", "GPTSOVITS_ENABLED": True}
    cm = _CM(core_config, tts_custom_config={"is_custom": True})
    assert _selected_catalog(core_config, cm) is None
    assert tts_provider_registry.is_selected_preset_voice(core_config, cm, "Milo") is False
    # 赢家是无静态目录的 gptsovits：/voices 也应抑制 core-native（key 非 None 但目录为 None）
    assert tts_provider_registry.selected_provider_key(core_config, cm) == "gptsovits"


@pytest.mark.unit
def test_no_catalog_winner_suppresses_native_voices():
    # vLLM-Omni（无静态目录）赢得 dispatch：selected_provider_key 返回 vllm_omni、
    # 目录为 None → /voices 的三路分支既不出目录、也不回退 core-native。
    core_config = {"CORE_API_TYPE": "gemini", "ENABLE_CUSTOM_API": True}
    cm = _CM(core_config, raw_core_config={"ttsModelProvider": "vllm_omni"})
    key = tts_provider_registry.selected_provider_key(core_config, cm)
    assert key == "vllm_omni"
    assert tts_provider_registry.preset_catalog_for_ui(key) is None


@pytest.mark.unit
def test_preview_guard_blocks_mimo_preset_but_lets_same_name_clone_through():
    from main_routers.characters_router import _is_unpreviewable_selected_preset_voice

    cc = {"CORE_API_TYPE": "qwen", "assistApi": "mimo"}
    # 纯预制音色（无 voice_data、不在任何存储桶）→ 拦下（暂不支持试听）
    assert _is_unpreviewable_selected_preset_voice(_CM(cc), cc, "Milo", None) is True
    # 与预制同名的克隆音色：当前 api 有 voice_data → 放行走克隆试听
    assert _is_unpreviewable_selected_preset_voice(_CM(cc), cc, "Milo", {"provider": "cosyvoice"}) is False
    # 跨槽克隆（voice_data 为 None 但存在于任一存储桶）→ 同样放行
    assert _is_unpreviewable_selected_preset_voice(_CM(cc, stored_voice_ids={"Milo"}), cc, "Milo", None) is False
    # MiMo 未选中 → 不是预制，放行
    not_sel = {"CORE_API_TYPE": "qwen", "assistApi": "qwen"}
    assert _is_unpreviewable_selected_preset_voice(_CM(not_sel), not_sel, "Milo", None) is False


@pytest.mark.unit
def test_mimo_preset_voice_saveable_only_when_selected():
    selected = {"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"}
    not_selected = {"CORE_API_TYPE": "qwen", "assistApi": "qwen"}
    assert tts_provider_registry.is_selected_preset_voice(selected, _CM(selected), "Milo") is True
    assert tts_provider_registry.is_selected_preset_voice(not_selected, _CM(not_selected), "Milo") is False


# ── validate_voice_id 端到端（真 ConfigManager）：cleanup 用它判定有效性，
#    必须认 mimo 预制 voice 合法（选中时），否则存量 mimo 配置会被清空 ──────────


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


@pytest.mark.unit
def test_validate_voice_id_accepts_mimo_preset_when_selected(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "mimo"})
    assert config_manager.validate_voice_id("Milo") is True
    assert config_manager.validate_voice_id("冰糖") is True


@pytest.mark.unit
def test_validate_voice_id_rejects_mimo_preset_when_not_selected(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "qwen"})
    assert config_manager.validate_voice_id("Milo") is False


# ── normalize/storage 端到端：保存 MiMo 预制音色补全 source=preset/provider=mimo
#    ownership 元数据（#1842 结构化 schema 缺口，#1848 遗留），并仍 round-trip 回裸 key ──


@pytest.mark.unit
def test_normalize_mimo_preset_marks_source_preset_provider_mimo(config_manager):
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "mimo"})
    vc = config_manager.normalize_voice_id_to_config("Milo")
    assert (vc.source, vc.provider, vc.ref) == ("preset", "mimo", "Milo")
    # 写入存储：迁成结构对象（非裸串），且 round-trip 回库 key 'Milo'
    stored = config_manager.voice_id_to_storage_value("Milo")
    assert stored == {"source": "preset", "provider": "mimo", "ref": "Milo"}
    assert read_legacy_voice_id(stored) == "Milo"


@pytest.mark.unit
def test_normalize_mimo_preset_unresolved_when_not_selected(config_manager):
    # 未选中 MiMo → 不认作预制，裸 ref 原样带过（provider/source 空），不误标
    _write_core_config(config_manager, {"coreApi": "qwen", "assistApi": "qwen"})
    vc = config_manager.normalize_voice_id_to_config("Milo")
    assert (vc.source, vc.provider, vc.ref) == ("", "", "Milo")
    # 落库侧：未解析的裸 ref 存回扁平串（不写成空壳结构对象），守住回归
    stored = config_manager.voice_id_to_storage_value("Milo")
    assert stored == "Milo"
    assert read_legacy_voice_id(stored) == "Milo"
