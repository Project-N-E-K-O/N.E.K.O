"""Scoped persona section headers are chosen by (subject_kind, platform).

A ``participant`` subject ``neko_visit:<uid>`` and a ``group_chat`` subject
``neko_visit:<pair>`` share the same id prefix, so the table must be picked by
kind and platform together; ``qq:*`` subjects keep their existing headers
byte for byte.
"""
from __future__ import annotations

import pytest

from config.prompts import prompts_memory as pm
from config.prompts.prompts_memory import (
    SCOPED_PERSONA_SECTION_HEADER,
    SCOPED_PERSONA_SECTION_HEADER_NAMED,
    get_scoped_persona_section_header,
)

LOCALES = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
VISIT_KEYS = ("group_chat@neko_visit", "participant@neko_visit")


@pytest.mark.parametrize("key", VISIT_KEYS)
def test_visit_tables_exist_in_both_header_tables(key: str):
    assert set(SCOPED_PERSONA_SECTION_HEADER[key]) == set(LOCALES)
    assert set(SCOPED_PERSONA_SECTION_HEADER_NAMED[key]) == set(LOCALES)
    for lang in LOCALES:
        assert "{subject_id}" in SCOPED_PERSONA_SECTION_HEADER[key][lang]
        named = SCOPED_PERSONA_SECTION_HEADER_NAMED[key][lang]
        assert "{display_name}" in named and "{subject_id}" in named


def test_group_participant_has_no_visit_table():
    assert "group_participant@neko_visit" not in SCOPED_PERSONA_SECTION_HEADER
    assert "group_participant@neko_visit" not in SCOPED_PERSONA_SECTION_HEADER_NAMED


@pytest.mark.parametrize("lang", LOCALES)
def test_visit_group_chat_picks_visit_table(lang: str):
    sid = "neko_visit:pair123"
    assert get_scoped_persona_section_header("group_chat", sid, lang) == (
        SCOPED_PERSONA_SECTION_HEADER["group_chat@neko_visit"][lang].format(subject_id=sid)
    )
    assert get_scoped_persona_section_header(
        "group_chat", sid, lang, display_name="Kiki",
    ) == SCOPED_PERSONA_SECTION_HEADER_NAMED["group_chat@neko_visit"][lang].format(
        display_name="Kiki", subject_id=sid,
    )


@pytest.mark.parametrize("lang", LOCALES)
def test_visit_participant_picks_visit_table(lang: str):
    sid = "neko_visit:uid42"
    assert get_scoped_persona_section_header("participant", sid, lang) == (
        SCOPED_PERSONA_SECTION_HEADER["participant@neko_visit"][lang].format(subject_id=sid)
    )
    assert get_scoped_persona_section_header(
        "participant", sid, lang, display_name="Kiki",
    ) == SCOPED_PERSONA_SECTION_HEADER_NAMED["participant@neko_visit"][lang].format(
        display_name="Kiki", subject_id=sid,
    )


def test_same_prefix_different_kind_gets_different_headers():
    # Mutation guard: a bare-prefix lookup ("neko_visit" -> one table) would
    # give both kinds the same header.
    group = get_scoped_persona_section_header("group_chat", "neko_visit:x", "zh")
    member = get_scoped_persona_section_header("participant", "neko_visit:x", "zh")
    assert group != member
    assert group == "串门记忆（neko_visit:x）"
    assert member == "串门对象记忆（neko_visit:x）"


@pytest.mark.parametrize("lang", LOCALES)
def test_visit_group_participant_falls_back_to_generic(lang: str):
    sid = "neko_visit:pair:uid"
    assert get_scoped_persona_section_header("group_participant", sid, lang) == (
        SCOPED_PERSONA_SECTION_HEADER["group_participant"][lang].format(subject_id=sid)
    )
    assert get_scoped_persona_section_header(
        "group_participant", sid, lang, display_name="N",
    ) == SCOPED_PERSONA_SECTION_HEADER_NAMED["group_participant"][lang].format(
        display_name="N", subject_id=sid,
    )


# Snapshot of the qq headers before the kind+platform selector existed; they
# must not change by a single byte.
_QQ_SNAPSHOT = {
    ("group_chat", "qq:7788", "zh", None): "群聊记忆（qq:7788）",
    ("group_chat", "qq:7788", "zh-TW", "水群"): "群組聊天記憶（水群，qq:7788）",
    ("participant", "qq:1", "en", None): "Participant memory (qq:1)",
    ("participant", "qq:1", "ja", "太郎"): "メンバーの記憶（太郎、qq:1）",
    ("group_participant", "qq:7788:1", "zh-TW", "小明"): "群組內成員記憶（小明，qq:7788:1）",
    ("group_participant", "qq:7788:1", "ru", None): "Память об участнике группы (qq:7788:1)",
    ("unknown_kind", "qq:1", "zh", "x"): "qq:1",
}


@pytest.mark.parametrize(("args", "expected"), list(_QQ_SNAPSHOT.items()))
def test_qq_headers_are_unchanged(args, expected):
    kind, sid, lang, display = args
    out = get_scoped_persona_section_header(kind, sid, lang, display_name=display)
    assert out.encode("utf-8") == expected.encode("utf-8")


@pytest.mark.parametrize("lang", LOCALES)
@pytest.mark.parametrize("kind", ["group_chat", "participant", "group_participant"])
def test_qq_headers_match_generic_tables_for_every_locale(kind: str, lang: str):
    sid = "qq:1:2" if kind == "group_participant" else "qq:1"
    assert get_scoped_persona_section_header(kind, sid, lang) == (
        SCOPED_PERSONA_SECTION_HEADER[kind][lang].format(subject_id=sid)
    )


def test_subject_id_without_platform_uses_kind_table():
    assert get_scoped_persona_section_header("group_chat", "bare", "en") == (
        "Group chat memory (bare)"
    )


def test_visit_tables_have_traditional_chinese_distinct_from_simplified():
    for table in (SCOPED_PERSONA_SECTION_HEADER, SCOPED_PERSONA_SECTION_HEADER_NAMED):
        for key in VISIT_KEYS:
            assert table[key]["zh-TW"] != table[key]["zh"], key
    assert callable(pm.get_scoped_persona_section_header)
