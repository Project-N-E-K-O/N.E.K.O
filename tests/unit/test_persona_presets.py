from __future__ import annotations

import pytest

from utils.persona_presets import (
    PERSONA_OVERRIDE_FIELDS,
    get_persona_preset,
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
