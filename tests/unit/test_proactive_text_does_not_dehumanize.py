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
