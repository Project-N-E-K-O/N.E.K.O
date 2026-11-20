import asyncio
import json
from types import SimpleNamespace

import main_routers.characters_router.cards as cards_module


def _stub_config_manager(cards_dir):
    return SimpleNamespace(
        chara_dir=str(cards_dir),
        ensure_chara_directory=lambda: None,
    )


def _write_card(cards_dir, filename, payload):
    path = cards_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _list_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(cards_module, "get_config_manager", lambda: _stub_config_manager(tmp_path))
    return asyncio.run(cards_module.get_character_cards())


def test_list_keeps_card_that_only_declares_profile_name(tmp_path, monkeypatch):
    """The repo's own schema stores the card name under the profile-name field, which must not be dropped."""
    _write_card(tmp_path, "chinese.chara.json", {"档案名": "中文卡", "昵称": "小卡"})

    result = _list_cards(tmp_path, monkeypatch)

    assert result["success"] is True
    assert [card["name"] for card in result["character_cards"]] == ["中文卡"]
    assert result["character_cards"][0]["id"] == "chinese"
    assert result["character_cards"][0]["rawData"]["档案名"] == "中文卡"


def test_list_still_supports_name_field_and_prefers_it(tmp_path, monkeypatch):
    _write_card(tmp_path, "english.chara.json", {"name": "EnglishCard", "description": "d"})
    _write_card(tmp_path, "both.chara.json", {"name": "EnglishName", "档案名": "中文名"})

    result = _list_cards(tmp_path, monkeypatch)

    by_id = {card["id"]: card["name"] for card in result["character_cards"]}
    assert by_id == {"english": "EnglishCard", "both": "EnglishName"}


def test_list_skips_card_without_any_name(tmp_path, monkeypatch, caplog):
    _write_card(tmp_path, "nameless.chara.json", {"昵称": "没有卡名"})
    _write_card(tmp_path, "chinese.chara.json", {"档案名": "中文卡"})

    result = _list_cards(tmp_path, monkeypatch)

    assert [card["id"] for card in result["character_cards"]] == ["chinese"]
    assert "nameless.chara.json" in caplog.text
