"""Broadcast preferences: category and lane switches, and the quiet window."""

from __future__ import annotations

import asyncio
import threading

import pytest

from plugin.sdk.plugin import Err, Ok, SdkError
from plugin.plugins.neko_wows import NekoWowsPlugin
from plugin.plugins.neko_wows.detectors._base import GameEvent
from plugin.plugins.neko_wows.domain.catalog import (
    DAMAGE_MILESTONE,
    LOW_HEALTH,
    OWN_SHIP_SUNK,
    PRIORITY_TARGET,
    spec_for,
)
from plugin.plugins.neko_wows.domain.contracts import (
    ALL_CATEGORIES,
    ALL_INTRUSION_MODES,
    CATEGORY_PROGRESS,
    CATEGORY_SURVIVAL,
    CHANNEL_SINGLE,
    INTRUSION_ALLOW_INTERRUPT,
    INTRUSION_CRITICAL_ONLY,
    INTRUSION_NO_INTERRUPT,
    LANE_NORMAL,
    LANE_URGENT,
    WowsConfig,
)
from plugin.plugins.neko_wows.domain.facts import WowsFacts
from plugin.plugins.neko_wows.policy.arbiter import (
    Arbiter,
    REASON_QUIET_WINDOW,
)
from plugin.plugins.neko_wows.policy.tactic_policy import (
    AdviceCandidate,
    WowsTacticPolicy,
)


def facts(at=100.0):
    return WowsFacts(seq=1, at=at, battle_id="b-1", own_hp_ratio=0.4)


def event(event_id, at=100.0):
    return GameEvent(
        event_id=event_id, severity=70, at=at, seq=1, battle_id="b-1", detail={})


def candidate(event_id, *, at=100.0, cfg=None):
    settings = cfg or WowsConfig()
    spec = spec_for(event_id)
    return AdviceCandidate(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority,
        severity=70,
        at=at,
        seq=1,
        battle_id="b-1",
        summary=spec.summary,
        expires_at=at + settings.ttl_for(spec.lane),
    )


def outcomes(decision, event_id):
    return [step.outcome for step in decision.chain if step.event_id == event_id]


class _ConfigSource:
    def __init__(self, dry_run):
        self.dry_run = dry_run

    async def dump(self):
        return {"neko_wows": {"dry_run": self.dry_run}}


class _ReloadTarget:
    def __init__(self, *, current, configured):
        self.cfg = WowsConfig()
        self.cfg.dry_run = current
        self.config = _ConfigSource(configured)
        self._preference_lock = asyncio.Lock()
        self._pipeline_lock = threading.RLock()
        self.logger = type("Logger", (), {"warning": lambda *_: None})()

    async def _apply_stored_preferences(self, _cfg):
        return None

    def _apply_config(self, cfg):
        self.cfg = cfg


def test_startup_config_reload_always_forces_dry_run():
    target = _ReloadTarget(current=False, configured=False)
    cfg = asyncio.run(
        NekoWowsPlugin._reload_config(target, force_dry_run=True))
    assert cfg.dry_run is True


def test_hot_reload_preserves_the_explicit_session_dry_run_choice():
    target = _ReloadTarget(current=False, configured=True)
    cfg = asyncio.run(NekoWowsPlugin._reload_config(target))
    assert cfg.dry_run is False


# --- category and lane switches ------------------------------------------

def test_every_catalog_category_is_a_known_preference():
    """A category the panel cannot show is a category nobody can turn off."""
    from plugin.plugins.neko_wows.domain.catalog import EVENT_CATALOG

    used = {spec.coalesce_key for spec in EVENT_CATALOG.values()}
    assert used <= set(ALL_CATEGORIES)


def test_a_disabled_category_never_becomes_a_candidate():
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_SURVIVAL]})
    policy = WowsTacticPolicy(cfg)
    candidates = policy.expand(
        [event(LOW_HEALTH), event(DAMAGE_MILESTONE)], facts())
    assert [item.event_id for item in candidates] == [DAMAGE_MILESTONE]


def test_disabling_one_category_leaves_the_others_alone():
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_PROGRESS]})
    candidates = WowsTacticPolicy(cfg).expand(
        [event(LOW_HEALTH), event(DAMAGE_MILESTONE)], facts())
    assert [item.event_id for item in candidates] == [LOW_HEALTH]


def test_a_disabled_lane_drops_every_event_in_it():
    cfg = WowsConfig.from_mapping({"disabled_lanes": [LANE_URGENT]})
    candidates = WowsTacticPolicy(cfg).expand(
        [event(OWN_SHIP_SUNK), event(LOW_HEALTH), event(PRIORITY_TARGET)], facts())
    assert [item.event_id for item in candidates] == [PRIORITY_TARGET]


def test_disabled_events_do_not_consume_a_cooldown_slot():
    """Filtering in policy, not delivery, is what makes this true."""
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_SURVIVAL]})
    policy = WowsTacticPolicy(cfg)
    arbiter = Arbiter(cfg)

    decision = arbiter.decide(policy.expand([event(LOW_HEALTH)], facts()), 100.0)
    assert decision.chosen is None
    assert arbiter.stats()["queued"] == 0
    assert arbiter.stats()["cooldowns"] == 0


def test_unknown_preference_names_are_ignored():
    cfg = WowsConfig.from_mapping({
        "disabled_categories": ["not_a_category", CATEGORY_SURVIVAL],
        "disabled_lanes": ["sideways", LANE_NORMAL],
    })
    assert cfg.disabled_categories == (CATEGORY_SURVIVAL,)
    assert cfg.disabled_lanes == (LANE_NORMAL,)


def test_a_non_list_preference_disables_nothing():
    cfg = WowsConfig.from_mapping({"disabled_categories": "wows_survival"})
    assert cfg.disabled_categories == ()


def test_lane_and_category_helpers_agree_with_the_config():
    cfg = WowsConfig.from_mapping({
        "disabled_categories": [CATEGORY_SURVIVAL],
        "disabled_lanes": [LANE_URGENT],
    })
    assert cfg.category_enabled(CATEGORY_SURVIVAL) is False
    assert cfg.category_enabled(CATEGORY_PROGRESS) is True
    assert cfg.lane_enabled(LANE_URGENT) is False
    assert cfg.lane_enabled(LANE_NORMAL) is True


# --- intrusion policy ----------------------------------------------------

def test_the_default_policy_is_critical_only():
    assert WowsConfig().dialogue_intrusion_mode == INTRUSION_CRITICAL_ONLY


def test_an_unknown_mode_falls_back_to_critical_only():
    cfg = WowsConfig.from_mapping({"dialogue_intrusion_mode": "whatever"})
    assert cfg.dialogue_intrusion_mode == INTRUSION_CRITICAL_ONLY


def test_all_modes_are_accepted():
    for mode in ALL_INTRUSION_MODES:
        cfg = WowsConfig.from_mapping({"dialogue_intrusion_mode": mode})
        assert cfg.dialogue_intrusion_mode == mode


def test_no_interrupt_holds_back_even_an_urgent_call_out():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    decision = arbiter.decide([candidate(OWN_SHIP_SUNK, at=110.0, cfg=cfg)], 110.0)
    assert decision.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(decision, OWN_SHIP_SUNK)


def test_critical_only_lets_urgent_through_but_holds_normal():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_CRITICAL_ONLY,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    normal = arbiter.decide([candidate(DAMAGE_MILESTONE, at=110.0, cfg=cfg)], 110.0)
    assert normal.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(normal, DAMAGE_MILESTONE)

    urgent = arbiter.decide([candidate(LOW_HEALTH, at=111.0, cfg=cfg)], 111.0)
    assert urgent.chosen is not None
    assert urgent.chosen.lane == LANE_URGENT


def test_allow_interrupt_ignores_the_window_entirely():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_ALLOW_INTERRUPT,
        "user_chat_quiet_window_seconds": 600.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    decision = arbiter.decide([candidate(DAMAGE_MILESTONE, at=101.0, cfg=cfg)], 101.0)
    assert decision.chosen is not None


def test_the_window_expires_on_its_own():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 30.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    blocked = arbiter.decide([candidate(LOW_HEALTH, at=120.0, cfg=cfg)], 120.0)
    assert blocked.chosen is None

    later = 100.0 + 31.0
    reopened = arbiter.decide([candidate(LOW_HEALTH, at=later, cfg=cfg)], later)
    assert reopened.chosen is not None


def test_a_zero_length_window_never_suppresses():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 0.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    assert arbiter.decide(
        [candidate(LOW_HEALTH, at=100.0, cfg=cfg)], 100.0).chosen is not None


def test_the_reason_says_it_was_the_plugin_that_suppressed():
    """The host has its own gate; the two must be distinguishable."""
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    decision = arbiter.decide([candidate(LOW_HEALTH, at=110.0, cfg=cfg)], 110.0)
    detail = next(
        step.detail for step in decision.chain
        if step.outcome == REASON_QUIET_WINDOW)
    assert "插件静默窗口" in detail
    assert INTRUSION_NO_INTERRUPT in detail


def test_clearing_the_window_reopens_output_immediately():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 600.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    arbiter.clear_quiet_window()
    assert arbiter.decide(
        [candidate(LOW_HEALTH, at=101.0, cfg=cfg)], 101.0).chosen is not None


def test_the_window_is_reported_for_the_panel():
    cfg = WowsConfig.from_mapping({"user_chat_quiet_window_seconds": 45.0})
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    stats = arbiter.stats()
    assert stats["quiet_until"] == pytest.approx(145.0)
    assert stats["intrusion_mode"] == cfg.dialogue_intrusion_mode


def test_a_held_candidate_stays_queued_for_when_the_window_closes():
    """Suppression is not a drop: the queue keeps it until its TTL runs out."""
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 4.0,
        "urgent_ttl_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    held = arbiter.decide([candidate(LOW_HEALTH, at=101.0, cfg=cfg)], 101.0)
    assert held.chosen is None
    assert arbiter.stats()["queued"] == 1

    released = arbiter.decide([], 106.0)
    assert released.chosen is not None
    assert released.chosen.event_id == LOW_HEALTH


# --- timing overrides ----------------------------------------------------

def test_timing_overrides_take_effect_through_the_shared_config():
    cfg = WowsConfig()
    arbiter = Arbiter(cfg)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0, cfg=cfg)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    # Default urgent gap is 6s, so this would normally be blocked.
    cfg.urgent_min_gap_seconds = 0.0
    arbiter.apply_config(cfg)
    from plugin.plugins.neko_wows.domain.catalog import ENEMY_CLOSING

    reopened = arbiter.decide([candidate(ENEMY_CLOSING, at=101.0, cfg=cfg)], 101.0)
    assert reopened.chosen is not None


# --- live action atomicity -----------------------------------------------

class _TrackingLock:
    def __init__(self):
        self._lock = threading.RLock()
        self.depth = 0

    @property
    def held(self):
        return self.depth > 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.depth -= 1
        self._lock.release()


class _GuardedConfig(WowsConfig):
    _GUARDED_FIELDS = frozenset({
        "channel_mode",
        "dialogue_intrusion_mode",
        "user_chat_quiet_window_seconds",
        "disabled_categories",
        "disabled_lanes",
        "urgent_ttl_seconds",
        "urgent_min_gap_seconds",
        "normal_ttl_seconds",
        "normal_min_gap_seconds",
    })

    def attach_guard(self, lock):
        self._guard_lock = lock

    def __setattr__(self, name, value):
        lock = getattr(self, "_guard_lock", None)
        if lock is not None and name in self._GUARDED_FIELDS:
            assert lock.held, f"{name} changed outside _pipeline_lock"
        super().__setattr__(name, value)


class _GuardedDependency:
    def __init__(self, lock, *, enforce=True):
        self.lock = lock
        self.enforce = enforce
        self.calls = []

    def _record(self, name, *args):
        if self.enforce:
            assert self.lock.held, f"{name} called outside _pipeline_lock"
        self.calls.append((name, args))

    def apply_config(self, cfg):
        self._record("apply_config", cfg)

    def pause(self, *args):
        self._record("pause", *args)

    def resume(self):
        self._record("resume")

    def note_user_activity(self, at):
        self._record("note_user_activity", at)


class _ActionStore:
    def __init__(self, outcome=Ok(None), *, raises=None):
        self.outcome = outcome
        self.raises = raises
        self.calls = []

    async def set(self, key, value):
        self.calls.append((key, value))
        if self.raises is not None:
            raise self.raises
        return self.outcome


class _BlockingActionStore:
    def __init__(self):
        self.calls = []
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def set(self, key, value):
        self.calls.append((key, value))
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()
        return Ok(None)


class _ActionTimeline:
    def record(self, *_args, **_kwargs):
        return None


def _action_target(*, guarded=True, store=None):
    target = object.__new__(NekoWowsPlugin)
    target._preference_lock = asyncio.Lock()
    target._pipeline_lock = _TrackingLock()
    target.cfg = _GuardedConfig() if guarded else WowsConfig()
    if guarded:
        target.cfg.attach_guard(target._pipeline_lock)
    target.router = _GuardedDependency(target._pipeline_lock, enforce=guarded)
    target.policy = _GuardedDependency(target._pipeline_lock, enforce=guarded)
    target.arbiter = _GuardedDependency(target._pipeline_lock, enforce=guarded)
    target.dispatcher = _GuardedDependency(target._pipeline_lock, enforce=guarded)
    target.service = _GuardedDependency(target._pipeline_lock, enforce=guarded)
    target.timeline = _ActionTimeline()
    target.store = store or _ActionStore()
    target.logger = type("Logger", (), {"warning": lambda *_args: None})()
    return target


@pytest.mark.parametrize(("action", "kwargs", "expected"), [
    ("set_channel_mode", {"mode": CHANNEL_SINGLE},
     {"channel_mode": CHANNEL_SINGLE}),
    ("set_intrusion_mode", {
        "mode": INTRUSION_NO_INTERRUPT,
        "quiet_window_seconds": 25.0,
    }, {
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 25.0,
    }),
    ("set_category_enabled", {
        "category": CATEGORY_SURVIVAL,
        "enabled": False,
    }, {"disabled_categories": (CATEGORY_SURVIVAL,)}),
    ("set_lane_enabled", {
        "lane": LANE_URGENT,
        "enabled": False,
    }, {"disabled_lanes": (LANE_URGENT,)}),
    ("set_lane_timing", {
        "lane": LANE_URGENT,
        "ttl_seconds": 15.0,
        "min_gap_seconds": 3.0,
    }, {"urgent_ttl_seconds": 15.0, "urgent_min_gap_seconds": 3.0}),
])
def test_live_preference_actions_change_runtime_only_under_pipeline_lock(
    action, kwargs, expected,
):
    target = _action_target()

    result = asyncio.run(getattr(target, action)(**kwargs))

    assert result.is_ok()
    for field, value in expected.items():
        assert getattr(target.cfg, field) == value


@pytest.mark.parametrize(("action", "kwargs", "unchanged"), [
    ("set_channel_mode", {"mode": CHANNEL_SINGLE},
     {"channel_mode": "dual"}),
    ("set_intrusion_mode", {
        "mode": INTRUSION_NO_INTERRUPT,
        "quiet_window_seconds": 25.0,
    }, {
        "dialogue_intrusion_mode": INTRUSION_CRITICAL_ONLY,
        "user_chat_quiet_window_seconds": 60.0,
    }),
    ("set_category_enabled", {
        "category": CATEGORY_SURVIVAL,
        "enabled": False,
    }, {"disabled_categories": ()}),
    ("set_lane_enabled", {
        "lane": LANE_URGENT,
        "enabled": False,
    }, {"disabled_lanes": ()}),
])
@pytest.mark.parametrize("store", [
    _ActionStore(Err(SdkError("disk"))),
    _ActionStore(raises=RuntimeError("disk")),
])
def test_persistent_preference_failure_keeps_runtime_unchanged(
    action, kwargs, unchanged, store,
):
    target = _action_target(guarded=False, store=store)

    result = asyncio.run(getattr(target, action)(**kwargs))

    assert result.is_err()
    for field, value in unchanged.items():
        assert getattr(target.cfg, field) == value


def test_pause_resume_and_chat_activity_are_pipeline_state_transitions():
    target = _action_target()

    assert asyncio.run(target.pause()).is_ok()
    assert asyncio.run(target.resume()).is_ok()
    assert target.on_chat_message().is_ok()


def test_persistent_preference_actions_are_serialized():
    async def scenario():
        store = _BlockingActionStore()
        target = _action_target(guarded=False, store=store)
        first = asyncio.create_task(target.set_category_enabled(
            category=CATEGORY_SURVIVAL, enabled=False))
        await asyncio.wait_for(store.first_started.wait(), timeout=1.0)

        second = asyncio.create_task(target.set_lane_enabled(
            lane=LANE_URGENT, enabled=False))
        await asyncio.sleep(0)
        overlapped = store.second_started.is_set()

        store.release_first.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert overlapped is False
        assert first_result.is_ok()
        assert second_result.is_ok()

    asyncio.run(scenario())


def test_intrusion_mode_and_window_are_persisted_as_one_atomic_value():
    target = _action_target()

    result = asyncio.run(target.set_intrusion_mode(
        mode=INTRUSION_NO_INTERRUPT, quiet_window_seconds=25.0))

    assert result.is_ok()
    assert target.store.calls == [(
        "intrusion_settings",
        {"mode": INTRUSION_NO_INTERRUPT, "quiet_window_seconds": 25.0},
    )]


def test_atomic_intrusion_preference_takes_precedence_over_legacy_keys():
    class Target:
        async def _stored(self, key):
            values = {
                "intrusion_settings": {
                    "mode": INTRUSION_NO_INTERRUPT,
                    "quiet_window_seconds": 25.0,
                },
                "dialogue_intrusion_mode": INTRUSION_ALLOW_INTERRUPT,
                "user_chat_quiet_window_seconds": 600.0,
            }
            return values.get(key)

    cfg = WowsConfig()
    asyncio.run(NekoWowsPlugin._apply_stored_preferences(Target(), cfg))

    assert cfg.dialogue_intrusion_mode == INTRUSION_NO_INTERRUPT
    assert cfg.user_chat_quiet_window_seconds == 25.0
