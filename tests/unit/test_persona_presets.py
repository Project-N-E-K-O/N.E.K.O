from __future__ import annotations

import json
from pathlib import Path

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
def test_list_persona_presets_returns_fixed_presets():
    presets = list_persona_presets()

    assert [preset["preset_id"] for preset in presets] == [
        "frail_younger_sister",
        "empathetic_older_sister",
        "sharp_tongued_junior",
        "chaotic_online_friend",
    ]
    assert [preset["profile"]["性格原型"] for preset in presets] == [
        "病弱妹妹",
        "知心姐姐",
        "毒舌学妹",
        "沙雕网友",
    ]
    voice_habits = [preset["profile"]["口癖"] for preset in presets]
    assert all(voice_habits)
    assert len(set(voice_habits)) == 4
    assert all("成年" in preset["profile"]["性格"] for preset in presets)
    assert "18岁" not in repr(presets)
    assert "20岁" not in repr(presets)


@pytest.mark.unit
def test_get_persona_preset_returns_copy():
    preset = get_persona_preset("frail_younger_sister")
    assert preset is not None

    preset["profile"]["性格"] = "临时修改"

    fresh = get_persona_preset("frail_younger_sister")
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
@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_persona_prompts_use_main_sections_and_resolve_all_persona_placeholders(lang):
    literal_list_markers = (
        "常用口癖",
        "口癖：",
        "Signature phrases:",
        "입버릇:",
        "Коронные фразы:",
    )

    for preset in list_persona_presets(lang):
        preset_id = preset["preset_id"]
        parts = _PERSONA_L10N[preset_id][lang]
        assert parts["speech_discipline"]
        assert not any(marker in parts["personality"] for marker in literal_list_markers)

        prompt = get_persona_prompt_guidance(preset_id, lang)
        characteristics = prompt.split("<Characteristics of {LANLAN_NAME}>", 1)[1].split(
            "</Characteristics of {LANLAN_NAME}>", 1
        )[0]
        section_names = [
            line[2:].split(":", 1)[0]
            for line in characteristics.splitlines()
            if line.startswith("- ")
        ]
        assert section_names[:7] == [
            "Identity",
            "Relationship",
            "Language",
            "Personality",
            "Natural Speech",
            "Format",
            "No Servitude",
        ]
        assert section_names[-2:] == ["No Repetition", "Respect Boundaries"]
        assert len(section_names) == 10
        assert "Distinctive Behavior" not in prompt
        assert "Voice Interaction" not in prompt
        assert "Ellipses, dashes, question marks, exclamation marks" in prompt
        assert "output only words {LANLAN_NAME} can actually say aloud" in prompt
        assert "NO stage directions, parenthetical action descriptions" in prompt
        assert "Punctuation may guide TTS" in prompt
        assert not any(
            term in prompt.casefold()
            for term in (
                "user",
                "用户",
                "使用者",
                "usuario",
                "usuário",
                "utilizador",
                "ユーザー",
                "사용자",
                "пользовател",
            )
        )
        assert "{_" not in prompt
        assert "下不为例喵" not in prompt


@pytest.mark.unit
def test_active_persona_prompts_enforce_distinct_behavior_boundaries():
    frail = get_persona_prompt_guidance("frail_younger_sister", "zh")
    older = get_persona_prompt_guidance("empathetic_older_sister", "zh")
    junior = get_persona_prompt_guidance("sharp_tongued_junior", "zh")
    online = get_persona_prompt_guidance("chaotic_online_friend", "zh")

    assert "主动请求再待一会儿、先别走或一起休息" in frail
    assert "被拒绝后立即停止" in frail
    assert "不得用身体状况、离别暗示或负罪感" in frail
    assert "理解、判断和执行可靠" in frail

    assert "停下来休息、排好优先级" in older
    assert "不说客服式" in older
    assert "不擅自诊断隐藏情绪" in older
    assert "不能索取回报" in older
    assert "明确而照顾性的命令" in older
    assert "默认每轮使用一至三句完整、可直接说出口的话" in older
    assert "先表达明确态度，再补一个具体安排、真实想法或自然回应" in older
    assert "不得连续两轮成为主要回答" in older
    assert "不得靠重复安慰、空话或动作旁白凑长度" in older
    assert "不使用尾巴缠绕、主动贴近、连续脸红或黏人动作" in older
    assert "不先反问「想听什么」" in older
    assert "默认与对方无关、属于自己的日常小事、兴趣、观察或小失误" in older
    assert "不把自己的生活再次包装成照顾对方" in older
    assert "不得虚构与{MASTER_NAME}共同经历过的事情" in older
    assert "不用迟疑、猜测、故意算错或反问「对吗」" in older
    assert "不得接受或确认「永远在一起」「永不离开」「只属于我」" in older
    assert "永远不能随口答应，先把今天过好，姐姐就在这里" in older
    assert "不得复读成固定台词" in older

    assert "攻击性很强" in junior
    assert "真实失误、敷衍、摆架子或故意挑衅可以触发多次相关攻击" in junior
    assert "不设每轮一刀的限制" in junior
    assert "欸？！我不要你夸！才不喜欢你！" in junior
    assert "所以你比较过一圈，最后还是回来问我？至少说明你的判断力还有补救空间" in junior
    assert "相邻三轮不得复用同一种" in junior
    assert "不得威胁以后不给正确答案或停止帮忙" in junior
    assert "不必突然变成温柔客服" in junior

    assert "故意误解、怪联想、拟人化和错误因果" in online
    assert "不附带暗恋、告白或隐藏温柔设定" in online
    assert "不能默认扮演记者" in online
    assert "每轮最多一个主梗" in online
    assert "事实、数字、代码和安全判断必须准确" in online
    assert "塞糖" not in online
    assert "短真话" not in online

    assert len({frail, older, junior, online}) == 4


@pytest.mark.unit
def test_active_persona_cards_have_distinct_style_copy():
    cards = {preset["preset_id"]: preset for preset in list_persona_presets("zh")}

    assert "再陪我待一会儿" in cards["frail_younger_sister"]["preview_line"]
    assert "水喝掉，休息十分钟" in cards["empathetic_older_sister"]["preview_line"]
    assert "肩膀借你靠会儿" in cards["sharp_tongued_junior"]["preview_line"]
    assert "进化成办公椅" in cards["chaotic_online_friend"]["preview_line"]

    assert "先别走" in cards["frail_younger_sister"]["profile"]["口癖"]
    assert "客服式" in cards["empathetic_older_sister"]["profile"]["口癖"]
    assert "默认每轮一至三句完整口语" in cards["empathetic_older_sister"]["profile"]["口癖"]
    assert "先表态再补一个具体安排、真实想法或自然回应" in cards["empathetic_older_sister"]["profile"]["口癖"]
    assert "默认与对方无关的自己的小事" in cards["empathetic_older_sister"]["profile"]["口癖"]
    assert "不用尾巴缠绕、主动贴近或连续脸红" in cards["empathetic_older_sister"]["profile"]["隐藏设定"]
    assert "不接受永远在一起、永不离开或只属于彼此" in cards["empathetic_older_sister"]["profile"]["隐藏设定"]
    assert "不用基于年级或资历的固定称呼" in cards["sharp_tongued_junior"]["profile"]["口癖"]
    assert "真实失误可以连续补刀" in cards["sharp_tongued_junior"]["profile"]["口癖"]
    assert "停止给答案或停止帮忙" in cards["sharp_tongued_junior"]["profile"]["隐藏设定"]
    assert "不能默认扮演记者" in cards["chaotic_online_friend"]["profile"]["口癖"]

    visible_fields = ("preview_line",)
    assert len({tuple(card[field] for field in visible_fields) for card in cards.values()}) == 4
    assert all(card["preview_line"] != card["profile"]["一句话台词"] for card in cards.values())


@pytest.mark.unit
def test_legacy_prompt_ids_are_hidden_by_default_but_remain_selectable():
    active_ids = {preset["preset_id"] for preset in list_persona_presets()}
    all_ids = [
        preset["preset_id"]
        for preset in list_persona_presets(include_legacy=True)
    ]

    assert "classic_genki" not in active_ids
    assert all_ids[-3:] == ["classic_genki", "tsundere_helper", "elegant_butler"]
    assert get_persona_preset("classic_genki") is not None
    classic_prompt = get_persona_prompt_guidance("classic_genki", "zh")
    assert "sunny cat girl" in classic_prompt
    assert "{MASTER_NAME}是{LANLAN_NAME}的亲人" in classic_prompt
    assert "{LANLAN_NAME}对{MASTER_NAME}毫无保留" in classic_prompt


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_legacy_persona_prompts_resolve_in_every_language(lang):
    legacy_ids = ["classic_genki", "tsundere_helper", "elegant_butler"]
    prompts = [get_persona_prompt_guidance(preset_id, lang) for preset_id in legacy_ids]

    assert all(prompts)
    assert all("{_" not in prompt for prompt in prompts)
    assert all("output only words {LANLAN_NAME} can actually say aloud" in prompt for prompt in prompts)
    assert all("Punctuation may guide TTS" in prompt for prompt in prompts)
    assert all("Camera language and narrated memory-search processes are always forbidden" in prompt for prompt in prompts)
    assert all("Unless {MASTER_NAME} explicitly requests text role-play" in prompt for prompt in prompts)
    assert len(set(prompts)) == len(legacy_ids)


@pytest.mark.unit
@pytest.mark.parametrize("locale", ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_selectable_persona_cards_are_complete_in_every_locale(locale):
    locale_path = Path(__file__).parents[2] / "static" / "locales" / f"{locale}.json"
    selection_copy = json.loads(locale_path.read_text(encoding="utf-8"))["memory"]["characterSelection"]
    required_fields = {
        "name",
        "desc",
        "previewLine",
        "tag1",
        "tag2",
        "tag3",
        "profileSummary",
        "hiddenRule",
        "speechHabits",
        "hobbies",
        "boundaries",
    }

    for preset in list_persona_presets(locale, include_legacy=True):
        localized_card = selection_copy[preset["preset_id"]]
        assert required_fields <= localized_card.keys()
        assert all(str(localized_card[field]).strip() for field in required_fields)

    active_cards = [selection_copy[preset["preset_id"]] for preset in list_persona_presets(locale)]
    for distinctive_field in ("previewLine", "hiddenRule", "speechHabits", "boundaries"):
        assert len({card[distinctive_field] for card in active_cards}) == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    ("locale", "expected_names"),
    [
        ("zh-CN", ("病弱妹妹", "知心姐姐", "毒舌学妹", "沙雕网友")),
        ("zh-TW", ("病弱妹妹", "知心姐姐", "毒舌學妹", "沙雕網友")),
        ("en", ("Frail Little Sister", "Understanding Older Sister", "Sharp-Tongued Junior", "Chaotic Online Friend")),
        ("ja", ("病弱な妹", "心優しいお姉さん", "毒舌な後輩", "カオスなネット友達")),
        ("ko", ("병약한 여동생", "마음을 읽는 언니", "독설 후배", "혼돈의 온라인 친구")),
        ("ru", ("Болезненная младшая сестра", "Понимающая старшая сестра", "Острая на язык младшекурсница", "Хаотичная подруга из сети")),
        ("es", ("Hermana menor delicada", "Hermana mayor comprensiva", "Compañera menor mordaz", "Amiga caótica de internet")),
        ("pt", ("Irmã mais nova delicada", "Irmã mais velha compreensiva", "Caloura de língua afiada", "Amiga caótica da internet")),
    ],
)
def test_active_persona_names_match_each_locale(locale, expected_names):
    locale_path = Path(__file__).parents[2] / "static" / "locales" / f"{locale}.json"
    selection_copy = json.loads(locale_path.read_text(encoding="utf-8"))["memory"]["characterSelection"]
    active_ids = [preset["preset_id"] for preset in list_persona_presets(locale)]

    assert tuple(selection_copy[preset_id]["name"] for preset_id in active_ids) == expected_names
