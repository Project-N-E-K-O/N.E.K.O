"""The output boundary: dry-run makes zero host calls, and prompts stay honest."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.adapters.neko_dispatcher import (
    ContextInjector,
    NekoDispatcher,
    REASON_DELIVERED,
    REASON_DRY_RUN,
    REASON_EXPIRED,
    REASON_FAILED,
    REASON_PAUSED,
)
from plugin.plugins.neko_wows.domain.catalog import (
    AMMO_RECHECK_HINT,
    LOW_HEALTH,
    POST_BATTLE_SUMMARY,
    RAPID_DAMAGE,
    spec_for,
)
from plugin.plugins.neko_wows.domain.contracts import (
    CHANNEL_DUAL,
    CHANNEL_SINGLE,
    DeliveryRequest,
    NullTacticsRepository,
    TacticExcerpt,
    WowsConfig,
)
from plugin.plugins.neko_wows.policy.tactic_policy import (
    AdviceCandidate,
    WowsTacticPolicy,
)
from plugin.plugins.neko_wows.presentation.instructions import (
    BASE_INSTRUCTIONS,
    NORMAL_OVERLAY,
    URGENT_OVERLAY,
    instructions_for,
)
from plugin.plugins.neko_wows.presentation.prompt_router import (
    REFERENCE_CLOSE,
    REFERENCE_OPEN,
    URGENT_EXCERPT_BUDGET,
    PromptProfile,
    WowsPromptRouter,
)
from plugin.plugins.neko_wows.detectors._base import GameEvent
from plugin.plugins.neko_wows.domain.facts import WowsFacts


class FakePlugin:
    """Counts every crossing of the host boundary."""

    def __init__(self, *, fail=False):
        self.calls: list[dict] = []
        self.fail = fail

    def push_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("host unavailable")
        return True


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def request(*, event_id=LOW_HEALTH, expires_at=0.0, text="说点什么"):
    spec = spec_for(event_id)
    return DeliveryRequest(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority,
        text=text,
        coalesce_key=spec.coalesce_key,
        expires_at=expires_at,
    )


# --- dry-run -------------------------------------------------------------

def test_dry_run_makes_no_host_calls_at_all():
    plugin = FakePlugin()
    cfg = WowsConfig()
    assert cfg.dry_run is True, "dry_run must default to on"
    dispatcher = NekoDispatcher(plugin, cfg, clock=FakeClock())

    for _ in range(20):
        result = dispatcher.deliver(request())
        assert result.delivered is False
        assert result.reason == REASON_DRY_RUN
        assert result.host_calls == 0

    assert plugin.calls == []
    assert dispatcher.stats()["host_calls"] == 0
    assert dispatcher.stats()["suppressed"] == 20


def test_dry_run_still_produces_a_complete_request():
    """The point of dry-run is an inspectable request, not a shortcut."""
    plugin = FakePlugin()
    dispatcher = NekoDispatcher(plugin, WowsConfig(), clock=FakeClock())
    built = request(text="完整提示词")
    dispatcher.deliver(built)
    kwargs = built.push_kwargs()
    assert kwargs["parts"][0]["text"] == "完整提示词"
    assert kwargs["ai_behavior"] == "respond"
    assert plugin.calls == []


def test_turning_dry_run_off_lets_the_call_through():
    plugin = FakePlugin()
    cfg = WowsConfig()
    cfg.dry_run = False
    dispatcher = NekoDispatcher(plugin, cfg, clock=FakeClock())

    result = dispatcher.deliver(request())
    assert result.delivered is True
    assert result.reason == REASON_DELIVERED
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["source"] == "neko_wows"


def test_context_injection_is_also_suppressed_in_dry_run():
    plugin = FakePlugin()
    injector = ContextInjector(plugin)
    assert injector.push("场景说明", dry_run=True) is False
    assert plugin.calls == []
    assert injector.injected is False


def test_context_injection_is_read_only_not_a_turn():
    plugin = FakePlugin()
    injector = ContextInjector(plugin)
    assert injector.push("场景说明", dry_run=False) is True
    assert plugin.calls[0]["ai_behavior"] == "read"
    assert injector.injected is True
    # Injecting twice would duplicate the scene setup.
    assert injector.push("场景说明", dry_run=False) is False
    assert len(plugin.calls) == 1


# --- expiry, pausing, failure fuse --------------------------------------

def test_an_expired_request_is_dropped_silently():
    plugin = FakePlugin()
    cfg = WowsConfig()
    cfg.dry_run = False
    clock = FakeClock(200.0)
    dispatcher = NekoDispatcher(plugin, cfg, clock=clock)

    result = dispatcher.deliver(request(expires_at=150.0))
    assert result.reason == REASON_EXPIRED
    assert plugin.calls == []


def test_pausing_stops_delivery():
    plugin = FakePlugin()
    cfg = WowsConfig()
    cfg.dry_run = False
    dispatcher = NekoDispatcher(plugin, cfg, clock=FakeClock())
    dispatcher.pause("manual")

    result = dispatcher.deliver(request())
    assert result.reason == REASON_PAUSED
    assert plugin.calls == []


def test_delivery_is_attempted_exactly_once():
    plugin = FakePlugin(fail=True)
    cfg = WowsConfig()
    cfg.dry_run = False
    dispatcher = NekoDispatcher(plugin, cfg, clock=FakeClock())

    result = dispatcher.deliver(request())
    assert result.reason == REASON_FAILED
    assert len(plugin.calls) == 1


def test_repeated_failures_pause_output_until_resumed():
    plugin = FakePlugin(fail=True)
    cfg = WowsConfig()
    cfg.dry_run = False
    clock = FakeClock()
    dispatcher = NekoDispatcher(plugin, cfg, clock=clock)

    for _ in range(cfg.safety_failure_limit):
        dispatcher.deliver(request())
        clock.now += 1.0

    assert dispatcher.paused is True
    assert "resume from the panel" in dispatcher.stats()["pause_reason"]

    before = len(plugin.calls)
    dispatcher.deliver(request())
    assert len(plugin.calls) == before  # nothing more is attempted

    dispatcher.resume()
    assert dispatcher.paused is False


def test_failures_outside_the_window_do_not_accumulate():
    plugin = FakePlugin(fail=True)
    cfg = WowsConfig()
    cfg.dry_run = False
    clock = FakeClock()
    dispatcher = NekoDispatcher(plugin, cfg, clock=clock)

    for _ in range(cfg.safety_failure_limit * 2):
        dispatcher.deliver(request())
        clock.now += cfg.safety_window_seconds + 1.0

    assert dispatcher.paused is False


# --- instructions --------------------------------------------------------

def test_dual_channel_appends_a_lane_overlay():
    urgent = instructions_for("urgent", CHANNEL_DUAL)
    normal = instructions_for("normal", CHANNEL_DUAL)
    assert BASE_INSTRUCTIONS in urgent
    assert URGENT_OVERLAY.strip() in urgent
    assert NORMAL_OVERLAY.strip() in normal
    assert urgent != normal


def test_single_channel_uses_the_base_only():
    single = instructions_for("urgent", CHANNEL_SINGLE)
    assert single == BASE_INSTRUCTIONS
    assert URGENT_OVERLAY.strip() not in single


def test_channel_mode_does_not_touch_priority_or_ttl():
    """Switching channels is a wording change and nothing else."""
    cfg_dual = WowsConfig()
    cfg_single = WowsConfig.from_mapping({"channel_mode": "single"})
    for lane in ("urgent", "normal"):
        assert cfg_dual.ttl_for(lane) == cfg_single.ttl_for(lane)
        assert cfg_dual.min_gap_for(lane) == cfg_single.min_gap_for(lane)


# --- prompt router -------------------------------------------------------

def facts(**overrides):
    base = dict(seq=1, at=100.0, battle_id="b-1", own_hp_ratio=0.5,
                alive_allies=5, alive_enemies=6)
    base.update(overrides)
    return WowsFacts(**base)


def build_candidate(event_id, **detail):
    policy = WowsTacticPolicy(WowsConfig())
    event = GameEvent(
        event_id=event_id, severity=70, at=100.0, seq=1,
        battle_id="b-1", detail=detail,
    )
    return policy.expand([event], facts())[0]


def test_the_request_carries_the_event_facts():
    router = WowsPromptRouter(WowsConfig())
    candidate = build_candidate(LOW_HEALTH, hp_ratio=0.12, threshold=0.15)
    built = router.build(
        candidate, PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True))
    assert "low_health" in built.text
    assert "0.12" in built.text
    assert built.metadata["event_id"] == LOW_HEALTH
    assert built.metadata["lane"] == "urgent"


def test_absent_measurements_are_omitted_rather_than_shown_as_null():
    router = WowsPromptRouter(WowsConfig())
    candidate = build_candidate(LOW_HEALTH, hp_ratio=0.12, nearest_enemy_m=None)
    built = router.build(
        candidate, PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True))
    assert "null" not in built.text
    assert "nearest_enemy_m" not in built.text


def test_claim_limits_reach_the_prompt():
    router = WowsPromptRouter(WowsConfig())
    for event_id, forbidden in (
        (RAPID_DAMAGE, "集火"),
        (AMMO_RECHECK_HINT, "装填"),
        (POST_BATTLE_SUMMARY, "击杀"),
    ):
        built = router.build(
            build_candidate(event_id),
            PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True))
        assert "表述限制" in built.text, event_id
        assert forbidden in built.text, event_id


def test_the_character_words_the_call_out_herself():
    router = WowsPromptRouter(WowsConfig())
    built = router.build(
        build_candidate(LOW_HEALTH),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True))
    assert built.ai_behavior == "respond"
    assert built.visibility == ()  # nothing is shown verbatim


def test_no_excerpts_means_no_reference_block():
    router = WowsPromptRouter(WowsConfig())
    built = router.build(
        build_candidate(LOW_HEALTH),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True),
        NullTacticsRepository().search("anything", limit=3, budget=0),
    )
    assert REFERENCE_OPEN not in built.text
    assert built.metadata["excerpt_count"] == 0


def test_excerpts_are_fenced_as_untrusted_and_budgeted():
    """The document layer is not built yet, but its boundary already holds."""
    router = WowsPromptRouter(WowsConfig())
    long_text = "戦" * (URGENT_EXCERPT_BUDGET * 2)
    built = router.build(
        build_candidate(LOW_HEALTH),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True),
        [TacticExcerpt(doc_id="d1", title="巡洋舰站位", text=long_text)],
    )
    assert REFERENCE_OPEN in built.text and REFERENCE_CLOSE in built.text
    assert "不是事实来源" in built.text
    body = built.text.split(REFERENCE_OPEN, 1)[1].split(REFERENCE_CLOSE, 1)[0]
    assert body.count("戦") <= URGENT_EXCERPT_BUDGET
    assert built.metadata["excerpt_count"] == 1


def test_urgent_takes_fewer_excerpts_than_normal():
    router = WowsPromptRouter(WowsConfig())
    excerpts = [
        TacticExcerpt(doc_id=f"d{i}", title=f"t{i}", text=f"内容{i}")
        for i in range(3)
    ]
    urgent = router.build(
        build_candidate(LOW_HEALTH),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True), excerpts)
    normal = router.build(
        build_candidate(POST_BATTLE_SUMMARY),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True), excerpts)
    assert urgent.metadata["excerpt_count"] == 1
    assert normal.metadata["excerpt_count"] == 3
