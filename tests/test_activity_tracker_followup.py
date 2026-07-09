"""Tests for the activity tracker follow-up bundle.

Covers the privacy / own-app / user-override / config-externalization /
tone-modifier features that landed on top of PR #1015's base tracker.
``#1`` (game intensity × genre schema) is exercised by the override
tests since the schema accepts both 2-tuple (legacy) and 4-tuple
(new) game keyword rows.

Each test constructs the state machine with explicit ``ActivityPreferences``
to avoid touching the real user_preferences.json, and feeds a fabricated
``SystemSnapshot`` to drive classification — no I/O, no real polling.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from main_logic.activity.snapshot import (
    ActivitySnapshot,
    UnfinishedThread,
    derive_propensity,
    derive_skip_probability,
    derive_tone,
    format_activity_state_section,
)
from main_logic.activity.state_machine import (
    ActivityStateMachine,
    observation_from_system,
)
from main_logic.activity.system_signals import SystemSnapshot
from utils.activity_config import (
    ActivityPreferences,
    _AppOverride,
    _cache,
    _GameOverride,
)


def _sys_snap(
    *,
    title: str | None = None,
    process: str | None = None,
    idle: float = 1.0,
    cpu: float = 10.0,
    gpu: float | None = None,
    ts: float | None = None,
) -> SystemSnapshot:
    """Tiny SystemSnapshot factory for test brevity."""
    return SystemSnapshot(
        timestamp=ts if ts is not None else time.time(),
        idle_seconds=idle,
        cpu_avg_30s=cpu,
        cpu_instant=cpu,
        window_title=title,
        process_name=process,
        gpu_utilization=gpu,
        os_signals_available=True,
    )


# ── #3 Privacy blacklist ────────────────────────────────────────────


@pytest.mark.parametrize('title,process', [
    ('KeePass - vault.kdbx', 'KeePass.exe'),
    ('1Password 8', '1Password.exe'),
    ('Bitwarden', 'Bitwarden.exe'),
    ('Ledger Live - Portfolio', 'Ledger Live.exe'),
])
def test_privacy_classification_emits_private_state(title, process):
    """Sensitive apps classify as state='private', propensity='closed'.

    Native apps only — title-based ``private`` classification fired
    inside a browser process gets demoted to ``unknown`` (see
    ``observation_from_system``) to avoid false-positives on marketing
    pages, docs, and HN posts about password managers. So
    Vaultwarden-via-chrome.exe is intentionally NOT in this list any
    more; covered separately by
    ``test_private_title_in_browser_does_not_trigger_lockdown``.
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title=title, process=process)
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.state == 'private'
    assert snap.propensity == 'closed'


def test_private_state_redacts_active_window():
    """ActivitySnapshot.active_window is None when state=private — no leakage."""
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='KeePass - mybank.kdbx', process='KeePass.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.active_window is None  # title 'mybank' must not leak


def test_private_state_overrides_voice_engaged():
    """Privacy wins over voice mode — secrets-app foreground silences AI even mid-voice."""
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)
    sm.update_voice_mode(True)
    sm.update_voice_rms()
    sn = _sys_snap(title='1Password', process='1Password.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.state == 'private'


def test_private_state_yields_to_away():
    """When the user has been idle for AWAY_IDLE_SECONDS, away wins.

    Privacy app left open while user walked away is just an idle desk —
    no secrets being handled right now. Away → frontend backoff handles
    cadence, no proactive misfire concern.
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='KeePass', process='KeePass.exe', idle=20 * 60)
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.state == 'away'


@pytest.mark.parametrize('title,process', [
    ('Bitwarden Pricing | Best Password Manager - Bitwarden', 'chrome.exe'),
    ('KeePass User Guide - Documentation', 'firefox.exe'),
    ('1Password vs LastPass - blog comparison', 'msedge.exe'),
    ('Why I switched to Vaultwarden — Hacker News', 'brave.exe'),
])
def test_private_title_in_browser_does_not_trigger_lockdown(title, process):
    """Browser tabs about password managers (marketing pages, docs,
    blog posts, HN comments) MUST NOT trip the privacy lockdown.

    Native private apps catch via ``PRIVATE_PROCESS_NAMES`` (process
    match in ``observation_from_system``); the title-only path inside
    a browser is too noisy and would silence proactive chat over
    "user is reading about KeePass". Only real running password
    managers should drive the lockdown.
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title=title, process=process)
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))
    sm.update_user_message()

    snap = sm.get_snapshot()
    assert snap.state != 'private', (
        f"browser tab {title!r} (process={process}) must NOT classify "
        f"as 'private'; got state={snap.state}"
    )


# ── #4 Own-app exclusion (N.E.K.O / Xiao8) ──────────────────────────


def test_own_app_does_not_replace_window_observation():
    """Catgirl-app foreground is transparent — previous window stays active."""
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    # Step 1: User in VS Code (work) for 100s
    work = _sys_snap(title='proactive_chat.py - VS Code', process='Code.exe')
    sm.update_system(work)
    sm.update_window(observation_from_system(work, prefs))
    sm.update_user_message()
    snap_before = sm.get_snapshot(now=time.time() + 100)
    assert snap_before.state == 'focused_work'

    # Step 2: User opens N.E.K.O
    own = _sys_snap(title='Project N.E.K.O', process='Xiao8.exe', gpu=85.0)
    sm.update_system(own)
    sm.update_window(observation_from_system(own, prefs), now=time.time() + 200)
    snap_during = sm.get_snapshot(now=time.time() + 201)

    # Window should still be Code.exe — own_app was filtered out
    assert snap_during.active_window is not None
    assert snap_during.active_window.canonical == 'Code.exe'
    assert snap_during.state == 'focused_work'


def test_own_app_preserves_previous_window_for_gpu_fallback():
    """own_app foreground keeps prev window's classification active.

    Critically, own_app does NOT suppress gaming-by-GPU on the prev
    window — if the user had an unknown high-GPU game running and
    briefly tabs to N.E.K.O, their real activity is still that game
    and the classification should reflect it. The own_app contract is
    "freeze dwell + don't replace the observation", not "disable
    background classification".
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    # Step 1: unknown high-GPU app (an indie game not in keyword DB).
    # GPU fallback should fire and classify as gaming.
    indie = _sys_snap(title='SomeIndieGame', process='IndieGame.exe', gpu=85.0)
    sm.update_system(indie)
    sm.update_window(observation_from_system(indie, prefs))
    sm.update_user_message()
    snap_pre = sm.get_snapshot()
    assert snap_pre.state == 'gaming', (
        f'GPU fallback should classify unknown high-GPU + active user as gaming; '
        f'got {snap_pre.state}'
    )

    # Step 2: brief glance at N.E.K.O. Prev observation must NOT be
    # replaced; gaming-by-GPU continues to fire on the prev window data.
    own = _sys_snap(title='N.E.K.O', process='projectneko_server.exe', gpu=85.0)
    sm.update_system(own)
    sm.update_window(observation_from_system(own, prefs))
    snap_during = sm.get_snapshot()

    assert snap_during.active_window is not None
    # ``canonical`` is None for unknown-category observations (the
    # static DB had nothing); compare process_name which IS preserved.
    assert snap_during.active_window.process_name == 'IndieGame.exe', (
        'own_app must not replace prev window observation; '
        f'got process_name={snap_during.active_window.process_name}'
    )
    assert snap_during.active_window.category == 'unknown'
    assert snap_during.state == 'gaming', (
        "own_app must NOT short-circuit prev window's gaming-by-GPU classification — "
        "user's real activity (the indie game) is what matters; "
        f'got {snap_during.state}'
    )


# ── #4 User app overrides ───────────────────────────────────────────


def test_user_app_override_patches_unknown():
    """Unknown app + user override → classifies as the override category."""
    prefs = ActivityPreferences(
        user_app_overrides={
            'mycorpapp.exe': _AppOverride(
                category='work', subcategory='office', canonical='MyCorpApp',
            ),
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='MyCorpApp - Documents', process='MyCorpApp.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))
    sm.update_user_message()

    snap = sm.get_snapshot(now=time.time() + 100)  # past dwell threshold
    assert snap.state == 'focused_work'
    assert snap.active_window is not None
    assert snap.active_window.category == 'work'
    assert snap.active_window.canonical == 'MyCorpApp'


def test_user_app_override_does_not_rewrite_stable_static_classification():
    """User app override fires ONLY when static classifier returned 'unknown'.

    Symmetric with title-override behaviour: overrides are additive
    (they classify what the static DB missed), they don't rewrite a
    stable DB hit. Otherwise a user typo / mistaken category in the
    override dict could quietly break classification of a well-known app.
    """
    prefs = ActivityPreferences(
        user_app_overrides={
            'code.exe': _AppOverride(category='entertainment', canonical='Code'),
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    # Code.exe is in the static DB as work/ide; user override should be ignored.
    sn = _sys_snap(title='proactive_chat.py - Visual Studio Code', process='Code.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.active_window is not None
    assert snap.active_window.category == 'work', (
        'static DB hit (Code.exe → work) must not be rewritten by user override; '
        f'got category={snap.active_window.category}'
    )


def test_user_app_override_cannot_unmask_private():
    """User can't override KeePass to 'work' — privacy guarantee survives."""
    prefs = ActivityPreferences(
        user_app_overrides={
            'keepass.exe': _AppOverride(category='work'),
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='KeePass', process='KeePass.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    # Contract: user_app_overrides / user_title_overrides are additive-only —
    # they only fire when the static keyword DB returned ``unknown``
    # (`result.category == 'unknown'`). On a static private hit the override
    # is suppressed by the `static_locked` guard in `_apply_user_overrides`,
    # so a "work" override on KeePass.exe stays as `private`.
    assert snap.state == 'private', (
        'User app override must not be allowed to demote a privacy-DB hit'
    )


def test_user_game_override_patches_intensity():
    """User can flip a game's intensity/genre."""
    prefs = ActivityPreferences(
        user_game_overrides={
            'League of Legends': _GameOverride(intensity='casual', genre='moba'),
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='League of Legends', process='LeagueClient.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))

    snap = sm.get_snapshot()
    assert snap.state == 'gaming'
    assert snap.game_intensity == 'casual'
    # casual gaming → propensity=open per derivation table
    assert snap.propensity == 'open'
    assert snap.tone == 'playful'


# ── #5 Threshold externalization ────────────────────────────────────


def test_thresholds_load_from_preferences():
    """Threshold overrides take effect at state machine construction."""
    prefs = ActivityPreferences(
        thresholds={
            'away_idle_seconds': 60.0,  # default 900; aggressive 1min
            'focused_work_min_dwell_seconds': 5.0,
        },
    )
    sm = ActivityStateMachine(prefs=prefs)

    # 65s idle should now trip 'away' even though default is 15min.
    sn = _sys_snap(idle=65.0)
    sm.update_system(sn)
    snap = sm.get_snapshot()
    assert snap.state == 'away'


def test_count_thresholds_reject_non_integer_floats():
    """``window_switch_transition_threshold`` and
    ``unfinished_thread_max_followups`` are count-shaped — floats like
    ``0.9`` or ``1.7`` must NOT silently truncate to 0 / 1 (which would
    break transitioning detection or turn a typo into an unintended
    unfinished_thread override). Loader-side validation only checks
    "positive number"; the integer guard lives in ActivityStateMachine.

    Verify by direct ActivityPreferences construction (bypasses the
    loader's validation) — if the int guard ever regresses, this catches it.
    """
    # Float that would silently round to 0 or 1
    bad_prefs = ActivityPreferences(thresholds={
        'window_switch_transition_threshold': 0.9,    # int(0.9) → 0 (broken)
        'unfinished_thread_max_followups': 1.7,        # int(1.7) → 1 (off-by-one)
    })
    sm = ActivityStateMachine(prefs=bad_prefs)
    # Both must fall back to defaults rather than truncate
    assert sm._window_switch_transition_threshold == 5, (
        f'non-integer float must fall back to default 5; '
        f'got {sm._window_switch_transition_threshold}'
    )
    assert sm._unfinished_thread_max_followups == 1, (
        f'non-integer float must fall back to default 1; '
        f'got {sm._unfinished_thread_max_followups}'
    )

    # Whole-number floats (3.0) are OK — they ARE integers in value.
    ok_prefs = ActivityPreferences(thresholds={
        'window_switch_transition_threshold': 7.0,
        'unfinished_thread_max_followups': 3.0,
    })
    sm2 = ActivityStateMachine(prefs=ok_prefs)
    assert sm2._window_switch_transition_threshold == 7
    assert sm2._unfinished_thread_max_followups == 3


def test_loader_drops_invalid_threshold_values():
    """The JSON-side loader silently drops malformed threshold entries.

    Direct ``ActivityPreferences(thresholds={...})`` construction trusts
    the caller (no second-pass validation), but the JSON loader is the
    real-user path and must be defensive against typos / wrong types.
    """
    from utils.activity_config import _parse_thresholds
    out = _parse_thresholds({
        'away_idle_seconds': 60.0,           # valid positive number
        'stale_recovery_seconds': -5,        # negative → dropped
        'voice_active_window_seconds': 0,    # zero → dropped (positive only)
        'focused_work_min_dwell_seconds': True,  # bool → dropped (subclass of int)
        'casual_browsing_min_dwell_seconds': 'hi',  # string → dropped
        'gaming_gpu_threshold_percent': 60.0,
    })
    assert out == {
        'away_idle_seconds': 60.0,
        'gaming_gpu_threshold_percent': 60.0,
    }


# ── #7 Tone modifier ────────────────────────────────────────────────


@pytest.mark.parametrize('state,intensity,genre,expected', [
    ('voice_engaged',   None,           None,     'warm'),
    ('chatting',        None,           None,     'warm'),
    ('stale_returning', None,           None,     'warm'),
    ('focused_work',    None,           None,     'concise'),
    # ``idle`` while desk-pet is up reads as 摸鱼 territory — pair with
    # ``playful`` (light banter) instead of the businesslike ``concise``.
    # ``transitioning`` and ``away`` keep ``concise``: the former is
    # mid-context-switch, the latter doesn't render.
    ('idle',            None,           None,     'playful'),
    ('away',            None,           None,     'concise'),
    # casual_browsing == watching anime/video → snarky ``witty`` (with a
    # [PASS]-if-not-funny quality bar rendered separately), not generic playful.
    ('casual_browsing', None,           None,     'witty'),
    ('private',         None,           None,     'concise'),
    ('gaming',          'competitive',  'moba',   'terse'),
    ('gaming',          'competitive',  'fps',    'terse'),
    ('gaming',          'immersive',    'horror', 'hushed'),
    ('gaming',          'immersive',    'rpg',    'mellow'),
    ('gaming',          'immersive',    'action', 'mellow'),
    ('gaming',          'casual',       'sim',    'playful'),
    ('gaming',          'varied',       'misc',   'concise'),
    ('gaming',          None,           None,     'concise'),
])
def test_tone_derivation_table(state, intensity, genre, expected):
    """Pin the (state, intensity, genre) → tone mapping."""
    assert derive_tone(state, game_intensity=intensity, game_genre=genre) == expected


def _witty_snapshot() -> ActivitySnapshot:
    """A minimal casual_browsing / witty snapshot for render assertions."""
    return ActivitySnapshot(
        state='casual_browsing', state_age_seconds=120.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='open', tone='witty',
    )


@pytest.mark.unit
# zh/ja/ko/ru/en 是 _normalize_lang 能区分出的全部语言；es/pt 经 _normalize_lang
# 回退 en，这里一并断言它们至少拿到 en 质量闸（而非什么都没有）。
@pytest.mark.parametrize('lang', ['zh', 'en', 'ja', 'ko', 'ru', 'es', 'pt'])
def test_witty_quality_bar_renders(lang):
    """``witty`` 渲染时必须带上「没梗就 [PASS]」质量闸（区别于其它语气）。

    回归保护：质量闸是 witty 专属的额外一行，靠 ACTIVITY_TONE_QUALITY_BARS +
    formatter 的 .get(tone) 渲染。若任一环节回归（漏渲染 / 表里缺 lang），
    entertainment 就退回「有啥说啥」，丢掉「没梗宁可不说」的产品意图。
    """
    out = format_activity_state_section(_witty_snapshot(), lang=lang)
    assert '[PASS]' in out, f'{lang}: witty 质量闸（[PASS] 指令）未渲染'


@pytest.mark.unit
def test_non_witty_tone_has_no_quality_bar():
    """非 witty 语气不应渲染质量闸——它是 witty 专属，别误伤其它语气。"""
    snap = ActivitySnapshot(
        state='idle', state_age_seconds=10.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='open', tone='playful',
    )
    out = format_activity_state_section(snap, lang='zh')
    assert '[PASS]' not in out


@pytest.mark.unit
def test_activity_state_renders_open_threads_in_state_section():
    snap = ActivitySnapshot(
        state='idle', state_age_seconds=10.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='open', tone='playful',
        open_threads=['AI 答应等会帮看测试还没看'],
    )

    out = format_activity_state_section(snap, lang='zh')

    assert '开放话题:' in out
    assert '- AI 答应等会帮看测试还没看' in out


@pytest.mark.unit
def test_activity_state_unfinished_thread_hides_followup_count():
    snap = ActivitySnapshot(
        state='focused_work', state_age_seconds=10.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='restricted_screen_only', tone='concise',
        unfinished_thread=UnfinishedThread(
            text='主人，你今天准备几点出发?',
            age_seconds=60.0,
            follow_up_count=0,
            max_follow_ups=1,
        ),
    )

    out = format_activity_state_section(snap, lang='zh')

    assert '未收尾话题：「…主人，你今天准备几点出发?」(60s前)' in out
    assert '已跟进' not in out
    assert '/1' not in out


@pytest.mark.unit
def test_activity_state_line_uses_localized_label_not_english_enum():
    """Regression: the state line renders only the localized label, never
    the bare English enum (snap.state / snap.propensity). Weak models echo
    a leaked enum as the reply's first line, so a zh user ends up hearing
    'focused_work' / 'restricted_screen_only'."""
    snap = ActivitySnapshot(
        state='focused_work', state_age_seconds=10.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='restricted_screen_only', tone='concise',
    )
    out = format_activity_state_section(snap, lang='zh')
    assert '专注工作中' in out
    assert 'focused_work' not in out
    assert 'restricted_screen_only' not in out


@pytest.mark.unit
def test_activity_scores_line_maps_english_state_keys_to_localized_labels():
    """Regression: the scores line maps English state keys to localized
    labels instead of emitting 'focused_work' raw. The keys come from the
    emotion-tier LLM under an English contract — the subtlest leak source."""
    snap = ActivitySnapshot(
        state='focused_work', state_age_seconds=10.0, previous_state=None,
        transitioned_recently=False, stale_returning=False,
        propensity='open', tone='concise',
        activity_scores={'focused_work': 0.7, 'chatting': 0.2, 'idle': 0.1},
    )
    out = format_activity_state_section(snap, lang='zh')
    assert '专注工作中 0.7' in out
    assert '聊天中 0.2' in out
    assert 'focused_work' not in out
    assert 'chatting' not in out


# ── #1 / skip_probability ───────────────────────────────────────────


def test_skip_probability_defaults():
    """Pin the default skip-probability table."""
    # Non-gaming → always 0
    assert derive_skip_probability('focused_work') == 0.0
    assert derive_skip_probability('chatting') == 0.0
    assert derive_skip_probability('idle') == 0.0

    # Gaming defaults — competitive intentionally dropped to 0 (was 0.3).
    # 屏幕专注态的安静感现在由 /proactive_chat 的 base-interval×1.25 + 后端
    # 抖动机制承担，skip_probability 只留 immersive_horror 这一例外。
    assert derive_skip_probability('gaming', game_intensity='competitive') == 0.0
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='horror',
    ) == pytest.approx(0.3)
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='rpg',
    ) == 0.0
    assert derive_skip_probability('gaming', game_intensity='casual') == 0.0
    assert derive_skip_probability('gaming', game_intensity='varied') == 0.0


def test_skip_probability_user_overrides_replace_defaults():
    """User overrides win and clamp into [0, 1]."""
    overrides = {
        'competitive':       0.8,
        'immersive_horror':  1.0,
        'casual':            1.5,    # clamps to 1.0
        'immersive_rpg':     -0.5,   # clamps to 0.0
    }
    assert derive_skip_probability(
        'gaming', game_intensity='competitive', overrides=overrides,
    ) == pytest.approx(0.8)
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='horror', overrides=overrides,
    ) == pytest.approx(1.0)
    assert derive_skip_probability(
        'gaming', game_intensity='casual', overrides=overrides,
    ) == 1.0
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='rpg', overrides=overrides,
    ) == 0.0


def test_skip_probability_specific_combo_beats_intensity_only():
    """``immersive_horror`` override wins over an ``immersive`` override."""
    overrides = {'immersive': 0.4, 'immersive_horror': 0.9}
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='horror', overrides=overrides,
    ) == pytest.approx(0.9)
    # Without horror genre, falls through to intensity-only override
    assert derive_skip_probability(
        'gaming', game_intensity='immersive', game_genre='rpg', overrides=overrides,
    ) == pytest.approx(0.4)


# ── Privacy + stale_returning regression (Codex P1) ─────────────────


def test_private_survives_stale_returning_window():
    """Privacy lockdown must NOT downgrade to greeting_window when the
    stale-returning sticky window happens to be active.

    Scenario: user was away (15+ min idle), returns, opens KeePass as
    their first action. Without the fix, ``effective_state`` would be
    ``stale_returning`` → propensity ``greeting_window`` → proactive
    chat would run and could even nudge a reminisce, while the user
    is staring at password manager. Privacy must win.
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    # Simulate "user was away" then returns → open KeePass
    base = time.time()
    away_snap = _sys_snap(idle=20 * 60, ts=base)
    sm.update_system(away_snap)
    sm.get_snapshot(now=base)  # state machine sees away

    # User returns, opens KeePass within the stale_recovery window
    return_ts = base + 10
    keepass = _sys_snap(title='KeePass - vault.kdbx', process='KeePass.exe', idle=2.0, ts=return_ts)
    sm.update_system(keepass)
    sm.update_window(observation_from_system(keepass, prefs), now=return_ts)

    snap = sm.get_snapshot(now=return_ts)
    assert snap.state == 'private', (
        'Stale-recovery window must NOT override private state; '
        f'got {snap.state}'
    )
    assert snap.propensity == 'closed', (
        f'private must keep closed propensity (got {snap.propensity})'
    )


# ── Loader robustness (Codex P2) ────────────────────────────────────


def test_loader_keeps_last_good_prefs_on_parse_failure(tmp_path, monkeypatch):
    """A mid-edit corrupted JSON must NOT wipe previously cached overrides."""
    from utils.activity_config import (
        _GLOBAL_CONVERSATION_KEY, _load_from_file,
    )

    # Round 1: write a valid file with overrides
    pref_file = tmp_path / 'user_preferences.json'
    import json
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {
                'thresholds': {'away_idle_seconds': 300},
                'user_app_overrides': {
                    'mycorp.exe': {'category': 'work'},
                },
            },
        }]),
        encoding='utf-8',
    )
    p1 = _load_from_file(str(pref_file))
    assert p1 is not None
    assert p1.thresholds == {'away_idle_seconds': 300.0}
    assert 'mycorp.exe' in p1.user_app_overrides

    # Round 2: corrupt the file (simulating mid-edit save)
    pref_file.write_text('{ malformed json without closing', encoding='utf-8')
    p2 = _load_from_file(str(pref_file))
    assert p2 is None, 'parse failure must signal None, not return defaults'

    # Round 3: end-to-end through the public API. Pin the contract that
    # `get_activity_preferences()` keeps the previously cached prefs
    # intact when `_load_from_file` returns None — not just that the
    # private helper signals None correctly. Without this leg, a future
    # refactor that "helpfully" replaces the cache with defaults on
    # parse failure would still let the test above pass while breaking
    # the user-facing contract (their overrides quietly vanish).
    from utils.activity_config import (
        get_activity_preferences,
        invalidate_activity_preferences_cache,
    )
    monkeypatch.setattr(
        'utils.activity_config._resolve_preferences_path',
        lambda: str(pref_file),
    )
    invalidate_activity_preferences_cache()

    # Restore valid file content + populate cache via the public API
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {
                'thresholds': {'away_idle_seconds': 300},
                'user_app_overrides': {
                    'mycorp.exe': {'category': 'work'},
                },
            },
        }]),
        encoding='utf-8',
    )
    cached_good = get_activity_preferences()
    assert cached_good.thresholds == {'away_idle_seconds': 300.0}
    assert 'mycorp.exe' in cached_good.user_app_overrides

    # Now corrupt the file and force a reload. The public API must
    # serve the previous good cache, NOT defaults.
    pref_file.write_text('{ malformed json again', encoding='utf-8')
    invalidate_activity_preferences_cache()
    cached_after_corrupt = get_activity_preferences()
    assert cached_after_corrupt.thresholds == {'away_idle_seconds': 300.0}, (
        'parse failure must preserve previously cached thresholds; '
        f'got {cached_after_corrupt.thresholds}'
    )
    assert 'mycorp.exe' in cached_after_corrupt.user_app_overrides, (
        'parse failure must preserve previously cached user_app_overrides'
    )


def test_loader_returns_defaults_when_no_activity_section(tmp_path):
    """Successfully parsed file without activity section returns defaults
    (NOT None — that's reserved for parse failures)."""
    from utils.activity_config import (
        _GLOBAL_CONVERSATION_KEY, _load_from_file,
    )
    pref_file = tmp_path / 'user_preferences.json'
    import json
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'proactiveChatEnabled': True,
            # No 'activity' field
        }]),
        encoding='utf-8',
    )
    p = _load_from_file(str(pref_file))
    assert p is not None
    assert isinstance(p, ActivityPreferences)
    assert p.thresholds == {}
    assert p.user_app_overrides == {}


def test_conversation_turn_dispatcher_sends_messages_to_background_topic_pool():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    calls = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('user', lanlan_name, text, lang))

        def note_ai_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('ai', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: False,
    )
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))

    dispatcher.note_user_message(text='我想买凯迪拉克，但预算有点顶不住', now=1.0)
    dispatcher.note_ai_message(text='别急着破釜沉舟，先看看预算。', now=2.0)

    assert calls == [
        ('user', 'test_lanlan', '我想买凯迪拉克，但预算有点顶不住', 'zh-CN'),
        ('ai', 'test_lanlan', '别急着破釜沉舟，先看看预算。', 'zh-CN'),
    ]


def test_conversation_turn_dispatcher_uses_global_language_for_background_topic_pool(monkeypatch):
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink
    from utils import language_utils

    calls = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('user', lanlan_name, text, lang))

        def note_ai_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('ai', lanlan_name, text, lang))

    monkeypatch.setattr(language_utils, 'get_global_language', lambda: 'en-US')

    dispatcher = ConversationTurnDispatcher('test_lanlan', privacy_check=lambda: False)
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))
    dispatcher.note_user_message(text='I want a new phone but I am not sure about the price.', now=1.0)
    dispatcher.note_ai_message(text='Fair, let us slow down before your wallet files a complaint.', now=2.0)

    assert calls == [
        ('user', 'test_lanlan', 'I want a new phone but I am not sure about the price.', 'en'),
        ('ai', 'test_lanlan', 'Fair, let us slow down before your wallet files a complaint.', 'en'),
    ]


def test_conversation_turn_dispatcher_uses_session_language_for_background_topic_pool(monkeypatch):
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink
    from utils import language_utils

    calls = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('user', lanlan_name, text, lang))

        def note_ai_message(self, lanlan_name, text, *, lang='zh'):
            calls.append(('ai', lanlan_name, text, lang))

    monkeypatch.setattr(language_utils, 'get_global_language', lambda: 'en-US')

    dispatcher = ConversationTurnDispatcher('test_lanlan', privacy_check=lambda: False)
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))
    dispatcher.set_language('ja-JP')
    dispatcher.note_user_message(text='転職について少し迷っています。', now=1.0)
    dispatcher.note_ai_message(text='焦らず、次の条件を一緒に整理しよう。', now=2.0)

    assert calls == [
        ('user', 'test_lanlan', '転職について少し迷っています。', 'ja'),
        ('ai', 'test_lanlan', '焦らず、次の条件を一緒に整理しよう。', 'ja'),
    ]


def test_conversation_turn_dispatcher_preserves_traditional_chinese_topic_locale():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    calls = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            calls.append((lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher('test_lanlan', privacy_check=lambda: False)
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))
    dispatcher.set_language('zh-TW')
    dispatcher.note_user_message(text='我想用繁體中文聊最近的生活選擇', now=1.0)

    assert calls == [('test_lanlan', '我想用繁體中文聊最近的生活選擇', 'zh-TW')]


def test_conversation_turn_dispatcher_keeps_topic_text_in_privacy_mode():
    from main_logic.conversation_turns import (
        ActivityTrackerTurnSink,
        ConversationTurnDispatcher,
        TopicHookTurnSink,
    )

    activity_calls = []
    topic_calls = []

    class FakeActivityTracker:
        def on_user_message(self, *, text=None, now=None):
            activity_calls.append(('user', text, now))

        def on_ai_message(self, *, text=None, now=None):
            activity_calls.append(('ai', text, now))

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            topic_calls.append(('user', lanlan_name, text, lang))

        def note_ai_message(self, lanlan_name, text, *, lang='zh'):
            topic_calls.append(('ai', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: True,
    )
    dispatcher.add_sink(ActivityTrackerTurnSink(FakeActivityTracker()))
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))

    dispatcher.note_user_message(text='secret user turn', now=1.0)
    dispatcher.note_ai_message(text='secret ai turn?', now=2.0)

    assert activity_calls == [
        ('user', None, 1.0),
        ('ai', None, 2.0),
    ]
    assert topic_calls == [
        ('user', 'test_lanlan', 'secret user turn', 'zh-CN'),
        ('ai', 'test_lanlan', 'secret ai turn?', 'zh-CN'),
    ]


def test_conversation_turn_dispatcher_redacts_when_privacy_check_fails():
    from main_logic.conversation_turns import (
        ActivityTrackerTurnSink,
        ConversationTurnDispatcher,
        TopicHookTurnSink,
    )

    activity_calls = []
    topic_calls = []

    class FakeActivityTracker:
        def on_user_message(self, *, text=None, now=None):
            activity_calls.append(('user', text, now))

        def on_ai_message(self, *, text=None, now=None):
            activity_calls.append(('ai', text, now))

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            topic_calls.append(('user', lanlan_name, text, lang))

        def note_ai_message(self, lanlan_name, text, *, lang='zh'):
            topic_calls.append(('ai', lanlan_name, text, lang))

    def broken_privacy_check():
        raise RuntimeError("preference store unavailable")

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=broken_privacy_check,
    )
    dispatcher.add_sink(ActivityTrackerTurnSink(FakeActivityTracker()))
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))

    dispatcher.note_user_message(text='secret user turn', now=1.0)

    assert activity_calls == [('user', None, 1.0)]
    assert topic_calls == [('user', 'test_lanlan', 'secret user turn', 'zh-CN')]


def test_conversation_turn_dispatcher_sends_redacted_turns_to_topic_store():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    notes = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            notes.append(('user', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: True,
    )
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))

    dispatcher.note_user_message(text='secret user turn', now=1.0)

    assert notes == [('user', 'test_lanlan', 'secret user turn', 'zh-CN')]


def test_topic_turn_sink_ignores_activity_private_for_signal_store():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    notes = []

    class FakeTopicPool:
        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            notes.append(('user', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: False,
    )
    dispatcher.add_sink(
        TopicHookTurnSink(
            pool_factory=lambda: FakeTopicPool(),
            activity_private_check=lambda: True,
        )
    )

    dispatcher.note_user_message(text='private foreground turn', now=1.0)

    assert notes == [('user', 'test_lanlan', 'private foreground turn', 'zh-CN')]


def test_activity_guess_loop_kicks_topic_candidates_before_private_bail():
    from main_logic.activity.tracker import UserActivityTracker

    source = inspect.getsource(UserActivityTracker._activity_guess_loop)
    assert "self._process_topic_candidates_if_ready(lang=topic_lang, now=ts)" in source
    assert "await self._purge_topic_candidates_for_privacy()" not in source
    assert source.index("self._process_topic_candidates_if_ready(lang=topic_lang, now=ts)") < source.index("if rule_snap.state == 'private':")
    assert source.index("if rule_snap.state == 'private':") < source.index("if not _proactive_chat_enabled():")


def test_activity_guess_gate_wired_with_production_backoff_knobs():
    """config → tracker → gate wiring carries the user-tuned faster-decay
    schedule: 4x growth per re-narration, capped at 900s (aligned with the
    15-min away threshold)."""
    from main_logic.activity.tracker import UserActivityTracker
    from config import (
        ACTIVITY_GUESS_BACKOFF_BASE_SECONDS,
        ACTIVITY_GUESS_BACKOFF_MULTIPLIER,
        ACTIVITY_GUESS_BACKOFF_CAP_SECONDS,
    )

    tracker = UserActivityTracker('test_lanlan')
    gate = tracker._activity_guess_gate
    assert gate._base == ACTIVITY_GUESS_BACKOFF_BASE_SECONDS
    assert gate._mult == ACTIVITY_GUESS_BACKOFF_MULTIPLIER
    assert gate._cap == max(
        ACTIVITY_GUESS_BACKOFF_CAP_SECONDS, ACTIVITY_GUESS_BACKOFF_BASE_SECONDS,
    )
    # Pin the user's headline ask so a silent revert to the old 2x/600s is caught.
    assert ACTIVITY_GUESS_BACKOFF_MULTIPLIER == 4.0
    assert ACTIVITY_GUESS_BACKOFF_CAP_SECONDS == 900.0


def test_narration_suppressed_check_defaults_and_injection():
    from main_logic.activity.tracker import UserActivityTracker

    tracker = UserActivityTracker('test_lanlan')

    # No predicate injected → never suppressed (old behavior preserved).
    assert tracker._is_narration_suppressed() is False

    # Predicate returning True → suppressed.
    tracker.set_narration_suppressed_check(lambda: True)
    assert tracker._is_narration_suppressed() is True

    # Predicate returning False → not suppressed.
    tracker.set_narration_suppressed_check(lambda: False)
    assert tracker._is_narration_suppressed() is False

    # Raising predicate → fail-open (don't suppress, don't crash the heartbeat).
    def _boom():
        raise RuntimeError('boom')

    tracker.set_narration_suppressed_check(_boom)
    assert tracker._is_narration_suppressed() is False

    # Cleared → back to never suppressed.
    tracker.set_narration_suppressed_check(None)
    assert tracker._is_narration_suppressed() is False


def test_activity_guess_loop_skips_llm_when_narration_suppressed():
    """The narration-suppressed gate sits with the other no-consumer skips:
    after the proactive-disabled gate, before the away bail, and uses the
    fail-open helper rather than calling the predicate inline."""
    from main_logic.activity.tracker import UserActivityTracker

    source = inspect.getsource(UserActivityTracker._activity_guess_loop)
    assert "if self._is_narration_suppressed():" in source
    assert (
        source.index("if not _proactive_chat_enabled():")
        < source.index("if self._is_narration_suppressed():")
    )
    assert (
        source.index("if self._is_narration_suppressed():")
        < source.index("if rule_snap.state == 'away':")
    )


def test_activity_guess_signature_excludes_idle_bucket():
    """The activity_guess gate must not key on monotonically-growing idle time.

    While AFK, system_idle_seconds keeps climbing; an earlier version folded
    idle//30 into the dedup signature, flipping it every ~30s and burning one
    emotion-tier LLM call every ~40s during pure idle. The gate now keys on a
    coarse ``(state, window-category)`` signature handed to ActivityGuessGate
    (per-signature backoff + novelty bypass); idle seconds never enter it. The
    active->idle->away transition is still caught by state (away bails anyway).
    """
    from main_logic.activity.tracker import UserActivityTracker

    from types import SimpleNamespace

    loop_source = inspect.getsource(UserActivityTracker._activity_guess_loop)
    sig_source = inspect.getsource(UserActivityTracker._coarse_activity_sig)
    # idle_bucket 是旧签名里按 idle 秒数分桶的那个变量名（空烧根因），断言它
    # 不回归即精准守住该行为。不断言 "system_idle_seconds" not in source：那比
    # 约束目标更宽，会误伤将来 loop 里对 idle 秒数的其它无害引用（喂 LLM 的
    # signals 仍在 _snapshot_signals_for_llm 这个独立方法里用到它）。
    assert "idle_bucket" not in loop_source
    assert "idle_bucket" not in sig_source
    assert "self._activity_guess_gate.should_fire(" in loop_source
    # Behaviour (not just the comment): the coarse key is (state, category) —
    # different category → different key; same category but different
    # canonical/subcategory → SAME key (coarsening), so window flicker within a
    # category does not re-narrate.
    work = SimpleNamespace(
        state='focused_work',
        active_window=SimpleNamespace(category='work', canonical='IDE', subcategory='code'),
    )
    chat = SimpleNamespace(
        state='focused_work',
        active_window=SimpleNamespace(category='chat', canonical='IM', subcategory='dm'),
    )
    same_bucket = SimpleNamespace(
        state='focused_work',
        active_window=SimpleNamespace(category='work', canonical='Browser', subcategory='docs'),
    )
    assert (UserActivityTracker._coarse_activity_sig(work)
            != UserActivityTracker._coarse_activity_sig(chat))
    assert (UserActivityTracker._coarse_activity_sig(work)
            == UserActivityTracker._coarse_activity_sig(same_bucket))


def test_conversation_turn_dispatcher_does_not_purge_topic_signals_for_redacted_turns():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    purges = []
    notes = []

    class FakeTopicPool:
        def purge_all_accumulated_signals(self):
            purges.append("*")

        def purge_accumulated_signals(self, lanlan_name):
            purges.append(lanlan_name)

        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            notes.append(('user', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: True,
    )
    dispatcher.add_sink(TopicHookTurnSink(pool_factory=lambda: FakeTopicPool()))

    dispatcher.note_user_message(text='secret user turn', now=1.0)

    assert purges == []
    assert notes == [('user', 'test_lanlan', 'secret user turn', 'zh-CN')]


def test_topic_turn_sink_keeps_current_character_when_activity_is_private():
    from main_logic.conversation_turns import ConversationTurnDispatcher, TopicHookTurnSink

    purges = []
    notes = []

    class FakeTopicPool:
        def purge_accumulated_signals(self, lanlan_name):
            purges.append(lanlan_name)

        def note_user_message(self, lanlan_name, text, *, lang='zh'):
            notes.append(('user', lanlan_name, text, lang))

    dispatcher = ConversationTurnDispatcher(
        'test_lanlan',
        language='zh-CN',
        privacy_check=lambda: False,
    )
    dispatcher.add_sink(
        TopicHookTurnSink(
            pool_factory=lambda: FakeTopicPool(),
            activity_private_check=lambda: True,
        )
    )

    dispatcher.note_user_message(text='private foreground turn', now=1.0)

    assert purges == []
    assert notes == [('user', 'test_lanlan', 'private foreground turn', 'zh-CN')]


# ── Hot-reload (Codex P2) ───────────────────────────────────────────


def test_tracker_picks_up_fresh_prefs_via_refresh_hook():
    """``UserActivityTracker._refresh_prefs`` swaps in updated prefs.

    The state machine stores prefs at __init__, so a long-lived session
    won't see edits unless someone refreshes. This test calls the
    refresh hook directly with a new prefs object and verifies the
    state machine starts honouring the new override.
    """
    from main_logic.activity.tracker import UserActivityTracker
    from main_logic.activity.system_signals import (
        SystemSignalCollector, get_system_signal_collector,
    )

    # Round 1 — empty prefs, nothing classified
    initial_prefs = ActivityPreferences()
    tracker = UserActivityTracker(
        lanlan_name='_test_hot_reload',
        collector=get_system_signal_collector(),  # singleton fine; we don't start it
    )
    tracker._sm = ActivityStateMachine(prefs=initial_prefs)
    sn = _sys_snap(title='SomeUnknownApp', process='SomeUnknownApp.exe')
    obs = observation_from_system(sn, tracker._sm._prefs)
    assert obs.category == 'unknown', f'unknown app should classify as unknown, got {obs.category}'

    # Round 2 — bring in fresh prefs with override; tracker swaps in
    new_prefs = ActivityPreferences(
        user_app_overrides={
            'someunknownapp.exe': _AppOverride(category='work', subcategory='office', canonical='SomeUnknownApp'),
        },
    )

    # Simulate the loader returning a different cached object
    original = _cache.prefs
    try:
        _cache.prefs = new_prefs

        # Direct private-hook call — sanity check that _refresh_prefs
        # itself swaps prefs correctly when invoked.
        tracker._refresh_prefs()
        obs2 = observation_from_system(sn, tracker._sm._prefs)
        assert obs2.category == 'work'
        assert obs2.canonical == 'SomeUnknownApp'

        # Public API check — the contract is "get_snapshot_sync triggers
        # _refresh_prefs at the entry point". If a future refactor
        # removes that call from the public entry, the direct test
        # above would still pass while live sessions stop hot-reloading.
        # Restore an older prefs object so the next public call has to
        # re-pick the cached new_prefs.
        sentinel_prefs = ActivityPreferences()  # baseline w/ no overrides
        tracker._sm._prefs = sentinel_prefs
        _cache.prefs = new_prefs  # the loader cache stays on new_prefs
        tracker.get_snapshot_sync()  # public entry — must call _refresh_prefs
        assert tracker._sm._prefs is new_prefs, (
            'public get_snapshot_sync must call _refresh_prefs to swap '
            'in fresh cached prefs; if a future refactor removes that '
            'call, live sessions will stop hot-reloading user overrides'
        )

        # Async path parity — the same contract must hold for the async
        # public entry. Reset the in-memory prefs to the sentinel and
        # leave the loader cache pointing at new_prefs, then await
        # get_snapshot() and assert the swap happened. Without this,
        # an async-path refactor could silently strip _refresh_prefs
        # while the sync test stays green.
        tracker._sm._prefs = sentinel_prefs
        _cache.prefs = new_prefs
        asyncio.run(tracker.get_snapshot())
        assert tracker._sm._prefs is new_prefs, (
            'public async get_snapshot must call _refresh_prefs to swap '
            'in fresh cached prefs; if a future refactor removes that '
            'call from the async entry, live sessions will stop '
            'hot-reloading user overrides'
        )
    finally:
        _cache.prefs = original


# ── update_window collapse: intensity/genre must invalidate (CR Major) ─


def test_update_window_collapses_on_canonical_but_invalidates_on_intensity_change():
    """Hot-reloaded ``user_game_overrides`` must propagate immediately.

    When the user is in a tagged game (e.g. League of Legends, default
    competitive moba) and edits ``user_game_overrides`` to flip it to
    ``casual``, the next observation has identical
    category/subcategory/canonical but a NEW intensity. The collapse
    logic must treat this as a window state change so propensity /
    skip_probability / tone re-derive against the new intensity.
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    sn = _sys_snap(title='League of Legends', process='LeagueClient.exe')
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))
    snap1 = sm.get_snapshot()
    assert snap1.game_intensity == 'competitive'
    assert snap1.tone == 'terse'

    # Hot-reload: user override flips LoL to casual
    new_prefs = ActivityPreferences(
        user_game_overrides={
            'League of Legends': _GameOverride(intensity='casual'),
        },
    )
    sm._prefs = new_prefs
    sm.update_window(observation_from_system(sn, new_prefs))
    snap2 = sm.get_snapshot()
    assert snap2.game_intensity == 'casual', (
        'collapse logic must include intensity in same-check; '
        f'got {snap2.game_intensity}'
    )
    assert snap2.propensity == 'open'   # casual unlocks open propensity
    assert snap2.tone == 'playful'


# ── unfinished_thread max_followups respects threshold (CR Major) ─────


def test_mark_unfinished_thread_used_honors_threshold_override():
    """When prefs set max_followups=3, the cap retires the thread on the
    third call, proving explicit integer overrides still win over the default."""
    prefs = ActivityPreferences(
        thresholds={'unfinished_thread_max_followups': 3.0},
    )
    sm = ActivityStateMachine(prefs=prefs)
    # Trip the question heuristic so an unfinished thread opens
    sm.update_ai_message(text='主人，你今天准备做什么呢?')
    assert sm._unfinished_thread is not None
    assert sm._unfinished_thread['follow_up_count'] == 0

    sm.mark_unfinished_thread_used()
    assert sm._unfinished_thread is not None
    assert sm._unfinished_thread['follow_up_count'] == 1

    sm.mark_unfinished_thread_used()
    assert sm._unfinished_thread is not None  # still alive at 2/3
    assert sm._unfinished_thread['follow_up_count'] == 2

    sm.mark_unfinished_thread_used()
    # Hits the threshold (3) — record retired
    assert sm._unfinished_thread is None, (
        'threshold override 3 must retire on the 3rd usage, not 2 (module constant)'
    )


# ── own_app dwell freeze (CR Major) ───────────────────────────────────


def test_own_app_freezes_dwell_timer_on_previous_window():
    """Brief glance at the catgirl app must NOT artificially extend
    the previous window's dwell.

    Scenario: user is in VS Code for 60s (below 90s focused_work
    threshold). They glance at N.E.K.O for 40s, then return to VS Code.
    Without the dwell freeze, total elapsed at return is 100s, which
    would trip focused_work even though actual VS Code time is only
    60s + ε. With the freeze, dwell-on-VS-Code at return ≈ 60s, still
    below threshold (correct).
    """
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    base = time.time()

    # t=0: VS Code first observation
    work = _sys_snap(title='proactive_chat.py - VS Code', process='Code.exe', ts=base)
    sm.update_system(work)
    sm.update_window(observation_from_system(work, prefs), now=base)
    sm.update_user_message(now=base)

    # t=80: still in VS Code, dwell ≈ 80s (below 90s threshold)
    sm.update_user_message(now=base + 80)
    snap_pre = sm.get_snapshot(now=base + 80)
    assert snap_pre.state in ('idle', 'focused_work')  # boundary case

    # t=85-130: 45s detour to N.E.K.O — own_app foreground
    own = _sys_snap(title='Project N.E.K.O', process='Xiao8.exe', ts=base + 85)
    sm.update_system(own)
    sm.update_window(observation_from_system(own, prefs), now=base + 85)
    # Multiple polls during own_app stretch (only first matters for freeze)
    sm.update_window(observation_from_system(own, prefs), now=base + 100)
    sm.update_window(observation_from_system(own, prefs), now=base + 120)

    # t=130: return to VS Code. Dwell-on-Code should be ~85s (= 80 + ε
    # before detour, then resumed), NOT 130s. Since 85 < 90, focused_work
    # must NOT have tripped from the brief detour alone.
    work_resume = _sys_snap(title='proactive_chat.py - VS Code', process='Code.exe', ts=base + 130)
    sm.update_system(work_resume)
    sm.update_window(observation_from_system(work_resume, prefs), now=base + 130)
    sm.update_user_message(now=base + 130)
    snap_post = sm.get_snapshot(now=base + 132)

    # Dwell should be roughly equivalent to time spent in VS Code only
    # (80 + 2 ≈ 82s), not full elapsed (132s). Threshold 90 not yet hit.
    dwell = base + 132 - sm._current_window_started_at
    assert dwell < 90, (
        f'dwell freeze must subtract own_app time; got {dwell:.1f}s '
        f'(would be ~132 without the freeze)'
    )
    # Same assertion expressed at the state-machine API level: the brief
    # own_app detour must NOT cause focused_work to fire on return.
    assert snap_post.state != 'focused_work', (
        f'dwell freeze should keep state below focused_work threshold '
        f'after a 45s own_app detour; got {snap_post.state}'
    )


# ── canonical fallback in loader (CR Minor) ───────────────────────────


def test_high_gpu_reason_uses_threshold_override():
    """``high_gpu`` reason must respect the same threshold as gaming-by-GPU.

    If the user lifts ``gaming_gpu_threshold_percent`` to 85, GPU at 70
    should NOT trigger gaming-by-GPU AND should NOT emit ``high_gpu``
    reason — both are 'is the GPU notable right now?' decisions and
    mustn't disagree.
    """
    prefs = ActivityPreferences(
        thresholds={'gaming_gpu_threshold_percent': 85.0},
    )
    sm = ActivityStateMachine(prefs=prefs)
    # GPU at 70 — between default 60 and overridden 85. With the fix,
    # neither classifier nor reason emitter should flag it.
    sn = _sys_snap(title='SomeUnknownApp', process='Other.exe', gpu=70.0)
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs))
    sm.update_user_message()

    snap = sm.get_snapshot()
    reason_codes = [r[0] for r in snap.propensity_reasons]
    assert 'high_gpu' not in reason_codes, (
        f'high_gpu reason must respect user threshold (85%); '
        f'got reasons={reason_codes} for GPU=70%'
    )
    assert snap.state != 'gaming', (
        f'gaming-by-GPU must respect threshold (85%); got state={snap.state}'
    )

    # Now push GPU above the override — both should fire
    sn2 = _sys_snap(title='SomeUnknownApp', process='Other.exe', gpu=90.0)
    sm.update_system(sn2)
    sm.update_window(observation_from_system(sn2, prefs))

    snap2 = sm.get_snapshot()
    reason_codes2 = [r[0] for r in snap2.propensity_reasons]
    assert 'high_gpu' in reason_codes2, (
        f'high_gpu reason should fire above override threshold (85%); '
        f'got reasons={reason_codes2} for GPU=90%'
    )


def test_loader_canonical_falls_back_to_override_key(tmp_path):
    """Doc says canonical defaults to override key when missing — verify."""
    from utils.activity_config import (
        _GLOBAL_CONVERSATION_KEY, _load_from_file,
    )
    pref_file = tmp_path / 'user_preferences.json'
    import json
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {
                'user_app_overrides': {
                    'MyCorpApp.exe': {'category': 'work'},  # no canonical
                },
                'user_title_overrides': {
                    'MyDashboard': {'category': 'work'},     # no canonical
                },
            },
        }]),
        encoding='utf-8',
    )
    prefs = _load_from_file(str(pref_file))
    assert prefs is not None
    # App override key gets lowercased for dict storage; canonical
    # preserves the original-case key value.
    assert 'mycorpapp.exe' in prefs.user_app_overrides
    assert prefs.user_app_overrides['mycorpapp.exe'].canonical == 'MyCorpApp.exe'
    # Title override falls back the same way
    assert 'mydashboard' in prefs.user_title_overrides
    assert prefs.user_title_overrides['mydashboard'].canonical == 'MyDashboard'


# ── Break-reminder accumulator + transition detection ──────────────


def _make_tracker_for_break_tests(
    *,
    work_break_minutes: float = 30,
    anti_slack_min_focus_minutes: float = 5,
    anti_slack_cooldown_minutes: float = 15,
):
    """Build a UserActivityTracker with break-reminder thresholds set.

    Bypasses collector startup — tests drive ``_tick_break_reminders``
    directly with synthetic ActivitySnapshots, so the collector + system
    signal pipeline never run.
    """
    from main_logic.activity.tracker import UserActivityTracker
    tracker = UserActivityTracker('test_lanlan')
    tracker._sm._prefs = ActivityPreferences(thresholds={
        'work_break_minutes': work_break_minutes,
        'anti_slack_min_focus_minutes': anti_slack_min_focus_minutes,
        'anti_slack_cooldown_minutes': anti_slack_cooldown_minutes,
    })
    return tracker


def _snap_for_state(state: str, *, app: str | None = 'VS Code'):
    """Minimal ActivitySnapshot with the fields _tick_break_reminders reads."""
    from main_logic.activity.snapshot import ActivitySnapshot, WindowObservation
    win = (
        WindowObservation(
            process_name=None, title=None, category='work',
            subcategory='ide', canonical=app, is_browser=False,
        )
        if app else None
    )
    return ActivitySnapshot(
        state=state,
        state_age_seconds=10.0,
        previous_state=None,
        transitioned_recently=False,
        stale_returning=False,
        propensity='open',
        active_window=win,
    )


def _tick_for_seconds(tracker, *, state: str, seconds: float, step: float = 20.0,
                     app: str | None = 'VS Code', start: float = 1000.0) -> float:
    """Drive _tick_break_reminders forward by ``seconds`` total via ``step`` slices.

    Per-tick advance is capped by ``_BREAK_REMINDER_TICK_MAX_DELTA_SECONDS``
    (default 30s); using 20s steps mirrors the real activity_guess loop
    cadence and keeps every step credited fully. Returns the final ``now``.
    """
    now = start
    end = start + seconds
    snap = _snap_for_state(state, app=app)
    # First tick records the baseline timestamp (no delta credited).
    tracker._tick_break_reminders(snap, now=now)
    while now < end:
        now = min(now + step, end)
        tracker._tick_break_reminders(snap, now=now)
    return now


@pytest.mark.unit
def test_context_prompt_reemits_after_session_baseline_reset():
    """跨 session 仍在同一状态时，reset 基线后应重新算作「进入」再置 pending。

    回归保护 Codex P2：tracker 跨 session 长存，若不在 session 开始清情境弹窗基线，
    「上个 session 在游戏、新 session 仍在游戏」就检测不到进入、漏弹。也确认这只动情境
    弹窗专属基线、状态保持期间不会重复触发。
    """
    tracker = _make_tracker_for_break_tests()

    # session A：进入 gaming → 置 play pending
    tracker._tick_break_reminders(_snap_for_state('gaming', app='Game'), now=1000.0)
    assert tracker._context_prompt_pending is not None
    assert tracker._context_prompt_pending['context'] == 'play'

    # 模拟 drain 消费掉；仍在 gaming → 状态保持，下一 tick 不重复触发
    tracker._context_prompt_pending = None
    tracker._tick_break_reminders(_snap_for_state('gaming', app='Game'), now=1020.0)
    assert tracker._context_prompt_pending is None

    # 新 session：清基线 → 仍在 gaming 也重新算「进入」→ 再置 play pending
    tracker.reset_context_prompt_baseline()
    tracker._tick_break_reminders(_snap_for_state('gaming', app='Game'), now=1040.0)
    assert tracker._context_prompt_pending is not None
    assert tracker._context_prompt_pending['context'] == 'play'


@pytest.mark.unit
def test_context_prompt_pending_cleared_on_leaving_target_state():
    """进游戏置 pending 后、还没 drain 就切到非目标态(idle) → 过期 pending 应被清掉。

    回归保护 Codex P2：pending 可能由 get_snapshot 路径（实验组 kick）置、还没等 loop
    drain，用户就离开了游戏/工作；不清就会把已离开的场景推成过期弹窗。
    """
    tracker = _make_tracker_for_break_tests()

    # 进 gaming → 置 play pending（模拟 get_snapshot 置、未 drain）
    tracker._tick_break_reminders(_snap_for_state('gaming', app='Game'), now=1000.0)
    assert tracker._context_prompt_pending is not None

    # 切到 idle（非目标态）→ 过期 pending 被清
    tracker._tick_break_reminders(_snap_for_state('idle', app=None), now=1020.0)
    assert tracker._context_prompt_pending is None


def test_break_acc_advances_during_focused_work():
    """Accumulator credits real time spent in focused_work."""
    tracker = _make_tracker_for_break_tests()
    _tick_for_seconds(tracker, state='focused_work', seconds=600)
    # 600s ± a tick — first call records timestamp without delta.
    assert 540 <= tracker._work_acc_seconds <= 610


def test_break_acc_extends_through_transitioning_when_already_started():
    """Transitioning during a real focus session keeps the timer running."""
    tracker = _make_tracker_for_break_tests()
    # Build up 5 minutes of focused_work.
    t = _tick_for_seconds(tracker, state='focused_work', seconds=300)
    pre_acc = tracker._work_acc_seconds
    assert pre_acc >= 280  # tolerance for first-tick init
    # Now flick to transitioning for 30 seconds — accumulator should keep growing.
    snap_transitioning = _snap_for_state('transitioning')
    tracker._tick_break_reminders(snap_transitioning, now=t + 20)
    tracker._tick_break_reminders(snap_transitioning, now=t + 40)
    assert tracker._work_acc_seconds > pre_acc


def test_break_acc_resets_on_any_other_state():
    """Anything other than focused_work / transitioning resets immediately."""
    tracker = _make_tracker_for_break_tests()
    _tick_for_seconds(tracker, state='focused_work', seconds=600)
    assert tracker._work_acc_seconds > 500

    # One tick of casual_browsing → reset
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=tracker._break_tick_last_at + 20)
    assert tracker._work_acc_seconds == 0


def test_break_acc_transitioning_alone_cannot_start_timer():
    """Pure transitioning (acc==0) does not arm the timer."""
    tracker = _make_tracker_for_break_tests()
    snap = _snap_for_state('transitioning')
    tracker._tick_break_reminders(snap, now=1000.0)
    tracker._tick_break_reminders(snap, now=1020.0)
    tracker._tick_break_reminders(snap, now=1040.0)
    assert tracker._work_acc_seconds == 0
    assert tracker._work_break_pending is None


def test_work_break_pending_armed_at_threshold():
    """work_break_pending populates once accumulator crosses threshold."""
    tracker = _make_tracker_for_break_tests(work_break_minutes=2)  # 120s for fast test
    _tick_for_seconds(tracker, state='focused_work', seconds=60)
    assert tracker._work_break_pending is None  # below threshold
    _tick_for_seconds(
        tracker, state='focused_work', seconds=120,
        start=tracker._break_tick_last_at,
    )
    assert tracker._work_break_pending is not None
    assert tracker._work_break_pending['minutes'] >= 2
    assert tracker._work_break_pending['app'] == 'VS Code'


def test_work_break_pending_persists_until_cleared():
    """Pending stays armed across ticks until mark_work_break_used."""
    tracker = _make_tracker_for_break_tests(work_break_minutes=1)
    _tick_for_seconds(tracker, state='focused_work', seconds=120)
    assert tracker._work_break_pending is not None
    # More focused_work ticks — pending stays, minutes refresh upward.
    base_minutes = tracker._work_break_pending['minutes']
    _tick_for_seconds(tracker, state='focused_work', seconds=120, start=tracker._break_tick_last_at)
    assert tracker._work_break_pending is not None
    assert tracker._work_break_pending['minutes'] >= base_minutes
    # Clearing via the public API resets accumulator + drops pending.
    tracker.mark_work_break_used()
    assert tracker._work_break_pending is None
    assert tracker._work_acc_seconds == 0


def test_anti_slack_pending_fires_on_focus_to_leisure():
    """Transitioning focused_work → casual_browsing arms anti-slack pending."""
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=2)
    # 3 minutes of focused_work
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=180)
    # Pivot to casual_browsing
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=end_t + 20)
    assert tracker._anti_slack_pending is not None
    assert tracker._anti_slack_pending['prev_app'] == 'VS Code'
    assert tracker._anti_slack_pending['new_app'] == 'YouTube'
    assert tracker._anti_slack_pending['minutes'] >= 2


def test_anti_slack_skipped_when_focus_too_short():
    """Below anti_slack_min_focus_minutes, the transition is silent."""
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=5)
    # Only 90s of focus
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=90)
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=end_t + 20)
    assert tracker._anti_slack_pending is None


def test_anti_slack_skipped_for_idle_target():
    """focused_work → idle does NOT fire (idle ≠ slacking; could be thinking)."""
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=2)
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=180)
    snap_idle = _snap_for_state('idle', app=None)
    tracker._tick_break_reminders(snap_idle, now=end_t + 20)
    assert tracker._anti_slack_pending is None


def test_anti_slack_respects_cooldown():
    """Within cooldown, no new anti-slack pending is emitted."""
    tracker = _make_tracker_for_break_tests(
        anti_slack_min_focus_minutes=2, anti_slack_cooldown_minutes=15,
    )
    # First focus → leisure: emits, then mark_used to start cooldown.
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=180)
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=end_t + 20)
    assert tracker._anti_slack_pending is not None
    tracker.mark_anti_slack_used(now=end_t + 30)

    # Second focus → leisure within cooldown: no pending.
    end_t2 = _tick_for_seconds(
        tracker, state='focused_work', seconds=180,
        start=end_t + 30,
    )
    snap_browsing2 = _snap_for_state('casual_browsing', app='Reddit')
    tracker._tick_break_reminders(snap_browsing2, now=end_t2 + 20)
    assert tracker._anti_slack_pending is None


def test_anti_slack_pending_cleared_on_return_to_focus():
    """If user goes back to focused_work, the pending is dropped (no longer slacking)."""
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=2)
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=180)
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=end_t + 20)
    assert tracker._anti_slack_pending is not None
    # Return to focused_work
    snap_back = _snap_for_state('focused_work', app='VS Code')
    tracker._tick_break_reminders(snap_back, now=end_t + 40)
    assert tracker._anti_slack_pending is None


def test_break_acc_tolerates_long_gaps():
    """Long gap between ticks doesn't credit the user fake minutes.

    If something pauses the loop (process suspend, idle deployment),
    the accumulator should not silently jump 30 minutes — the cap
    discards the suspect delta.
    """
    tracker = _make_tracker_for_break_tests()
    snap = _snap_for_state('focused_work')
    tracker._tick_break_reminders(snap, now=1000.0)
    # Tick again with a 1-hour gap → should be discarded
    tracker._tick_break_reminders(snap, now=1000.0 + 3600.0)
    # Accumulator stays at 0 (or near it) — the 3600s delta exceeds the cap
    assert tracker._work_acc_seconds < 60
    # Subsequent normal ticks credit normally
    tracker._tick_break_reminders(snap, now=1000.0 + 3620.0)
    assert tracker._work_acc_seconds > 0
    assert tracker._work_acc_seconds < 60


def test_anti_slack_uses_accumulator_not_wall_clock_after_suspend():
    """Long suspend → resume → leisure does NOT inflate session minutes.

    Regression test for Codex P1 (PR #1226): without the fix,
    ``session_seconds`` was computed from ``now - session_started_at``,
    so a 1-hour laptop sleep mid-focus session would emit anti-slack
    pending claiming the user worked for 60+ minutes when the
    accumulator only credited the genuine pre-sleep focus time.
    """
    # Set anti-slack threshold high enough that pre-sleep alone wouldn't
    # trip it, so any inflation would be detected as a false positive.
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=5)
    # Pre-sleep: only 1 minute of real focused_work (well below threshold).
    snap_focus = _snap_for_state('focused_work')
    tracker._tick_break_reminders(snap_focus, now=1000.0)
    tracker._tick_break_reminders(snap_focus, now=1020.0)
    tracker._tick_break_reminders(snap_focus, now=1040.0)
    tracker._tick_break_reminders(snap_focus, now=1060.0)
    pre_sleep_acc = tracker._work_acc_seconds
    assert pre_sleep_acc < 100  # ~60s of real focus
    # 1 hour of nothing — process suspended.
    suspended_until = 1060.0 + 3600.0
    # Resume: still in focused_work briefly (long delta gets discarded).
    tracker._tick_break_reminders(snap_focus, now=suspended_until)
    # Accumulator should NOT have ballooned — long gap was discarded.
    assert tracker._work_acc_seconds < 100
    # User immediately switches to YouTube post-resume.
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=suspended_until + 20.0)
    # No anti-slack pending — real focused minutes were below threshold,
    # wall-clock-based logic would have spuriously triggered with 61min.
    assert tracker._anti_slack_pending is None


def test_break_acc_resets_when_long_gap_post_state_is_leisure():
    """Long gap that lands on a leisure tick still resets accumulator.

    Codex P2 (PR #1226): the original gap-discard branch only ran the
    state-handling block when raw_delta was in range, so when a long
    gap landed on a casual_browsing/gaming tick the ``acc=0`` reset
    never executed and pre-gap focus minutes carried forward into the
    next focused_work stretch — triggering water_break_pending much
    earlier than 30 fresh minutes would warrant.
    """
    tracker = _make_tracker_for_break_tests(work_break_minutes=30)
    # Build up 25 minutes of pre-gap focus.
    _tick_for_seconds(tracker, state='focused_work', seconds=1500)
    pre_gap_acc = tracker._work_acc_seconds
    assert pre_gap_acc >= 1400  # generous lower bound for tick init
    # Long gap (1 hour suspend) lands on a casual_browsing tick.
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(
        snap_browsing, now=tracker._break_tick_last_at + 3600.0,
    )
    # Accumulator must have reset — otherwise the next focused_work
    # stretch would inherit 25 pre-gap minutes.
    assert tracker._work_acc_seconds == 0


def test_long_gap_does_not_fire_anti_slack_on_first_post_gap_leisure():
    """Long gap into leisure shouldn't claim the user just finished focusing.

    Codex P2: prev_known carries focused_work across the gap with
    stale session_started_at; without the long-gap reset, the
    anti-slack branch would fire claiming the user worked the entire
    gap — but we have no idea whether they were focused, away, or
    sleeping.
    """
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=2)
    _tick_for_seconds(tracker, state='focused_work', seconds=600)
    # Long gap then leisure tick
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(
        snap_browsing, now=tracker._break_tick_last_at + 3600.0,
    )
    # No anti-slack pending — gap-induced reset cleared session bookkeeping.
    assert tracker._anti_slack_pending is None


def test_clock_rollback_resets_accumulator():
    """Non-monotonic tick (NTP rollback / duplicate ts) clears stale focus.

    Codex P2 (PR #1226): symmetric to the long-gap branch — without
    the reset, a post-rollback focused_work tick would inherit
    pre-rollback minutes and trip water_break early. Also verifies
    that the unsafe-delta branch treats the post-rollback focused_work
    state as a fresh session entry (session_started_at refreshes).
    """
    tracker = _make_tracker_for_break_tests()
    snap_focus = _snap_for_state('focused_work')
    # Build up some focus
    tracker._tick_break_reminders(snap_focus, now=1000.0)
    tracker._tick_break_reminders(snap_focus, now=1020.0)
    tracker._tick_break_reminders(snap_focus, now=1040.0)
    assert tracker._work_acc_seconds > 0
    pre_rollback_session_start = tracker._focused_work_session_started_at
    assert pre_rollback_session_start is not None
    # NTP rollback — wall clock goes backward
    tracker._tick_break_reminders(snap_focus, now=900.0)
    assert tracker._work_acc_seconds == 0
    # session_started_at refreshed to the post-rollback time, proving
    # the bookkeeping branch saw prev_known==None and treated this as a
    # fresh entry (rather than carrying the pre-rollback session).
    assert tracker._focused_work_session_started_at == 900.0
    # Duplicate timestamp (raw_delta == 0) also gets the reset
    tracker._tick_break_reminders(snap_focus, now=1100.0)
    tracker._tick_break_reminders(snap_focus, now=1100.0)
    # First call credits delta, second call has raw_delta=0 → reset
    # (we don't differentiate "0 is harmless" from "<0 is dangerous";
    # both fall outside the `0 < raw_delta` window so both reset)
    assert tracker._work_acc_seconds == 0


def test_long_gap_clears_work_break_pending_too():
    """Pre-gap water_break_pending shouldn't persist across long gaps."""
    tracker = _make_tracker_for_break_tests(work_break_minutes=2)
    _tick_for_seconds(tracker, state='focused_work', seconds=200)
    assert tracker._work_break_pending is not None
    # Long gap lands on focused_work tick → still resets
    snap_focus = _snap_for_state('focused_work', app='VS Code')
    tracker._tick_break_reminders(
        snap_focus, now=tracker._break_tick_last_at + 3600.0,
    )
    assert tracker._work_break_pending is None
    assert tracker._work_acc_seconds == 0


def test_anti_slack_minutes_match_accumulator_when_no_gap():
    """When anti-slack fires from a continuous focus → leisure transition,
    reported minutes track the accumulator (not the wall-clock).

    Originally pinned the Codex P1 fix (accumulator vs wall-clock) using a
    suspend gap; the later Codex P2 fix made long-gap transitions skip
    anti-slack entirely, so this case now exercises the no-gap path.
    Minutes-match logic remains relevant for normal continuous sessions.
    """
    tracker = _make_tracker_for_break_tests(anti_slack_min_focus_minutes=5)
    # 6 minutes of continuous focused_work
    end_t = _tick_for_seconds(tracker, state='focused_work', seconds=360)
    real_focus_minutes = int(tracker._work_acc_seconds / 60)
    assert real_focus_minutes >= 5
    # Pivot to YouTube on the very next tick (no gap)
    snap_browsing = _snap_for_state('casual_browsing', app='YouTube')
    tracker._tick_break_reminders(snap_browsing, now=end_t + 20.0)
    assert tracker._anti_slack_pending is not None
    # Reported minutes track the accumulator value at tick start, not
    # ``now - session_started_at``.
    reported = tracker._anti_slack_pending['minutes']
    assert reported <= real_focus_minutes + 1  # ±1 for rounding


def test_unit_probability_rejects_out_of_range():
    """Out-of-range probabilities → None (use default), not clamped.

    Codex P2 (PR #1226): docstring contract says invalid → None,
    consistent with the rest of activity_config's fail-soft parsers.
    Without this, a typo like ``2`` would become ``always invite``.
    """
    from utils.activity_config import _parse_unit_probability
    assert _parse_unit_probability(0.5) == 0.5
    assert _parse_unit_probability(0) == 0.0
    assert _parse_unit_probability(1) == 1.0
    # Out of range → None (not clamped)
    assert _parse_unit_probability(2) is None
    assert _parse_unit_probability(-0.1) is None
    assert _parse_unit_probability(1.5) is None
    # Non-numeric / None / bool still rejected
    assert _parse_unit_probability(None) is None
    assert _parse_unit_probability('0.5') is None
    assert _parse_unit_probability(True) is None
    # NaN / Inf must fall through too — NaN comparisons are always False so
    # the range checks alone don't catch it. CodeRabbit Minor: PR #1226.
    assert _parse_unit_probability(float('nan')) is None
    assert _parse_unit_probability(float('inf')) is None
    assert _parse_unit_probability(float('-inf')) is None


def test_loader_wires_work_break_game_invite_probability(tmp_path):
    """File → _load_from_file → ActivityPreferences carries the new field.

    Pins the JSON loader path end-to-end so renaming the key or breaking
    ``_parse_activity_section`` wiring fails loud here, not silently
    falls back to the code default. CodeRabbit Nitpick: PR #1226.
    """
    import json
    from utils.activity_config import _GLOBAL_CONVERSATION_KEY, _load_from_file

    pref_file = tmp_path / 'user_preferences.json'
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {
                'work_break_game_invite_probability': 0.25,
            },
        }]),
        encoding='utf-8',
    )
    prefs = _load_from_file(str(pref_file))
    assert prefs is not None
    assert prefs.work_break_game_invite_probability == 0.25

    # Negative case: invalid value falls through to None (uses code default).
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {
                'work_break_game_invite_probability': 2,  # out of range
            },
        }]),
        encoding='utf-8',
    )
    prefs = _load_from_file(str(pref_file))
    assert prefs is not None
    assert prefs.work_break_game_invite_probability is None

    # Missing field → None (default).
    pref_file.write_text(
        json.dumps([{
            'model_path': _GLOBAL_CONVERSATION_KEY,
            'activity': {},
        }]),
        encoding='utf-8',
    )
    prefs = _load_from_file(str(pref_file))
    assert prefs is not None
    assert prefs.work_break_game_invite_probability is None


# ── focused_video (immersive video watching) ────────────────────────
#
# Placed at the end of the file deliberately: the docstring-cjk ratchet
# (scripts/check_docstring_no_cjk.py) flags CJK docstrings whose line span
# overlaps diff-added lines. ``test_witty_quality_bar_renders`` above carries
# an intentional CJK docstring; appending here keeps it out of this change's
# diff range so it isn't re-flagged.


def _video_prefs(*, focused=100, casual=10) -> ActivityPreferences:
    """ActivityPreferences classifying player.exe as entertainment/video.

    Uses a small focused_video / casual_browsing dwell threshold so tests
    can cross them with modest fabricated time deltas.
    """
    return ActivityPreferences(
        user_app_overrides={
            'player.exe': _AppOverride(
                category='entertainment', subcategory='video', canonical='Player',
            ),
            'editor.exe': _AppOverride(
                category='work', subcategory='ide', canonical='Editor',
            ),
        },
        thresholds={
            'focused_video_min_dwell_seconds': focused,
            'casual_browsing_min_dwell_seconds': casual,
        },
    )


def test_focused_video_propensity_and_tone():
    """focused_video reuses restricted_screen_only propensity + witty tone.

    Both are deliberate reuse (no new propensity / tone added): the
    propensity cuts music/meme/web while keeping screen snark, and witty
    carries the same "[PASS] if you have nothing funny" quality bar as
    casual_browsing.
    """
    assert derive_propensity('focused_video') == 'restricted_screen_only'
    assert derive_tone('focused_video') == 'witty'


def test_focused_video_fires_after_sustained_dwell():
    """Video window below the dwell bar stays casual_browsing; past it flips.

    The high threshold is the "don't trip on a freshly opened clip" gate.
    """
    prefs = _video_prefs(focused=100, casual=10)
    sm = ActivityStateMachine(prefs=prefs)
    video = _sys_snap(title='Long Movie', process='player.exe')
    t0 = 1000.0
    sm.update_system(video)
    sm.update_window(observation_from_system(video, prefs), now=t0)

    # Below focused_video threshold → casual_browsing (open) — just a clip.
    early = sm.get_snapshot(now=t0 + 50)
    assert early.state == 'casual_browsing'
    assert early.propensity == 'open'

    # Past threshold → focused_video (restricted + witty).
    late = sm.get_snapshot(now=t0 + 120)
    assert late.state == 'focused_video'
    assert late.propensity == 'restricted_screen_only'
    assert late.tone == 'witty'
    codes = [code for code, _ in late.propensity_reasons]
    assert 'state_focused_video' in codes


def test_focused_video_dwell_resets_on_window_switch():
    """Continuity: switching to a non-video window resets the dwell clock.

    Watching a video, flicking to a work window, then back to the video
    restarts the dwell timer — so the user doesn't slide into focused_video
    on accumulated-but-interrupted watch time. This is what keeps
    "work while a video plays in the corner" on the open (casual) side.
    """
    prefs = _video_prefs(focused=100, casual=10)
    sm = ActivityStateMachine(prefs=prefs)
    video = _sys_snap(title='Ep 1', process='player.exe')
    work = _sys_snap(title='main.py', process='editor.exe')
    t0 = 1000.0

    # Watch video, approaching the focused_video threshold but not crossing.
    sm.update_system(video)
    sm.update_window(observation_from_system(video, prefs), now=t0)
    assert sm.get_snapshot(now=t0 + 80).state == 'casual_browsing'

    # Flick to a work window (dwell resets), then back to the video.
    sm.update_window(observation_from_system(work, prefs), now=t0 + 90)
    sm.update_system(video)
    sm.update_window(observation_from_system(video, prefs), now=t0 + 100)

    # Only 50s on the video since switching back → still casual_browsing,
    # even though absolute time since t0 (150s) is well past the threshold.
    snap = sm.get_snapshot(now=t0 + 150)
    assert snap.state == 'casual_browsing'
    assert snap.propensity == 'open'


def test_non_video_entertainment_never_upgrades_to_focused_video():
    """Non-video entertainment (social/etc) stays casual_browsing forever.

    Only subcategory video/live can reach focused_video; other
    entertainment dwell, however long, stays on the open side.
    """
    prefs = ActivityPreferences(
        user_app_overrides={
            'social.exe': _AppOverride(
                category='entertainment', subcategory='social', canonical='SocialApp',
            ),
        },
        thresholds={
            'focused_video_min_dwell_seconds': 100,
            'casual_browsing_min_dwell_seconds': 10,
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    sn = _sys_snap(title='Feed', process='social.exe')
    t0 = 1000.0
    sm.update_system(sn)
    sm.update_window(observation_from_system(sn, prefs), now=t0)

    snap = sm.get_snapshot(now=t0 + 500)  # way past focused_video threshold
    assert snap.state == 'casual_browsing'
    assert snap.propensity == 'open'


def test_focused_video_default_dwell_threshold_is_120():
    """Default FOCUSED_VIDEO_MIN_DWELL_SECONDS is 120s (2 min)."""
    from main_logic.activity.state_machine import FOCUSED_VIDEO_MIN_DWELL_SECONDS

    assert FOCUSED_VIDEO_MIN_DWELL_SECONDS == 120.0

    # No threshold override → the instance picks up the code default.
    prefs = ActivityPreferences(
        user_app_overrides={
            'player.exe': _AppOverride(
                category='entertainment', subcategory='video', canonical='Player',
            ),
        },
    )
    sm = ActivityStateMachine(prefs=prefs)
    assert sm._focused_video_min_dwell_seconds == 120.0

    video = _sys_snap(title='Cool Video', process='player.exe')
    t0 = 1000.0
    sm.update_system(video)
    sm.update_window(observation_from_system(video, prefs), now=t0)
    # Just under the 120s default → casual_browsing (30s default already cleared).
    assert sm.get_snapshot(now=t0 + 119).state == 'casual_browsing'
    # Just past it → focused_video.
    assert sm.get_snapshot(now=t0 + 121).state == 'focused_video'
