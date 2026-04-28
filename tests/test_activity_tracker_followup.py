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
    ActivitySnapshot,
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
