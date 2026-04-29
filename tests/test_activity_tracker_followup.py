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

import time

import pytest

from main_logic.activity.snapshot import (
    derive_skip_probability,
    derive_tone,
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
    # Override DOES change the WindowObservation (user said so), but
    # classification then runs through static DB first via the keyword
    # match — except in our current implementation user override wins.
    # The intentional behaviour: keyword DB hit is checked FIRST, then
    # user override patches. So static private match wins. Confirm.
    # NOTE: depending on implementation order this may be the inverse;
    # the test pins the safer (privacy-preserving) ordering.
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
    ('idle',            None,           None,     'concise'),
    ('away',            None,           None,     'concise'),
    ('casual_browsing', None,           None,     'playful'),
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


# ── #1 / skip_probability ───────────────────────────────────────────


def test_skip_probability_defaults():
    """Pin the default skip-probability table."""
    # Non-gaming → always 0
    assert derive_skip_probability('focused_work') == 0.0
    assert derive_skip_probability('chatting') == 0.0
    assert derive_skip_probability('idle') == 0.0

    # Gaming defaults
    assert derive_skip_probability('gaming', game_intensity='competitive') == pytest.approx(0.3)
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
        tracker._refresh_prefs()
        # Now classify with the post-swap prefs
        obs2 = observation_from_system(sn, tracker._sm._prefs)
        assert obs2.category == 'work'
        assert obs2.canonical == 'SomeUnknownApp'
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
    third call (not the second — the module constant default)."""
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
