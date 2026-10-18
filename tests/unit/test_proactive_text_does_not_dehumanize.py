"""Guard proactive-chat prompts against objectifying forms of address for AI.

PR #1041 fixed objectifying literals in PROACTIVE_ACTION_NOTE_* and _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.
This covers three remaining localized copy groups in prompts_proactive.py:

1. ``_P2_MEME_INSTRUCTION``: meme-action instructions injected by ``get_proactive_generate_prompt``
   into Phase 2 system prompts.
2. ``SCREEN_SECTION_HEADER`` / ``SCREEN_IMG_HINT``: vision-channel screen headings
   and screenshot descriptions from ``get_screen_section_header`` / ``get_screen_img_hint``.
3. ``PROACTIVE_MUSIC_PLAYING_HINT`` / ``PROACTIVE_MUSIC_FAILSAFE_HINTS``: playback
   constraints + fuzzy-match fallbacks from ``get_proactive_music_playing_hint`` /
   ``get_proactive_music_failsafe_hint``.

Exercise every helper:
- Real name (``MASTER = \"小明\"``) → retain the exact name with no prohibited literals.
- Empty name → use localized neutral terms from PROACTIVE_ACTION_NOTE_PLACEHOLDERS
  (\"对方\" / \"them\" / \"相手\" / \"상대\" / \"собеседника\"), still without prohibited words.

The prohibited list covers common objectifying variants in five languages, a core project value;
this guard prevents silently reintroducing 'master' into templates.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config.prompts.prompts_proactive import (
    PROACTIVE_ACTION_NOTE_PLACEHOLDERS,
    get_proactive_generate_prompt,
    get_proactive_music_failsafe_hint,
    get_proactive_music_playing_hint,
    get_screen_img_hint,
    get_screen_section_header,
)

LOCALES = ('zh', 'en', 'ja', 'ko', 'ru')
MASTER = "小明"

# Objectifying master variants, including #1041 PROACTIVE_ACTION_NOTE cases plus capitalized "Master";
# SCREEN_SECTION_HEADER's old English copy was "Master's Screen", requiring explicit coverage of this
# sentence-initial capitalization
FORBIDDEN_TERMS = ('主人', 'master', 'Master', 'ご主人', '주인', 'хозяин', 'Хозяин')

# Strip unexpanded ``{xxx}`` placeholders so valid Python format names like ``{master_name}`` / ``{MASTER_NAME}``
# do not falsely match prohibited "master". Match only safe characters
# (letters/digits/underscores), leaving actual template copy unchanged.
_PLACEHOLDER_RE = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')


def _assert_no_forbidden(text: str, *, ctx: str) -> None:
    stripped = _PLACEHOLDER_RE.sub('', text)
    for word in FORBIDDEN_TERMS:
        assert word not in stripped, f"{ctx}: 含物化称呼 '{word}' → {text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Screen section in the vision channel
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
# Music-related copy: playback + fuzzy-match fallback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_no_dehumanize_with_master(lang: str) -> None:
    # track_name contains literal {}, verifying the helper's escaping path still works.
    out = get_proactive_music_playing_hint('Bohemian {Rhapsody}', MASTER, lang)
    _assert_no_forbidden(out, ctx=f"music_playing_hint lang={lang}")
    # Chinese templates contain {master} and must inject the real name; other locales lack it and need not inject.
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
# Phase 2 generate prompt with injected meme-action instructions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lang', LOCALES)
def test_meme_instruction_expanded_inside_generate_prompt(lang: str) -> None:
    """With has_meme=True, generate_prompt must expand _P2_MEME_INSTRUCTION's {master}
    placeholder before returning, avoiding KeyError when outer .format(master_name=...) lacks master.
    """
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint='', has_music=False, has_meme=True, master_name=MASTER,
    )
    # No unexpanded {master} may remain
    assert '{master}' not in prompt, f"lang={lang} 残留未展开占位符: {prompt!r}"
    # The name must be injected
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
    """With has_meme=False, _P2_MEME_INSTRUCTION is not injected, but other localized generate-prompt
    copy must still avoid objectifying address."""
    for lang in LOCALES:
        prompt = get_proactive_generate_prompt(
            lang, music_playing_hint='', has_music=False, has_meme=False, master_name=MASTER,
        )
        _assert_no_forbidden(prompt, ctx=f"generate_prompt lang={lang} has_meme=False")


# ─────────────────────────────────────────────────────────────────────────────
# Brace escaping guards master_name containing literal ``{`` / ``}``
#
# generate_prompt and music_playing_hint use two formatting layers: helper .format(master=...),
# then system_router .format() over the reassembled whole prompt. The helper's first formatting
# injects literal master_name without automatically escaping braces; the outer second
# .format() sees leftovers such as ``{B}``, raises KeyError, and aborts proactive chat. Codex
# review #1043 (r3164599879 / r3164599885) caught these two P1 issues.
# ─────────────────────────────────────────────────────────────────────────────

WEIRD_MASTER = 'A{B}'  # Unusual user-chosen name containing double braces


def _simulate_outer_format(generate_prompt: str, music_playing_hint: str = '') -> str:
    """Simulate system_router.py L4485's whole-prompt .format(...) and verify no KeyError."""
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
    """For master_name='A{B}', the helper must escape braces in master values so outer .format
    does not interpret leftover ``{B}`` as a placeholder. Final output must retain literal ``A{B}``."""
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint='', has_music=False, has_meme=True,
        master_name=WEIRD_MASTER,
    )
    final = _simulate_outer_format(prompt)
    assert WEIRD_MASTER in final, f"lang={lang} 字面量 {WEIRD_MASTER!r} 丢失：{final!r}"


@pytest.mark.parametrize('lang', LOCALES)
def test_music_playing_hint_with_braced_master_survives_outer_format(lang: str) -> None:
    """Chinese templates contain {master}, requiring escaping for master_name='A{B}';
    other locales lack {master}, but passing the same name still must not raise.
    system_router inserts music_playing_hint into generate_prompt before whole-prompt .format()."""
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
    """Track names containing ``{`` / ``}`` (such as 'Bohemian {Rhapsody}') also require helper escaping,
    or outer .format() raises KeyError too; this is the corresponding regression for the master-name fix."""
    weird_track = 'Bohemian {Rhapsody}'
    hint = get_proactive_music_playing_hint(weird_track, MASTER, lang)
    prompt = get_proactive_generate_prompt(
        lang, music_playing_hint=hint, has_music=False, has_meme=False, master_name=MASTER,
    )
    final = _simulate_outer_format(prompt)
    assert weird_track in final, f"lang={lang} 歌名 {weird_track!r} 丢失：{final!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Cat-specific greetings after returning from cat form or being invited back also obey anti-objectification guards.
# Match trigger_cat_greeting: old templates contain {reason_hint}/{elapsed}/{time_hint}/{master};
# episode scene templates additionally contain {cat_form_scene}. First format reason_hint
# with .format(master=...), then inject into the main template and format the whole again.
# ─────────────────────────────────────────────────────────────────────────────

from config.prompts.prompts_proactive import (  # noqa: E402
    _CAT_GREETING_EPISODE_PROMPTS,
    _CAT_GREETING_EPISODE_RETURN_TONES,
    _CAT_GREETING_EPISODE_SCENES,
    _CAT_GREETING_TABLES,
    get_cat_greeting_episode_prompt,
    get_cat_greeting_episode_scene,
    get_cat_greeting_prompt,
    get_cat_greeting_reason_hint,
)

# (behavior, duration_seconds) covers awake/dozing/asleep × short/long durations.
_CAT_GREETING_CASES = [
    ('awake', 300), ('awake', 1000),
    ('nap', 300), ('nap', 2000),
    ('sleep', 300), ('sleep', 2000),
]


def _format_cat_greeting(behavior: str, duration: int, was_auto: bool, lang: str, master: str) -> str:
    template = get_cat_greeting_prompt(behavior, duration, lang)
    assert template is not None, f"{behavior}/{duration}/{lang} 不应静默"
    reason = get_cat_greeting_reason_hint(was_auto, lang).format(master=master)
    return template.format(
        reason_hint=reason, elapsed='10 分钟', name='奈々', master=master,
        time_hint='(time hint)',
    )


@pytest.mark.parametrize('lang', LOCALES)
@pytest.mark.parametrize('behavior,duration', _CAT_GREETING_CASES)
@pytest.mark.parametrize('was_auto', [True, False])
def test_cat_greeting_no_dehumanize_and_expands_master(lang, behavior, duration, was_auto) -> None:
    out = _format_cat_greeting(behavior, duration, was_auto, lang, MASTER)
    assert '{' not in out and '}' not in out, f"lang={lang} 残留占位符: {out!r}"
    assert MASTER in out, f"lang={lang} 实名未注入: {out!r}"
    _assert_no_forbidden(out, ctx=f"cat_greeting {behavior}/{duration}/{lang}/auto={was_auto}")


def test_cat_greeting_silences_short_returns() -> None:
    """Production cat return keeps the established three-minute silence gate."""
    for lang in LOCALES:
        assert get_cat_greeting_prompt('awake', 179, lang) is None
        assert get_cat_greeting_prompt('nap', 0, lang) is None
        assert get_cat_greeting_prompt('awake', 180, lang) is not None


# 'zh-TW' joined the tables with the issue #2500 backfill. The module's
# normalizer still collapses Traditional to 'zh' (keep_traditional=False), so the
# new rows are not reachable through get_cat_greeting_* yet — see
# test_cat_greeting_episode_scene_rejects_invalid_combinations_and_uses_english_fallback,
# which pins that collapse. This tuple is the table-coverage list, not the
# resolver's input domain.
_CAT_EPISODE_LOCALES = ('zh', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt')
_CAT_EPISODE_CASES = [
    {'kind': 'activity'},
    {'kind': 'activity', 'highlight': 'played_yarn'},
    {'kind': 'activity', 'highlight': 'ate_snack'},
    {'kind': 'activity', 'highlight': 'small_move'},
    {'kind': 'activity', 'highlight': 'social_ping'},
    {'kind': 'rest_after_activity'},
    {'kind': 'rest_after_activity', 'highlight': 'played_yarn'},
    {'kind': 'rest_after_activity', 'highlight': 'ate_snack'},
    {'kind': 'rest_after_activity', 'highlight': 'small_move'},
    {'kind': 'rest_after_activity', 'highlight': 'social_ping'},
    {'kind': 'rested'},
]


def test_cat_greeting_episode_prompt_tables_cover_all_normalized_locales() -> None:
    expected_locales = set(_CAT_EPISODE_LOCALES)
    expected_behavior_bands = {
        ('awake', 'short'), ('awake', 'long'),
        ('nap', 'short'), ('nap', 'long'),
        ('sleep', 'short'), ('sleep', 'long'),
    }
    assert set(_CAT_GREETING_EPISODE_SCENES) == expected_locales
    assert set(_CAT_GREETING_EPISODE_PROMPTS) == expected_locales
    assert set(_CAT_GREETING_EPISODE_RETURN_TONES) == expected_locales
    for lang in expected_locales:
        assert set(_CAT_GREETING_EPISODE_SCENES[lang]) == {
            'activity', 'rest_after_activity', 'rested',
        }
        assert set(_CAT_GREETING_EPISODE_RETURN_TONES[lang]) == expected_behavior_bands


@pytest.mark.parametrize('lang', _CAT_EPISODE_LOCALES)
@pytest.mark.parametrize('episode', _CAT_EPISODE_CASES)
def test_cat_greeting_episode_scene_is_localized_and_contains_no_transport_detail(lang, episode) -> None:
    scene = get_cat_greeting_episode_scene(episode, lang)
    assert scene, f'lang={lang} episode={episode} missing localized scene'
    assert '{' not in scene and '}' not in scene, f'lang={lang} leaked format placeholder: {scene!r}'
    _assert_no_forbidden(scene, ctx=f'cat_episode_scene lang={lang} episode={episode}')
    for forbidden_transport in (
        'cat1_', 'cat2_', 'cat3_', 'actionId', 'requestId', 'runId',
        'appetite', 'sleepiness', 'energy', 'social_need', 'stimulation_need',
    ):
        assert forbidden_transport not in scene, f'lang={lang} leaked {forbidden_transport}: {scene!r}'


def test_cat_greeting_episode_scene_rejects_invalid_combinations_and_uses_english_fallback() -> None:
    for episode in (
        None,
        [],
        {'kind': 'unknown'},
        {'kind': 'rested', 'highlight': 'played_yarn'},
        {'kind': 'activity', 'highlight': 'raw text'},
        {'kind': 'activity', 'highlight': None},
    ):
        assert get_cat_greeting_episode_scene(episode, 'zh') == ''

    english = get_cat_greeting_episode_scene({'kind': 'rested'}, 'en')
    assert get_cat_greeting_episode_scene({'kind': 'rested'}, 'fr-FR') == english
    zh = get_cat_greeting_episode_scene({'kind': 'rested'}, 'zh')
    assert get_cat_greeting_episode_scene({'kind': 'rested'}, 'zh-CN') == zh
    # Since #2500 C2, traditional Chinese uses its own entry instead of collapsing to simplified Chinese.
    traditional = get_cat_greeting_episode_scene({'kind': 'rested'}, 'zh-TW')
    # Vacuous-assertion trap: unknown kinds return '', satisfying both '' != zh and '' == '',
    # giving traditional-Chinese users empty copy while tests pass. Assert nonempty first.
    assert traditional, 'zh-TW 缺少 rested 场景行，繁中会拿到空文案'
    assert traditional != zh
    assert get_cat_greeting_episode_scene({'kind': 'rested'}, 'zh-Hant') == traditional


def test_cat_greeting_episode_scene_is_not_labeled_as_optional_background() -> None:
    assert '补充经历' not in get_cat_greeting_episode_scene(
        {'kind': 'activity', 'highlight': 'played_yarn'}, 'zh',
    )
    assert 'Additional episode background' not in get_cat_greeting_episode_scene(
        {'kind': 'activity', 'highlight': 'played_yarn'}, 'en',
    )


def test_cat_greeting_episode_social_ping_does_not_claim_a_count() -> None:
    for episode in (
        {'kind': 'activity', 'highlight': 'social_ping'},
        {'kind': 'rest_after_activity', 'highlight': 'social_ping'},
    ):
        assert '几声' not in get_cat_greeting_episode_scene(episode, 'zh')


@pytest.mark.parametrize(
    ('episode', 'included', 'excluded'),
    [
        ({'kind': 'activity', 'highlight': 'played_yarn'}, 'playing with yarn', 'quiet rest'),
        ({'kind': 'activity'}, 'moving about', 'quiet rest'),
        ({'kind': 'rest_after_activity', 'highlight': 'ate_snack'}, 'had a small snack', ''),
        ({'kind': 'rested'}, 'quiet rest', 'moving about'),
    ],
)
def test_cat_greeting_episode_scene_distinguishes_only_the_last_bounded_chapter(
    episode, included, excluded,
) -> None:
    scene = get_cat_greeting_episode_scene(episode, 'en')
    assert included in scene
    if excluded:
        assert excluded not in scene
    if episode['kind'] == 'rest_after_activity':
        assert scene.index('had a small snack') < scene.index('quiet rest')


def test_cat_greeting_episode_rest_scenes_do_not_claim_duration_or_deep_sleep() -> None:
    for lang in _CAT_EPISODE_LOCALES:
        for episode in (
            {'kind': 'rested'},
            {'kind': 'rest_after_activity', 'highlight': 'played_yarn'},
        ):
            scene = get_cat_greeting_episode_scene(episode, lang)
            assert not any(char.isdigit() for char in scene), f'lang={lang} duration leaked: {scene!r}'
    english = get_cat_greeting_episode_scene(
        {'kind': 'rest_after_activity', 'highlight': 'played_yarn'}, 'en',
    )
    assert english.index('played with yarn') < english.index('quiet rest')
    assert 'deep' not in english.lower()


@pytest.mark.parametrize('lang', _CAT_EPISODE_LOCALES)
@pytest.mark.parametrize('behavior,duration', _CAT_GREETING_CASES)
def test_cat_greeting_episode_scene_formats_inside_dedicated_prompt(lang, behavior, duration) -> None:
    episode = {'kind': 'rest_after_activity', 'highlight': 'played_yarn'}
    template = get_cat_greeting_episode_prompt(behavior, duration, lang)
    assert template is not None
    scene = get_cat_greeting_episode_scene(episode, lang)
    rendered = template.format(
        reason_hint=get_cat_greeting_reason_hint(False, lang).format(master=MASTER),
        elapsed='10 分钟',
        name='奈々',
        master=MASTER,
        time_hint='(time hint)',
        cat_form_scene=scene,
    )
    assert scene in rendered
    assert '{' not in rendered and '}' not in rendered
    _assert_no_forbidden(
        rendered,
        ctx=f'cat_episode_prompt {behavior}/{duration}/{lang}',
    )


def test_cat_greeting_episode_prompt_does_not_keep_a_conflicting_sleep_fact() -> None:
    episode = {'kind': 'activity', 'highlight': 'social_ping'}
    template = get_cat_greeting_episode_prompt('nap', 300, 'en')
    assert template is not None
    rendered = template.format(
        reason_hint=get_cat_greeting_reason_hint(False, 'en').format(master=MASTER),
        elapsed='5 minutes',
        name='奈々',
        master=MASTER,
        time_hint='(time hint)',
        cat_form_scene=get_cat_greeting_episode_scene(episode, 'en'),
    )
    assert 'You gave a soft little response as a cat.' in rendered
    assert 'dozed for 5 minutes' not in rendered


_CAT_SHORT_RETURN_INPUT_LOCALES = ('zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt')
_CAT_GREETING_END_MARKER = '======以上为环境提示======'


def test_cat_greeting_source_templates_embed_chinese_environment_end_marker() -> None:
    tables = (
        *_CAT_GREETING_TABLES.values(),
        _CAT_GREETING_EPISODE_PROMPTS,
    )
    for table in tables:
        assert set(table) == set(_CAT_EPISODE_LOCALES)
        for prompt in table.values():
            marker_lines = [
                line for line in prompt.splitlines() if line.startswith('======')
            ]
            assert len(marker_lines) == 2
            assert marker_lines[-1] == _CAT_GREETING_END_MARKER
            assert prompt.endswith(_CAT_GREETING_END_MARKER)
            assert prompt.count(_CAT_GREETING_END_MARKER) == 1


@pytest.mark.parametrize('lang', _CAT_SHORT_RETURN_INPUT_LOCALES)
def test_cat_greeting_all_prompt_paths_preserve_embedded_environment_end_marker(lang) -> None:
    prompts = (
        get_cat_greeting_prompt('awake', 300, lang),
        get_cat_greeting_episode_prompt('nap', 300, lang),
    )
    for prompt in prompts:
        assert prompt is not None
        marker_lines = [line for line in prompt.splitlines() if line.startswith('======')]
        assert len(marker_lines) == 2
        assert marker_lines[-1] == _CAT_GREETING_END_MARKER
        assert prompt.endswith(_CAT_GREETING_END_MARKER)
        assert prompt.count(_CAT_GREETING_END_MARKER) == 1
        assert '猫形态返回系统提示' not in prompt


@pytest.mark.parametrize('lang', _CAT_SHORT_RETURN_INPUT_LOCALES)
@pytest.mark.parametrize('behavior', ('awake', 'nap', 'sleep'))
def test_cat_greeting_episode_prompt_cannot_bypass_minimum_dwell_time(lang, behavior) -> None:
    template = get_cat_greeting_episode_prompt(behavior, 10, lang)
    assert template is None


def test_cat_greeting_episode_prompt_keeps_unified_silence_boundary() -> None:
    assert get_cat_greeting_episode_prompt('awake', 179, 'en') is None
    assert get_cat_greeting_episode_prompt('awake', 180, 'en') is not None
