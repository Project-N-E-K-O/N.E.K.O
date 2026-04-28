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
    ('Vaultwarden Web Vault', 'chrome.exe'),  # title-only match
    ('Ledger Live - Portfolio', 'Ledger Live.exe'),
])
def test_privacy_classification_emits_private_state(title, process):
    """Sensitive apps classify as state='private', propensity='closed'."""
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


def test_own_app_suppresses_gpu_fallback_gaming():
    """Catgirl rendering its own Live2D/VRM should NOT trip gaming-by-GPU."""
    prefs = ActivityPreferences()
    sm = ActivityStateMachine(prefs=prefs)

    # High GPU + own app foreground — would normally trip gaming-by-GPU
    # if ``unknown`` category, but own_app is filtered upstream so the
    # GPU fallback branch never sees this observation.
    own = _sys_snap(title='N.E.K.O', process='projectneko_server.exe', gpu=85.0)
    sm.update_system(own)
    sm.update_window(observation_from_system(own, prefs))
    sm.update_user_message()
    snap = sm.get_snapshot()

    # Without any prior observation, default state is 'idle' — the key
    # assertion is "NOT gaming".
    assert snap.state != 'gaming'


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
        _CacheState, _GLOBAL_CONVERSATION_KEY, _load_from_file,
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
    import utils.activity_config as ac_mod
    original = ac_mod._cache.prefs
    try:
        ac_mod._cache.prefs = new_prefs
        tracker._refresh_prefs()
        # Now classify with the post-swap prefs
        obs2 = observation_from_system(sn, tracker._sm._prefs)
        assert obs2.category == 'work'
        assert obs2.canonical == 'SomeUnknownApp'
    finally:
        ac_mod._cache.prefs = original
