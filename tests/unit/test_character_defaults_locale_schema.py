"""Regression tests for localized character profile schemas and default templates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import (
    CHARACTER_GENDER_FIELD_KEYS,
    CHARACTER_NICKNAME_FIELD_KEYS,
    get_character_gender,
    get_character_nickname,
    get_localized_default_characters,
    resolve_master_display_name,
)
from main_routers.config_router.page_config import _resolve_master_display_name
from memory.stop_names import _assemble_stop_names
from services.theater.llm_context import _load_player_address
from utils.config_manager.persona_payload import _is_default_yui_character
from utils.language_utils import TranslationService


REPO_ROOT = Path(__file__).resolve().parents[2]
CHAR_DIR = REPO_ROOT / "config" / "characters"
ALL_LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


@pytest.mark.unit
def test_get_localized_default_characters_translates_korean():
    cfg = get_localized_default_characters("ko")
    master = cfg["主人"]
    default_catgirl = cfg["猫娘"]["test"]
    assert master["昵称"] == "오빠"
    assert master["性别"] == "남성"
    assert default_catgirl["性别"] == "여성"
    assert default_catgirl["昵称"] == "T짱, 꼬마 T"
    assert "T酱" not in default_catgirl["昵称"]


@pytest.mark.unit
@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_character_templates_align_with_localized_field_key_constants(locale: str):
    payload = json.loads((CHAR_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    master = payload["主人"]
    yui = payload["猫娘"]["YUI"]

    assert any(k in master for k in CHARACTER_NICKNAME_FIELD_KEYS)
    assert any(k in master for k in CHARACTER_GENDER_FIELD_KEYS)
    assert any(k in yui for k in CHARACTER_NICKNAME_FIELD_KEYS)
    assert any(k in yui for k in CHARACTER_GENDER_FIELD_KEYS)

    assert get_character_nickname(master)
    assert get_character_gender(master)
    assert get_character_nickname(yui) == "YUI"
    assert get_character_gender(yui)
    assert resolve_master_display_name(master) == get_character_nickname(master)


@pytest.mark.unit
@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_backend_readers_resolve_nicknames_from_all_locale_templates(locale: str):
    payload = json.loads((CHAR_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    master = payload["主人"]
    catgirls = payload["猫娘"]
    expected_master_nick = get_character_nickname(master)

    assert _resolve_master_display_name(master, "Fallback") == expected_master_nick
    assert _load_player_address(None, characters=payload) == expected_master_nick

    stop_names = _assemble_stop_names("Master", "YUI", master, catgirls, "YUI")
    assert expected_master_nick in stop_names
    assert "YUI" in stop_names

    # Verify default YUI detection matches localized nickname keys as well as name.
    renamed_card = dict(catgirls["YUI"])
    renamed_card["_reserved"] = {"avatar": {"live2d": {"model_path": "yui-lolita/yui-lolita.model3.json"}}}
    assert _is_default_yui_character("CustomName", renamed_card) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_translation_service_splits_comma_separated_localized_nickname_fields():
    service = TranslationService.__new__(TranslationService)
    service.translate_text_robust = AsyncMock(side_effect=lambda text, _lang: f"T({text})")

    translated = await service.translate_dict(
        {"Nickname": "Alice, Bob", "Gender": "Female", "档案名": "KeepMe"},
        "ja",
        fields_to_translate=list(CHARACTER_NICKNAME_FIELD_KEYS + CHARACTER_GENDER_FIELD_KEYS),
    )
    assert translated["Nickname"] == "T(Alice), T(Bob)"
    assert translated["Gender"] == "T(Female)"
    assert translated["档案名"] == "KeepMe"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_translation_service_matches_fields_to_translate_case_insensitively():
    service = TranslationService.__new__(TranslationService)
    service.translate_text_robust = AsyncMock(side_effect=lambda text, _lang: f"T:{text}")

    translated = await service.translate_dict(
        {
            "nickname": "Alice, Bob",
            "NICKNAME": "Charlie",
            "apodo": "Juan",
            "gender": "Female",
            "档案名": "KeepRaw",
        },
        "ja",
        fields_to_translate=list(CHARACTER_NICKNAME_FIELD_KEYS + CHARACTER_GENDER_FIELD_KEYS),
    )
    assert translated["nickname"] == "T:Alice, T:Bob"
    assert translated["NICKNAME"] == "T:Charlie"
    assert translated["apodo"] == "T:Juan"
    assert translated["gender"] == "T:Female"
    assert translated["档案名"] == "KeepRaw"


@pytest.mark.unit
def test_app_chat_js_resolves_localized_master_nicknames():
    js_path = REPO_ROOT / "static" / "app" / "app-chat.js"
    script = (
        "const fs = require('fs');\n"
        "const vm = require('vm');\n"
        "const assert = require('assert');\n"
        "(async () => {\n"
        "  for (const [profile, expectedDisplay, expectedNick] of [\n"
        "    [{ Nickname: 'Human, Buddy', '档案名': 'Carbon' }, 'Human', 'Human, Buddy'],\n"
        "    [{ nickname: 'lower, nick', '档案名': 'Carbon' }, 'lower', 'lower, nick'],\n"
        "    [{ NICKNAME: 'CaseUpper', '档案名': 'Carbon' }, 'CaseUpper', 'CaseUpper'],\n"
        "    [{ apodo: 'CaseLower', '档案名': 'Carbon' }, 'CaseLower', 'CaseLower'],\n"
        "    [{ APELIDO: 'CasePt', '档案名': 'Carbon' }, 'CasePt', 'CasePt'],\n"
        "    [{ 'прозвище': 'Боб', '档案名': 'Carbon' }, 'Боб', 'Боб'],\n"
        "    [{ NICKNAME: '  ', nickname: 'NonEmptyFallback', '档案名': 'Carbon' }, 'NonEmptyFallback', 'NonEmptyFallback'],\n"
        "    [{ '닉네임': '인간', '档案名': '탄소' }, '인간', '인간'],\n"
        "    [{ 'Прозвище': 'Человек' }, 'Человек', 'Человек'],\n"
        "    [{ '档案名': 'FallbackProfile' }, 'FallbackProfile', ''],\n"
        "  ]) {\n"
        "    const ctx = {\n"
        "      window: { appState: {}, addEventListener: () => {} },\n"
        "      localStorage: { getItem: () => null },\n"
        "      console,\n"
        "      fetch: async () => ({ ok: true, json: async () => ({ '主人': profile }) }),\n"
        "    };\n"
        "    ctx.window.window = ctx.window;\n"
        "    vm.createContext(ctx);\n"
        f"    vm.runInContext(fs.readFileSync({json.dumps(str(js_path))}, 'utf8'), ctx);\n"
        "    const resolved = await ctx.window.appChat.ensureUserDisplayName();\n"
        "    assert.strictEqual(resolved, expectedDisplay);\n"
        "    assert.strictEqual(ctx.window.master_display_name, expectedDisplay);\n"
        "    assert.strictEqual(ctx.window.master_nickname, expectedNick);\n"
        "  }\n"
        "})().catch((err) => { console.error(err); process.exit(1); });\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
