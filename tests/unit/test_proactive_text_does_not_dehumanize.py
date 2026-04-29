"""主动搭话相关 prompt 文案的反 AI 物化称呼护栏。

PR #1041 修复了 PROACTIVE_ACTION_NOTE_* 和 _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES
的物化称呼字面量。本文件覆盖剩下三块还在 prompts_proactive.py 里的本地化文案：

1. ``_P2_MEME_INSTRUCTION``：表情包行为指令，由 ``get_proactive_generate_prompt``
   注入 Phase 2 system prompt。
2. ``SCREEN_SECTION_HEADER`` / ``SCREEN_IMG_HINT``：vision 通道的屏幕区块标题
   和截图说明，由 ``get_screen_section_header`` / ``get_screen_img_hint`` 输出。
3. ``PROACTIVE_MUSIC_PLAYING_HINT`` / ``PROACTIVE_MUSIC_FAILSAFE_HINTS``：放歌时的
   行为约束 + 模糊匹配兜底，由 ``get_proactive_music_playing_hint`` /
   ``get_proactive_music_failsafe_hint`` 输出。

每个 helper 都过一遍：
- 实名传入（``MASTER = "小明"``）→ 名字应原样出现，且禁词字面量都不在结果里。
- 空名传入 → 应回退到 PROACTIVE_ACTION_NOTE_PLACEHOLDERS 的本地化中性词
  ("对方" / "them" / "相手" / "상대" / "собеседника")，禁词仍不在结果里。

禁词列表覆盖五种语言的常见物化变体——这是项目核心价值观，下次有人想再悄悄
把"主人"加回模板会被这层挡住。
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config.prompts_proactive import (
    PROACTIVE_ACTION_NOTE_PLACEHOLDERS,
    get_proactive_generate_prompt,
    get_proactive_music_failsafe_hint,
    get_proactive_music_playing_hint,
    get_screen_img_hint,
    get_screen_section_header,
)

LOCALES = ('zh', 'en', 'ja', 'ko', 'ru')
MASTER = "小明"

# master 的物化变体（含 #1041 PROACTIVE_ACTION_NOTE 测试同款，再加上"Master"
# 大写——SCREEN_SECTION_HEADER en 旧文案是 "Master's Screen"，要专门把这种
# 句首大写挡住）
FORBIDDEN_TERMS = ('主人', 'master', 'Master', 'ご主人', '주인', 'хозяин', 'Хозяин')

# 剥掉 ``{xxx}`` 形式的未展开占位符，避免诸如 ``{master_name}`` / ``{MASTER_NAME}``
# 这种合法的 Python format placeholder 名字误命中 "master" 禁词。仅匹配安全字符
# （字母/数字/下划线），保留模板里的实际文案不动。
_PLACEHOLDER_RE = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')


def _assert_no_forbidden(text: str, *, ctx: str) -> None:
    stripped = _PLACEHOLDER_RE.sub('', text)
    for word in FORBIDDEN_TERMS:
        assert word not in stripped, f"{ctx}: 含物化称呼 '{word}' → {text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 屏幕区块（vision 通道）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lang', LOCALES)
def test_screen_section_header_expands_master_name(lang: str) -> None:
    out = get_screen_section_header(MASTER, lang)
    assert MASTER in out, f"lang={lang} 实名未注入: {out!r}"
    _assert_no_forbidden(out, ctx=f"screen_section_header lang={lang}")


@pytest.mark.parametrize('lang', LOCALES)
def test_screen_section_header_falls_back_when_master_empty(lang: str) -> None:
    fallback = PROACTIVE_ACTION_NOTE_PLACEHOLDERS[lang]['master']
    for empty in ('', None, '   '):
        out = get_screen_section_header(empty, lang)
        assert fallback in out, f"lang={lang} 兜底 {fallback!r} 未注入: {out!r}"
        _assert_no_forbidden(out, ctx=f"screen_section_header lang={lang} empty={empty!r}")


@pytest.mark.parametrize('lang', LOCALES)
def test_screen_img_hint_expands_master_name(lang: str) -> None:
    out = get_screen_img_hint(MASTER, lang)
    assert MASTER in out, f"lang={lang} 实名未注入: {out!r}"
    _assert_no_forbidden(out, ctx=f"screen_img_hint lang={lang}")


@pytest.mark.parametrize('lang', LOCALES)
def test_screen_img_hint_falls_back_when_master_empty(lang: str) -> None:
    fallback = PROACTIVE_ACTION_NOTE_PLACEHOLDERS[lang]['master']
    out = get_screen_img_hint('', lang)
    assert fallback in out, f"lang={lang} 兜底 {fallback!r} 未注入: {out!r}"
    _assert_no_forbidden(out, ctx=f"screen_img_hint lang={lang} empty")


# ─────────────────────────────────────────────────────────────────────────────
# 音乐相关（放歌中 + 模糊匹配兜底）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_no_dehumanize_with_master(lang: str) -> None:
    # track_name 含 {} 字面量，验证 helper 的转义路径仍然工作。
    out = get_proactive_music_playing_hint('Bohemian {Rhapsody}', MASTER, lang)
    _assert_no_forbidden(out, ctx=f"music_playing_hint lang={lang}")
    # zh 模板含 {master}，应注入实名；其它 locale 模板无 {master}，不强求。
    if lang == 'zh':
        assert MASTER in out


@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_no_dehumanize_with_empty_master(lang: str) -> None:
    out = get_proactive_music_playing_hint('Some Track', '', lang)
    _assert_no_forbidden(out, ctx=f"music_playing_hint lang={lang} empty")


@pytest.mark.parametrize('lang', LOCALES)
def test_music_failsafe_hint_expands_master_name(lang: str) -> None:
    out = get_proactive_music_failsafe_hint(MASTER, lang)
    assert MASTER in out, f"lang={lang} 实名未注入: {out!r}"
    _assert_no_forbidden(out, ctx=f"music_failsafe lang={lang}")


@pytest.mark.parametrize('lang', LOCALES)
def test_music_failsafe_hint_falls_back_when_master_empty(lang: str) -> None:
    fallback = PROACTIVE_ACTION_NOTE_PLACEHOLDERS[lang]['master']
    out = get_proactive_music_failsafe_hint(None, lang)
    assert fallback in out
    _assert_no_forbidden(out, ctx=f"music_failsafe lang={lang} empty")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 generate prompt（表情包行为指令注入）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lang', LOCALES)
def test_meme_instruction_expanded_inside_generate_prompt(lang: str) -> None:
    """当 has_meme=True，generate_prompt 必须把 _P2_MEME_INSTRUCTION 里的 {master}
    占位符在返回前展开，避免外层 .format(master_name=...) 因不知道 master 报 KeyError。
    """
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint='', has_music=False, has_meme=True, master_name=MASTER,
    )
    # 必须没有未展开的 {master}
    assert '{master}' not in prompt, f"lang={lang} 残留未展开占位符: {prompt!r}"
    # 必须把名字注入了
    assert MASTER in prompt, f"lang={lang} 名字未出现"
    _assert_no_forbidden(prompt, ctx=f"generate_prompt lang={lang} has_meme=True")


@pytest.mark.parametrize('lang', LOCALES)
def test_meme_instruction_falls_back_when_master_empty(lang: str) -> None:
    fallback = PROACTIVE_ACTION_NOTE_PLACEHOLDERS[lang]['master']
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint='', has_music=False, has_meme=True, master_name='',
    )
    assert '{master}' not in prompt
    assert fallback in prompt, f"lang={lang} 兜底未注入"
    _assert_no_forbidden(prompt, ctx=f"generate_prompt lang={lang} empty has_meme=True")


def test_generate_prompt_does_not_dehumanize_when_no_meme() -> None:
    """has_meme=False 路径里 _P2_MEME_INSTRUCTION 不会被注入，但 generate prompt 里
    其它本地化文本也不应混进物化称呼。"""
    for lang in LOCALES:
        prompt = get_proactive_generate_prompt(
            lang, music_playing_hint='', has_music=False, has_meme=False, master_name=MASTER,
        )
        _assert_no_forbidden(prompt, ctx=f"generate_prompt lang={lang} has_meme=False")


# ─────────────────────────────────────────────────────────────────────────────
# 大括号转义 —— 防御 master_name 含 ``{`` / ``}`` 的边界 case
#
# generate_prompt 和 music_playing_hint 走的是"helper 内先 .format(master=...)、
# 拼回 prompt 后 system_router 再整体 .format()"的双层路径。helper 第一次 format
# 时把 master_name 字面量原样注入，**不会** escape 它含的花括号；外层第二次
# .format() 看到 ``{B}`` 这种残留就会 KeyError，proactive 直接 abort。Codex
# review #1043 (r3164599879 / r3164599885) 抓的就是这两条 P1。
# ─────────────────────────────────────────────────────────────────────────────

WEIRD_MASTER = 'A{B}'  # 含双括号的"用户起的怪名字"


def _simulate_outer_format(generate_prompt: str, music_playing_hint: str = '') -> str:
    """模拟 system_router.py L4485 的整体 .format(...)，验证不会 KeyError。"""
    return generate_prompt.format(
        character_prompt='<char>',
        inner_thoughts='<inner>',
        state_section='<state>',
        memory_context='<mem>',
        recent_chats_section='<recent>',
        screen_section='<screen>',
        external_section='<ext>',
        music_section='<music>',
        meme_section='<meme>',
        master_name=WEIRD_MASTER,
        source_instruction='<src>',
        output_format_section='<out>',
    )


@pytest.mark.parametrize('lang', LOCALES)
def test_generate_prompt_with_braced_master_survives_outer_format(lang: str) -> None:
    """master_name='A{B}' 时 helper 必须把 master 值的花括号 escape，外层 .format
    才不会把残留 ``{B}`` 当占位符 KeyError。修完后最终字符串里应该有字面量 ``A{B}``。"""
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint='', has_music=False, has_meme=True,
        master_name=WEIRD_MASTER,
    )
    final = _simulate_outer_format(prompt)
    assert WEIRD_MASTER in final, f"lang={lang} 字面量 {WEIRD_MASTER!r} 丢失：{final!r}"


@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_with_braced_master_survives_outer_format(lang: str) -> None:
    """zh 模板含 {master} 占位符，master_name='A{B}' 时 helper 要 escape；
    其它 locale 模板没 {master}，但传同样的 master_name 也不应 raise。
    music_playing_hint 在 system_router 里被拼回 generate_prompt 内层、走整体 .format()。"""
    hint = get_proactive_music_playing_hint('Some Track', WEIRD_MASTER, lang)
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint=hint, has_music=False, has_meme=False,
        master_name=WEIRD_MASTER,
    )
    final = _simulate_outer_format(prompt)
    if lang == 'zh':
        assert WEIRD_MASTER in final, f"lang={lang} hint 内字面量 {WEIRD_MASTER!r} 丢失"


@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_with_braced_track_name_survives_outer_format(lang: str) -> None:
    """歌名带 ``{`` / ``}``（如 'Bohemian {Rhapsody}'）时 helper 也要 escape，
    否则同样会让外层 .format() KeyError。这是 master 修法的对照回归。"""
    weird_track = 'Bohemian {Rhapsody}'
    hint = get_proactive_music_playing_hint(weird_track, MASTER, lang)
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint=hint, has_music=False, has_meme=False, master_name=MASTER,
    )
    final = _simulate_outer_format(prompt)
    assert weird_track in final, f"lang={lang} 歌名 {weird_track!r} 丢失：{final!r}"
