# -*- coding: utf-8 -*-
from utils.config_manager.persona_payload import (
    _append_persona_guidance_to_prompt,
    _format_character_card_prompt_block,
    _format_master_profile_prompt_block,
)


def test_format_character_card_prompt_block_includes_identity_fields():
    block = _format_character_card_prompt_block(
        {
            "自称": "本喵",
            "昵称": "余生channel",
            "核心特质": "傲娇 蛮横",
            "行为特点": "刀子嘴豆腐心",
            "_reserved": {"voice_id": "zh-CN-XiaoxiaoNeural"},
        }
    )
    assert "<Character Card>" in block
    assert "自称: 本喵" in block
    assert "昵称: 余生channel" in block
    assert "核心特质: 傲娇 蛮横" in block
    assert "AI character only" in block
    assert "voice_id" not in block


def test_format_master_profile_prompt_block_pins_user_address():
    block = _format_master_profile_prompt_block(
        {
            "档案名": "余生",
            "昵称": "人类",
            "性别": "男",
        }
    )
    assert "<User Profile>" in block
    assert "Address the user as: 余生" in block
    assert "档案名: 余生" in block
    assert "Never address the user with Character Card names" in block


def test_append_persona_guidance_pins_character_card_after_preset(monkeypatch):
    monkeypatch.setattr(
        "utils.persona_presets.get_persona_prompt_guidance",
        lambda preset_id, lang=None: "PRESET_GUIDANCE_BODY",
    )
    monkeypatch.setattr(
        "utils.config_manager.persona_payload.is_default_prompt",
        lambda text: True,
    )
    prompt = _append_persona_guidance_to_prompt(
        "DEFAULT_BASE",
        {
            "自称": "本喵",
            "昵称": "余生channel",
            "核心特质": "答应的一定会完成",
            "_reserved": {
                "persona_override": {
                    "preset_id": "tsundere_helper",
                    "prompt_guidance": "stale",
                }
            },
        },
        master_payload={"档案名": "余生", "昵称": "人类"},
    )
    assert prompt.startswith("PRESET_GUIDANCE_BODY")
    assert prompt.index("<User Profile>") < prompt.index("<Character Card>")
    assert "Address the user as: 余生" in prompt
    assert "自称: 本喵" in prompt
    assert "核心特质: 答应的一定会完成" in prompt
    assert "Never address the user with Character Card names" in prompt
