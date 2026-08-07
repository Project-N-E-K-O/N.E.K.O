"""Whole-pipeline replay over a desensitized battle.

Drives the same chain the plugin runs at runtime -- adapter, cursor, facts,
detectors, policy, arbiter, prompt router, dispatcher -- so a regression anywhere
in the seam between two stages shows up here rather than only in a real battle.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

import pytest

from plugin.plugins.neko_wows.adapters.neko_dispatcher import (
    NekoDispatcher,
    REASON_DRY_RUN,
)
from plugin.plugins.neko_wows.adapters.schema_adapter import WowsSchemaAdapter
from plugin.plugins.neko_wows.adapters.transport import CursorGate
from plugin.plugins.neko_wows.detectors._base import DetectorRegistry
from plugin.plugins.neko_wows.detectors.geometry import build_geometry_detectors
from plugin.plugins.neko_wows.detectors.lifecycle import build_lifecycle_detectors
from plugin.plugins.neko_wows.detectors.survival import build_survival_detectors
from plugin.plugins.neko_wows.detectors.targeting import build_targeting_detectors
from plugin.plugins.neko_wows.detectors.threat import build_threat_detectors
from plugin.plugins.neko_wows.domain.catalog import (
    BATTLE_ENDED,
    BATTLE_STARTED,
    EVENT_CATALOG,
    POST_BATTLE_SUMMARY,
)
from plugin.plugins.neko_wows.domain.contracts import (
    CHANNEL_DUAL,
    NullTacticsRepository,
    WowsConfig,
)
from plugin.plugins.neko_wows.domain.facts import FactBuilder
from plugin.plugins.neko_wows.domain.snapshot import (
    STATUS_ENDED,
    STATUS_LIVE,
    STATUS_STALE,
    STATUS_WAITING,
)
from plugin.plugins.neko_wows.policy.arbiter import Arbiter
from plugin.plugins.neko_wows.policy.tactic_policy import WowsTacticPolicy
from plugin.plugins.neko_wows.presentation.prompt_router import (
    PromptProfile,
    WowsPromptRouter,
)
from plugin.plugins.neko_wows.ship_data.context import (
    BattleShipContextManager,
    ContextObservation,
)
from plugin.plugins.neko_wows.ship_data.models import (
    CatalogMeta,
    CatalogShip,
    ShipProfile,
)
from plugin.plugins.neko_wows.ship_data.resolver import normalize_ship_alias

REPLAY = (
    Path(__file__).resolve().parents[2]
    / "plugin" / "plugins" / "neko_wows" / "contract" / "replay_battle.json"
)

REPLAY_SHIP_IDS = {
    "OwnShip": 91001,
    "AllyCruiser": 91002,
    "EnemyDD": 91003,
    "EnemyCA": 91004,
}
REPLAY_SHIP_CLASSES = {
    "OwnShip": "Battleship",
    "AllyCruiser": "Cruiser",
    "EnemyDD": "Destroyer",
    "EnemyCA": "Cruiser",
}


class FakeCatalogSnapshot:
    """Small immutable catalog surface, freshly pinned for each battle."""

    def __init__(self):
        self.closed = False
        self.meta = CatalogMeta(
            schema_version=1,
            catalog_version="replay-catalog-v1",
            game_version="",
            channel="test",
            source_repo="replay-fixture",
            source_commit="test-only",
            content_sha256="0" * 64,
            default_language="en",
            ship_count=len(REPLAY_SHIP_IDS),
            profile_count=len(REPLAY_SHIP_IDS),
        )
        ships = {
            name: CatalogShip(
                ship_id=ship_id,
                ship_index=f"replay:{name}",
                name_key=f"IDS_{name.upper()}",
                display_name=name,
                nation="test",
                ship_class=REPLAY_SHIP_CLASSES[name],
                tier=10,
            )
            for name, ship_id in REPLAY_SHIP_IDS.items()
        }
        self._aliases = {
            normalize_ship_alias(name): (ship,)
            for name, ship in ships.items()
        }
        self._profiles = {
            ship.ship_id: ShipProfile(
                profile_id=f"{ship.ship_id}:reference_top:primary",
                ship_id=ship.ship_id,
                configuration="reference_top",
                variant_key="primary",
                is_primary=True,
                profile_schema_version=1,
                data={},
                profile_sha256="0" * 64,
            )
            for ship in ships.values()
        }

    def alias_candidates(self, alias_norm: str):
        if self.closed:
            return ()
        return self._aliases.get(alias_norm, ())

    def primary_profile(self, ship_id: int):
        if self.closed:
            return None
        return self._profiles.get(ship_id)

    def close(self):
        self.closed = True


class FakeCatalogStore:
    def __init__(self):
        self.snapshots: list[FakeCatalogSnapshot] = []
        self.requested_languages: list[str | None] = []

    def snapshot(self, *, language=None):
        self.requested_languages.append(language)
        snapshot = FakeCatalogSnapshot()
        self.snapshots.append(snapshot)
        return snapshot


class FakePlugin:
    def __init__(self):
        self.calls: list[dict] = []
        self.call_frames: list[tuple[int | None, dict]] = []
        self.frame_seq: int | None = None

    def push_message(self, **kwargs):
        self.calls.append(kwargs)
        self.call_frames.append((self.frame_seq, kwargs))
        return {"submitted": True}


class Pipeline:
    """The runtime chain, assembled the same way the plugin assembles it."""

    def __init__(self, cfg: WowsConfig):
        self.cfg = cfg
        self.plugin = FakePlugin()
        self.adapter = WowsSchemaAdapter()
        self.gate = CursorGate()
        self.facts = FactBuilder(cfg)
        self.registry = DetectorRegistry((
            *build_lifecycle_detectors(cfg),
            *build_survival_detectors(cfg),
            *build_threat_detectors(cfg),
            *build_geometry_detectors(cfg),
            *build_targeting_detectors(cfg),
        ))
        self.policy = WowsTacticPolicy(cfg)
        self.arbiter = Arbiter(cfg)
        self.router = WowsPromptRouter(cfg)
        self.tactics = NullTacticsRepository()
        self.dispatcher = NekoDispatcher(self.plugin, cfg, clock=self._clock)
        self.catalog_store = FakeCatalogStore()
        self.ship_context = BattleShipContextManager(
            self.plugin, self.catalog_store, cfg, clock=self._clock)

        self.previous = None
        self.now = 0.0
        self.snapshots = []
        self.observations: list[tuple[int, ContextObservation]] = []
        self.detected: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.dropped: list[str] = []
        self.blocked: list[tuple[str, tuple[str, ...]]] = []

    def _clock(self) -> float:
        return self.now

    def feed(self, payload, *, epoch=1, at=None):
        self.now = self.now + 1.0 if at is None else at
        snapshot = self.adapter.parse(
            payload, transport="ws", epoch=epoch, received_at=self.now)
        accepted, reason = self.gate.accept(snapshot, epoch)
        if not accepted:
            self.dropped.append(reason)
            return
        self.snapshots.append(snapshot)
        self.plugin.frame_seq = snapshot.seq

        try:
            current = (snapshot, self.facts.build(snapshot))
            result = self.registry.feed(self.previous, current, cfg=self.cfg)
            self.previous = current
            if result.identity_reset:
                self.arbiter.reset_battle(snapshot.battle_id)
            for entry in result.blocked:
                self.blocked.append((entry.detector, entry.missing))
            self.detected.extend(event.event_id for event in result.events)

            if snapshot.is_live:
                observation = self.ship_context.observe(
                    snapshot, dry_run=self.cfg.dry_run)
                self.observations.append((snapshot.seq, observation))

            candidates = self.policy.expand(result.events, current[1])
            decision = self.arbiter.decide(candidates, self.now)
            if decision.chosen is None:
                return
            request = self.router.build(
                decision.chosen,
                PromptProfile(
                    channel_mode=CHANNEL_DUAL, dry_run=self.cfg.dry_run),
                self.tactics.search(decision.chosen.summary, limit=3, budget=0),
            )
            outcome = self.dispatcher.deliver(request)
            self.arbiter.commit(
                decision.chosen, self.now, outcome_reason=outcome.reason)
            self.delivered.append((request.event_id, outcome.reason))
        finally:
            if snapshot.status == STATUS_ENDED:
                self.ship_context.reset("battle_end")


@pytest.fixture(scope="module")
def replay():
    return json.loads(REPLAY.read_text(encoding="utf-8"))


@pytest.fixture
def pipeline():
    return Pipeline(WowsConfig())


def run(pipeline: Pipeline, frames, *, epoch=1):
    for offset, frame in enumerate(frames, start=1):
        pipeline.feed(frame, epoch=epoch, at=100.0 + offset * 3.0)
    return pipeline


# --- fixture integrity ---------------------------------------------------

def test_the_replay_fixture_covers_the_battle_lifecycle(replay):
    statuses = [frame["source"]["status"] for frame in replay["frames"]]
    assert statuses[0] == STATUS_WAITING
    assert STATUS_LIVE in statuses
    assert STATUS_STALE in statuses
    assert statuses[-1] == STATUS_ENDED


def test_the_replay_fixture_carries_no_real_player_data(replay):
    """A shared fixture must not smuggle in someone's account name."""
    text = json.dumps(replay, ensure_ascii=False)
    for name in ("PlayerOne", "PlayerTwo", "FoeOne", "FoeTwo"):
        assert name in text
    # Synthetic ids only, in a range the game does not issue.
    for frame in replay["frames"]:
        for ship in frame["objects"]:
            assert 1000 <= ship["playerId"] <= 9999


# --- end to end ----------------------------------------------------------

def test_the_pipeline_survives_the_whole_replay(replay, pipeline):
    run(pipeline, replay["frames"])
    assert len(pipeline.snapshots) == len(replay["frames"])
    assert pipeline.dropped == []


def test_battle_start_and_end_are_both_detected(replay, pipeline):
    run(pipeline, replay["frames"])
    assert BATTLE_STARTED in pipeline.detected
    assert BATTLE_ENDED in pipeline.detected
    assert POST_BATTLE_SUMMARY in pipeline.detected


def test_every_detected_event_is_a_known_catalog_entry(replay, pipeline):
    run(pipeline, replay["frames"])
    for event_id in pipeline.detected:
        assert event_id in EVENT_CATALOG


def test_at_most_one_call_out_is_chosen_per_frame(replay, pipeline):
    run(pipeline, replay["frames"])
    assert len(pipeline.delivered) <= len(replay["frames"])


def test_the_replay_makes_zero_host_calls_in_dry_run(replay, pipeline):
    run(pipeline, replay["frames"])
    assert pipeline.plugin.calls == []
    assert pipeline.dispatcher.stats()["host_calls"] == 0
    assert all(reason == REASON_DRY_RUN for _event, reason in pipeline.delivered)
    assert pipeline.observations
    assert all(
        observation.submitted_ship_ids == ()
        for _seq, observation in pipeline.observations
    )


def test_battle_end_releases_the_replay_catalog_context(replay, pipeline):
    run(pipeline, replay["frames"])

    assert pipeline.ship_context.stats()["state"] == "idle"
    assert pipeline.catalog_store.snapshots
    assert all(snapshot.closed for snapshot in pipeline.catalog_store.snapshots)


def test_the_stale_frame_blocks_live_detectors_without_events(replay, pipeline):
    run(pipeline, replay["frames"])
    blocked_detectors = {name for name, _missing in pipeline.blocked}
    # The stale frame drops objects entirely, so anything needing them is gated.
    assert "enemy_closing" in blocked_detectors


def test_recovery_after_the_stale_frame_does_not_fabricate_edges(replay, pipeline):
    """The battle ends right after a stale patch; no survival edge may appear."""
    run(pipeline, replay["frames"])
    assert "own_ship_sunk" not in pipeline.detected


def test_the_cursor_advances_monotonically(replay, pipeline):
    run(pipeline, replay["frames"])
    seqs = [snapshot.seq for snapshot in pipeline.snapshots]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_a_replayed_frame_is_recognized_as_a_duplicate(replay, pipeline):
    frames = replay["frames"]
    run(pipeline, frames)
    pipeline.feed(frames[-1], epoch=1, at=400.0)
    assert pipeline.dropped == ["duplicate_seq"]


def test_a_second_battle_resets_state_and_re_announces_the_start(replay, pipeline):
    frames = replay["frames"]
    run(pipeline, frames)
    first_starts = pipeline.detected.count(BATTLE_STARTED)

    next_battle = []
    for offset, frame in enumerate(frames[1:], start=1):
        clone = json.loads(json.dumps(frame))
        clone["seq"] = 100 + offset
        clone["battleId"] = "replay-b2"
        next_battle.append(clone)
    for offset, frame in enumerate(next_battle, start=1):
        pipeline.feed(frame, epoch=1, at=500.0 + offset * 3.0)

    assert pipeline.detected.count(BATTLE_STARTED) == first_starts + 1
    assert len(pipeline.catalog_store.snapshots) == 2
    assert all(snapshot.closed for snapshot in pipeline.catalog_store.snapshots)
    assert pipeline.catalog_store.requested_languages == [
        pipeline.cfg.ship_catalog_language,
        pipeline.cfg.ship_catalog_language,
    ]


def test_switching_to_real_output_delivers_and_counts(replay, pipeline):
    """The same replay, with output enabled, must actually reach the host."""
    pipeline.cfg.dry_run = False
    pipeline.dispatcher.apply_config(pipeline.cfg)
    run(pipeline, replay["frames"])

    assert pipeline.plugin.calls, "something should have been said"

    def is_ship_reference_read(call):
        return (
            call["ai_behavior"] == "read"
            and call.get("metadata", {}).get("kind") == "ship_reference"
        )

    respond_calls = [
        call for call in pipeline.plugin.calls
        if call["ai_behavior"] == "respond"
    ]
    ship_reads = [
        call for call in pipeline.plugin.calls
        if is_ship_reference_read(call)
    ]
    assert ship_reads, "live replay should inject ship reference context"
    assert respond_calls, "the replay should still produce spoken output"
    assert len(pipeline.plugin.calls) == len(respond_calls) + len(ship_reads), (
        "every replay host call must be a response or ship-reference read")
    assert pipeline.dispatcher.stats()["host_calls"] == len(respond_calls)
    assert pipeline.ship_context.stats()["state"] == "idle"
    assert all(snapshot.closed for snapshot in pipeline.catalog_store.snapshots)
    expected_full_references = Counter({
        ship_id: 1 for ship_id in REPLAY_SHIP_IDS.values()
    })
    full_reference_ids = Counter(
        ship_id
        for call in ship_reads
        for ship_id in call["metadata"]["ship_ids"]
        if ship_id not in call["metadata"]["count_update_ship_ids"]
    )
    assert full_reference_ids == expected_full_references
    observed_submissions = Counter(
        ship_id
        for _seq, observation in pipeline.observations
        for ship_id in observation.submitted_ship_ids
    )
    assert observed_submissions == expected_full_references
    call_indexes_by_frame = defaultdict(
        lambda: {"ship_reads": [], "responds": []})
    for call_index, (frame_seq, call) in enumerate(pipeline.plugin.call_frames):
        if is_ship_reference_read(call):
            call_indexes_by_frame[frame_seq]["ship_reads"].append(call_index)
        elif call["ai_behavior"] == "respond":
            call_indexes_by_frame[frame_seq]["responds"].append(call_index)
    paired_frames = [
        indexes for indexes in call_indexes_by_frame.values()
        if indexes["ship_reads"] and indexes["responds"]
    ]
    assert paired_frames, "at least one frame should both read and respond"
    for indexes in paired_frames:
        assert max(indexes["ship_reads"]) < min(indexes["responds"])
    for call in respond_calls:
        assert call["source"] == "neko_wows"
        assert call["ai_behavior"] == "respond"
        assert call["visibility"] == []
        assert call["parts"][0]["text"]


def test_delivered_prompts_forbid_unsupported_claims(replay, pipeline):
    pipeline.cfg.dry_run = False
    pipeline.dispatcher.apply_config(pipeline.cfg)
    run(pipeline, replay["frames"])

    respond_calls = [
        call for call in pipeline.plugin.calls
        if call["ai_behavior"] == "respond"
    ]
    assert respond_calls
    for call in respond_calls:
        text = call["parts"][0]["text"]
        assert "只使用给出的事实" in text
        assert "击杀" in text  # named explicitly as off limits


def test_rendered_facts_contain_no_unsupported_domain_data(replay, pipeline):
    """The facts block is built from measurements, so it cannot name a domain
    the service declares unsupported."""
    pipeline.cfg.dry_run = False
    pipeline.dispatcher.apply_config(pipeline.cfg)
    run(pipeline, replay["frames"])

    respond_calls = [
        call for call in pipeline.plugin.calls
        if call["ai_behavior"] == "respond"
    ]
    assert respond_calls
    for call in respond_calls:
        text = call["parts"][0]["text"]
        # Everything from "事件：" onward is generated from the fact dicts.
        facts_block = text.split("事件：", 1)[1]
        for marker in ("capturePoint", "torpedo", "kills", "consumable"):
            assert marker not in facts_block, marker


def test_the_legacy_path_produces_the_same_kind_of_chain(replay):
    """A pre-envelope service must reach the same events, cursor aside."""
    pipeline = Pipeline(WowsConfig())
    legacy_frames = []
    for frame in replay["frames"]:
        clone = json.loads(json.dumps(frame))
        for key in ("serviceId", "apiVersion", "instanceId", "seq",
                    "battleId", "source", "capabilities", "availability",
                    "extensions"):
            clone.pop(key, None)
        legacy_frames.append(clone)

    for offset, frame in enumerate(legacy_frames, start=1):
        pipeline.feed(frame, epoch=1, at=100.0 + offset * 3.0)

    assert all(snapshot.legacy for snapshot in pipeline.snapshots)
    assert BATTLE_STARTED in pipeline.detected
    assert BATTLE_ENDED in pipeline.detected
    assert pipeline.plugin.calls == []
