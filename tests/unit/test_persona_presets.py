from __future__ import annotations

import pytest

from utils.persona_presets import (
    PERSONA_OVERRIDE_FIELDS,
    _PERSONA_L10N,
    get_persona_preset,
    get_persona_prompt_guidance,
    list_persona_presets,
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Pure helper tests do not need the repo-level mock memory server."""
    yield


@pytest.mark.unit
def test_list_persona_presets_returns_three_fixed_presets():
    presets = list_persona_presets()

    assert [preset["preset_id"] for preset in presets] == [
        "classic_genki",
        "tsundere_helper",
        "elegant_butler",
    ]
    assert presets[0]["profile"]["性格原型"] == "经典元气猫娘"
    assert presets[1]["profile"]["性格原型"] == "傲娇毒舌小猫"
    assert presets[2]["profile"]["性格原型"] == "优雅全能管家"
    assert all(preset["profile"]["口癖"].startswith("不用固定") for preset in presets)
    assert "下不为例喵" not in repr(presets)


@pytest.mark.unit
def test_get_persona_preset_returns_copy():
    preset = get_persona_preset("classic_genki")
    assert preset is not None

    preset["profile"]["性格"] = "临时修改"

    fresh = get_persona_preset("classic_genki")
    assert fresh is not None
    assert fresh["profile"]["性格"] != "临时修改"


@pytest.mark.unit
def test_persona_override_fields_cover_supported_profile_keys():
    assert set(PERSONA_OVERRIDE_FIELDS) == {
        "性格原型",
        "性格",
        "口癖",
        "爱好",
        "雷点",
        "隐藏设定",
        "一句话台词",
    }


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru"])
def test_persona_prompts_replace_literal_catchphrase_lists_with_speech_discipline(lang):
    literal_list_markers = (
        "常用口癖",
        "口癖：",
        "Signature phrases:",
        "입버릇:",
        "Коронные фразы:",
    )

    for preset_id, localized_parts in _PERSONA_L10N.items():
        parts = localized_parts[lang]
        assert parts["speech_discipline"]
        assert not any(marker in parts["personality"] for marker in literal_list_markers)

        prompt = get_persona_prompt_guidance(preset_id, lang)
        assert "- Natural Speech:" in prompt
        assert "{_persona_speech_discipline}" not in prompt
        assert "下不为例喵" not in prompt
