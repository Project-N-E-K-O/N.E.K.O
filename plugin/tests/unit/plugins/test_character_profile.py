from __future__ import annotations

import json
from pathlib import Path

import pytest


CHARACTER_DATA_DIR = (
    Path(__file__).parents[3] / "plugins" / "galgame_plugin" / "character_data"
)


def test_senren_banka_character_data_has_required_shape() -> None:
    payload = json.loads((CHARACTER_DATA_DIR / "senren_banka.json").read_text(encoding="utf-8"))

    assert payload["game_id"] == "senren_banka"
    assert payload["last_updated"] == "2026-05-18"
    assert "叢雨" in payload["characters"]
    murasame = payload["characters"]["叢雨"]
    assert murasame["identity"]
    assert murasame["character_voice"]["core_traits"]
    assert murasame["character_voice"]["first_person_pronoun"] == "わらわ"


def test_character_profile_manager_full_loading_is_opus_only() -> None:
    pytest.skip("CharacterProfileManager implementation is reserved for the Opus 4.7 phase")
