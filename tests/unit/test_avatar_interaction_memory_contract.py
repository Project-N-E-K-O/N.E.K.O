import pytest

from config.prompts_avatar_interaction import (
    _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES,
    _build_avatar_interaction_memory_meta,
)
from main_logic.cross_server import _should_persist_avatar_interaction_memory


# 测试公用 master_name —— 任意字符串即可，关键是验证它会被原样展开进 memory_note，
# 而不是回落到"主人"等物化称呼。
MASTER = "小明"


@pytest.mark.unit
def test_avatar_interaction_memory_meta_promotes_fist_and_hammer_summaries():
    fist_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
    }, MASTER)
    fist_rapid = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "rapid",
    }, MASTER)
    hammer_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "normal",
    }, MASTER)
    hammer_burst = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "burst",
    }, MASTER)

    assert fist_normal["memory_note"] == f"[{MASTER}摸了摸你的头]"
    assert fist_rapid["memory_note"] == f"[{MASTER}连续摸了摸你的头]"
    assert fist_rapid["memory_dedupe_rank"] > fist_normal["memory_dedupe_rank"]
    assert fist_rapid["memory_dedupe_key"] == fist_normal["memory_dedupe_key"] == "fist_touch"

    assert hammer_normal["memory_note"] == f"[{MASTER}用锤子敲了敲你的头]"
    assert hammer_burst["memory_note"] == f"[{MASTER}连续敲了你好几下]"
    assert hammer_burst["memory_dedupe_rank"] > hammer_normal["memory_dedupe_rank"]
    assert hammer_burst["memory_dedupe_key"] == hammer_normal["memory_dedupe_key"] == "hammer_bonk"


@pytest.mark.unit
def test_avatar_interaction_memory_window_allows_rank_upgrade_within_window():
    cache: dict[str, dict[str, int | str]] = {}

    first_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}摸了摸你的头]",
        "fist_touch",
        1,
    )
    upgraded_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}连续摸了摸你的头]",
        "fist_touch",
        2,
    )
    duplicate_summary_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}连续摸了摸你的头]",
        "fist_touch",
        2,
    )

    assert first_persisted is True
    assert upgraded_persisted is True
    assert duplicate_summary_persisted is False


# ─────────────────────────────────────────────────────────────────────────────
# 反 AI 物化护栏：禁止"主人 / Your master / ご主人さま / 주인 / Хозяин"
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_avatar_memory_templates_have_no_dehumanizing_literals():
    """模板字面量层面：所有语言的所有 (tool, action) 模板都不能含物化称呼。

    这是项目核心价值观的护栏——以后再有人加新语言/新 action 模板，手滑写进
    "主人"等附属称呼会被这条直接 fail 拦下。注意是检查模板原文（含 {master}
    占位符），不是格式化后的输出。
    """
    forbidden = [
        "主人",
        "Your master",
        "your master",
        "Master",
        "master",  # 跟 {master} 占位符以外的文字共存时 substring 也会命中
        "ご主人",
        "주인",
        "хозяин",
        "Хозяин",
    ]
    for locale, by_tool in _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.items():
        for tool_id, by_action in by_tool.items():
            for action_id, template in by_action.items():
                # 把 {master} 占位符抠掉，再检查残留文本是否含禁词
                stripped = template.replace("{master}", "")
                for word in forbidden:
                    assert word not in stripped, (
                        f"locale={locale} tool={tool_id} action={action_id} "
                        f"template={template!r} 含物化称呼 '{word}'"
                    )


@pytest.mark.unit
def test_avatar_memory_meta_expands_master_name_literally():
    """build 出来的 memory_note 应该包含传入的 master_name，且不含物化称呼。"""
    forbidden = ["主人", "Your master", "your master", "ご主人", "주인", "Хозяин", "хозяин"]
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for locale in ("zh", "en", "ja", "ko", "ru", "zh-TW"):
        meta = _build_avatar_interaction_memory_meta(locale, payload, MASTER)
        note = meta["memory_note"]
        assert MASTER in note, f"locale={locale} note={note!r} 没展开 master_name"
        for word in forbidden:
            assert word not in note, f"locale={locale} note={note!r} 含物化称呼 '{word}'"


@pytest.mark.unit
def test_avatar_memory_meta_empty_master_falls_back_to_neutral_word():
    """master_name 传空时按本地化中性词兜底（"对方 / they / 相手 / 상대 /
    собеседник"），同样不会回落到物化称呼。"""
    forbidden = ["主人", "Your master", "your master", "ご主人", "주인", "Хозяин", "хозяин"]
    expected_neutral = {
        "zh": "对方",
        "zh-TW": "對方",
        "en": "they",
        "ja": "相手",
        "ko": "상대",
        "ru": "собеседник",
    }
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for locale, neutral in expected_neutral.items():
        meta = _build_avatar_interaction_memory_meta(locale, payload, "")
        note = meta["memory_note"]
        assert neutral in note, f"locale={locale} note={note!r} 没用中性词回退"
        for word in forbidden:
            assert word not in note, f"locale={locale} note={note!r} 兜底回落到物化称呼 '{word}'"


@pytest.mark.unit
def test_avatar_memory_meta_master_name_passes_through_unchanged():
    """各种 master_name 输入都应原样展开（中文、英文、emoji、含空格名字等）。"""
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for name in ("小明", "Alice", "mochi", "猫猫", "Mei Wang"):
        meta = _build_avatar_interaction_memory_meta("zh", payload, name)
        assert name in meta["memory_note"]
