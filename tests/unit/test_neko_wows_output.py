"""The output boundary: dry-run makes zero host calls, and prompts stay honest."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_wows import NekoWowsPlugin
from plugin.plugins.neko_wows.adapters.neko_dispatcher import (
    ContextInjector,
    NekoDispatcher,
    REASON_DELIVERED,
    REASON_DRY_RUN,
    REASON_EXPIRED,
    REASON_FAILED,
    REASON_PAUSED,
)
from plugin.plugins.neko_wows.adapters.runtime_timeline import STAGE_SHIP_CATALOG
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
    VISION_LOOK_BEFORE_SPEAK,
    WOWS_CONTEXT_INSTRUCTIONS,
    WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS,
    context_instructions,
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
from plugin.plugins.neko_wows.domain.snapshot import STATUS_ENDED
from plugin.plugins.neko_wows.ship_data.context import (
    BattleShipContextManager,
    ContextObservation,
)
from plugin.plugins.neko_wows.ship_data.models import (
    CatalogMeta,
    CatalogShip,
    ShipProfile,
)


class FakePlugin:
    """Counts every crossing of the host boundary."""

    def __init__(self, *, fail=False, receipt=None):
        self.calls: list[dict] = []
        self.fail = fail
        self.receipt = {"submitted": True} if receipt is None else receipt

    def push_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("host unavailable")
        return self.receipt


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
    cfg = WowsConfig(dry_run=True)
    assert cfg.dry_run is True
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
    dispatcher = NekoDispatcher(
        plugin, WowsConfig(dry_run=True), clock=FakeClock())
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


def test_a_declined_sdk_submission_is_not_reported_as_delivered():
    plugin = FakePlugin(receipt={"submitted": False, "reason": "host_queue_full"})
    cfg = WowsConfig()
    cfg.dry_run = False
    dispatcher = NekoDispatcher(plugin, cfg, clock=FakeClock())

    result = dispatcher.deliver(request())

    assert result.delivered is False
    assert result.reason == REASON_FAILED
    assert result.host_calls == 1
    assert dispatcher.stats()["delivered"] == 0
    assert dispatcher.stats()["recent_failures"] == 1


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


def test_context_injection_replaces_when_scene_text_changes():
    """Screenshot toggle mid-battle must refresh the scene block, not keep the old one."""
    plugin = FakePlugin()
    injector = ContextInjector(plugin)
    assert injector.push("看不到屏幕画面", dry_run=False) is True

    assert injector.push("可调用 wows_look_at_battle", dry_run=False) is True
    assert injector.injected is True
    assert len(plugin.calls) == 2
    assert plugin.calls[1]["parts"][0]["text"] == "可调用 wows_look_at_battle"

    # Same replacement text is still a no-op.
    assert injector.push("可调用 wows_look_at_battle", dry_run=False) is False
    assert len(plugin.calls) == 2


def test_declined_context_submission_stays_retryable():
    plugin = FakePlugin(receipt={"submitted": False, "reason": "unavailable"})
    injector = ContextInjector(plugin)

    assert injector.push("场景说明", dry_run=False) is False
    assert injector.injected is False
    assert injector.host_calls == 1

    plugin.receipt = {"submitted": True}
    assert injector.push("场景说明", dry_run=False) is True
    assert injector.injected is True


def test_restore_cleans_up_an_existing_context_even_after_dry_run_is_enabled():
    plugin = FakePlugin()
    injector = ContextInjector(plugin)
    assert injector.push("场景说明", dry_run=False) is True

    assert injector.restore("恢复说明", dry_run=True) is True
    assert injector.injected is False
    assert len(plugin.calls) == 2


def test_battle_end_restores_context_after_the_frame_is_evaluated():
    calls = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin._pipeline_lock = threading.RLock()
    plugin.cfg = WowsConfig()
    plugin._evaluate_locked = lambda _snapshot: calls.append("evaluate")
    plugin.context_injector = SimpleNamespace(
        restore=lambda *_args, **_kwargs: calls.append("restore") or True)
    plugin.ship_context = SimpleNamespace(
        reset=lambda reason: calls.append(f"ship_reset:{reason}"))

    NekoWowsPlugin._evaluate(plugin, SimpleNamespace(status=STATUS_ENDED))

    assert calls == ["evaluate", "restore", "ship_reset:battle_end"]


def test_battle_end_restores_context_when_evaluation_raises():
    calls = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin._pipeline_lock = threading.RLock()
    plugin.cfg = WowsConfig()

    def fail_evaluation(_snapshot):
        calls.append("evaluate")
        raise RuntimeError("evaluation failed")

    plugin._evaluate_locked = fail_evaluation
    plugin.context_injector = SimpleNamespace(
        restore=lambda *_args, **_kwargs: calls.append("restore") or True)
    plugin.ship_context = SimpleNamespace(
        reset=lambda reason: calls.append(f"ship_reset:{reason}"))

    with pytest.raises(RuntimeError, match="evaluation failed"):
        NekoWowsPlugin._evaluate(plugin, SimpleNamespace(status=STATUS_ENDED))

    assert calls == ["evaluate", "restore", "ship_reset:battle_end"]


class _Timeline:
    def __init__(self):
        self.records = []

    def record(self, *args, **kwargs):
        self.records.append((args, kwargs))


def _catalog_order_target(*, catalog_error: Exception | None = None):
    calls = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = WowsConfig(dry_run=False)
    plugin._state_lock = threading.RLock()
    plugin._previous = None
    plugin._latest = None
    plugin._events_seen = 0
    plugin._blocked_signature = ()
    plugin._last_candidate = None
    plugin._prompt_bundle = SimpleNamespace(revision_id="builtin")
    plugin.timeline = _Timeline()
    facts = SimpleNamespace(at=100.0)
    plugin.facts = SimpleNamespace(build=lambda _snapshot: facts)
    event = SimpleNamespace(event_id="battle_started")
    plugin.registry = SimpleNamespace(feed=lambda *_args, **_kwargs: SimpleNamespace(
        identity_reset=False,
        blocked=(),
        events=(event,),
        reason="events",
    ))
    plugin.context_injector = SimpleNamespace(
        push=lambda *_args, **_kwargs: calls.append("scene") or True)
    plugin.live_vision = SimpleNamespace(is_active=lambda: False)

    def observe(*_args, **_kwargs):
        calls.append("ship")
        if catalog_error is not None:
            raise catalog_error
        return ContextObservation(state="null_catalog")

    plugin.ship_context = SimpleNamespace(observe=observe)
    chosen = SimpleNamespace(
        event_id="battle_started",
        lane="normal",
        severity=20,
        summary="开局",
    )
    plugin.policy = SimpleNamespace(expand=lambda *_args: (chosen,))
    plugin.arbiter = SimpleNamespace(
        decide=lambda *_args: SimpleNamespace(chosen=chosen, chain=()),
        commit=lambda *_args, **_kwargs: None,
    )
    request = SimpleNamespace(text="respond", event_id="battle_started")
    plugin.router = SimpleNamespace(build=lambda *_args: request)
    plugin.dispatcher = SimpleNamespace(deliver=lambda _request: (
        calls.append("respond")
        or SimpleNamespace(reason="delivered", host_calls=1)
    ))
    plugin._reference_for = lambda *_args: ()
    ship = SimpleNamespace(
        name="OwnShip",
        tier=10,
        ship_type="Battleship",
        ui_id=1,
        player_id=1001,
        team_id=0,
        relation="self",
    )
    snapshot = SimpleNamespace(
        is_live=True,
        active=True,
        identity=("replay-inst", "battle-1"),
        game_version="",
        ships=(ship,),
        self_ship=ship,
        seq=1,
        battle_id="battle-1",
        status="live",
        transport="ws",
    )
    return plugin, snapshot, calls


def test_ship_catalog_observation_runs_after_scene_and_before_response():
    plugin, snapshot, calls = _catalog_order_target()

    NekoWowsPlugin._evaluate_locked(plugin, snapshot)

    assert calls == ["scene", "ship", "respond"]


def test_automatic_lifecycle_never_queries_the_official_ship_api():
    class GuardOfficialClient:
        def __init__(self):
            self.calls = 0

        def query_ship_id(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("automatic lifecycle must stay on offline catalog")

    plugin, snapshot, calls = _catalog_order_target()
    plugin.cfg.official_api_enabled = True
    plugin.cfg.official_api_application_id = "test-only-app-id"
    official_api = GuardOfficialClient()
    plugin.official_api = official_api
    catalog_ship = CatalogShip(
        ship_id=92001,
        ship_index="replay:OwnShip",
        name_key="IDS_OWNSHIP",
        display_name="OwnShip",
        nation="test",
        ship_class="Battleship",
        tier=10,
    )
    profile = ShipProfile(
        profile_id="92001:reference_top:primary",
        ship_id=92001,
        configuration="reference_top",
        variant_key="primary",
        is_primary=True,
        profile_schema_version=1,
        data={},
        profile_sha256="0" * 64,
    )
    catalog = SimpleNamespace(
        meta=CatalogMeta(
            schema_version=1,
            catalog_version="lifecycle-test-v1",
            game_version="",
            channel="test",
            source_repo="test",
            source_commit="test-only",
            content_sha256="0" * 64,
            default_language="en",
            ship_count=1,
            profile_count=1,
        ),
        alias_candidates=lambda alias: (
            (catalog_ship,) if alias == "ownship" else ()),
        primary_profile=lambda ship_id: (
            profile if ship_id == catalog_ship.ship_id else None),
        close=lambda: None,
    )
    plugin.push_message = lambda **_kwargs: {"submitted": True}
    real_ship_context = BattleShipContextManager(
        plugin,
        SimpleNamespace(snapshot=lambda **_kwargs: catalog),
        plugin.cfg,
    )

    def observe_with_real_manager(*args, **kwargs):
        calls.append("ship")
        return real_ship_context.observe(*args, **kwargs)

    plugin.ship_context = SimpleNamespace(observe=observe_with_real_manager)

    NekoWowsPlugin._evaluate_locked(plugin, snapshot)

    assert official_api.calls == 0
    assert calls == ["scene", "ship", "respond"]
    assert real_ship_context.stats()["resolved_ship_types"] == 1
    assert real_ship_context.stats()["submitted_ship_types"] == 1


def test_ship_catalog_failure_does_not_stop_existing_delivery():
    plugin, snapshot, calls = _catalog_order_target(
        catalog_error=RuntimeError("catalog unavailable"))

    NekoWowsPlugin._evaluate_locked(plugin, snapshot)

    assert calls == ["scene", "ship", "respond"]
    assert any(
        args[:2] == (STAGE_SHIP_CATALOG, "error")
        for args, _kwargs in plugin.timeline.records
    )


def test_ship_catalog_dashboard_never_exposes_official_application_id():
    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = WowsConfig(
        official_api_enabled=True,
        official_api_region="asia",
        official_api_application_id="secret-app-id",
    )
    plugin.ship_context = SimpleNamespace(stats=lambda: {
        "state": "loaded",
        "frozen_catalog_version": "v1",
        "resolved_ship_types": 2,
    })

    payload = NekoWowsPlugin._ship_catalog_payload(plugin)

    assert payload["official_tool"] == {
        "enabled": True,
        "region": "asia",
        "key_configured": True,
        "cache_entries": 0,
        "cache_hits": 0,
        "cache_misses": 0,
    }
    assert "secret-app-id" not in repr(payload)


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


def test_screenshot_soft_prompt_is_off_by_default():
    router = WowsPromptRouter(WowsConfig())
    built = router.build(
        build_candidate(LOW_HEALTH),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True),
    )
    assert VISION_LOOK_BEFORE_SPEAK.strip() not in built.text
    assert built.metadata["screenshot_enabled"] is False


def test_screenshot_soft_prompt_is_appended_when_enabled():
    router = WowsPromptRouter(WowsConfig())
    for event_id in (LOW_HEALTH, POST_BATTLE_SUMMARY):
        built = router.build(
            build_candidate(event_id),
            PromptProfile(
                channel_mode=CHANNEL_DUAL,
                dry_run=True,
                screenshot_enabled=True,
            ),
        )
        assert "wows_look_at_battle" in built.text, event_id
        assert VISION_LOOK_BEFORE_SPEAK.strip() in built.text, event_id
        assert built.metadata["screenshot_enabled"] is True


def test_context_instructions_follow_the_screenshot_switch():
    assert context_instructions(screenshot_enabled=False) == WOWS_CONTEXT_INSTRUCTIONS
    enabled = context_instructions(screenshot_enabled=True)
    assert enabled == WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS
    assert "wows_look_at_battle" in enabled
    assert "看不到屏幕画面" not in enabled


def test_base_instructions_distinguish_visible_from_alive_counts():
    assert "visible_enemies" in BASE_INSTRUCTIONS
    assert "失去视野" in BASE_INSTRUCTIONS
    assert "团灭" in BASE_INSTRUCTIONS
