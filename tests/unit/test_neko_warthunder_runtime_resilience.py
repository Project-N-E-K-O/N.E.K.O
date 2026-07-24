from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from plugin.plugins.neko_warthunder.adapters.neko_dispatcher import (
    NekoDispatcher,
    _host_interrupt_pending,
    _output_event_max_age_seconds,
    _quiet_window_suppression,
)
from plugin.plugins.neko_warthunder.adapters.telemetry_client import parse_telemetry
from plugin.plugins.neko_warthunder.core.arbiter import Arbiter
from plugin.plugins.neko_warthunder.core.contracts import (
    BATTLE_ENDED,
    BattleEvent,
    BattleState,
    WtConfig,
)
from plugin.plugins.neko_warthunder.core.safety_guard import SafetyGuard
from plugin.plugins.neko_warthunder.detectors.discrete.lifecycle import (
    BattleEndDetector,
    DeathDetector,
)
from plugin.plugins.neko_warthunder.detectors.discrete.proximity import ProximityDetector
from plugin.plugins.neko_warthunder.detectors.discrete.situation import AirSituationDetector


_DATA_PROCESS = (
    Path(__file__).resolve().parents[2]
    / "plugin"
    / "plugins"
    / "neko_warthunder"
    / "data_layer"
    / "data process"
)
sys.path.insert(0, str(_DATA_PROCESS))

from wt_server import TelemetryService  # noqa: E402
from wt_telemetry import (  # noqa: E402
    ConnectionState,
    Indicators,
    MapObject,
    MapInfo,
    VehicleState,
    WarThunderClient,
)
from wt_geo import analyze_situation  # noqa: E402
from wt_proximity import ProximityTracker  # noqa: E402


def _running_ground_state(timestamp: float = 1.0) -> BattleState:
    return BattleState(
        connected=True,
        conn_state="in_battle",
        in_battle=True,
        domain="ground",
        mission_status="running",
        combat={"my": {"kills": 2, "deaths": 1}, "total_events": 3},
        timestamp=timestamp,
    )


def test_terminal_mission_status_emits_once_and_plain_exit_does_not_invent_result() -> None:
    detector = BattleEndDetector()
    running = _running_ground_state()
    assert detector.detect(BattleState(), running) is None

    success = _running_ground_state(2.0)
    success.mission_status = "success"
    event = detector.detect(running, success)
    assert event is not None
    assert event.payload == {"result": "success, K2/D1", "domain": "ground"}
    assert detector.detect(success, success) is None
    menu_after_success = BattleState(connected=True, conn_state="not_in_battle", timestamp=3.0)
    assert detector.detect(success, menu_after_success) is None

    # A new battle must not reuse the previous battle's terminal status or K/D.
    new_running = BattleState(
        connected=True,
        conn_state="in_battle",
        in_battle=True,
        domain="air",
        mission_status="running",
        timestamp=4.0,
    )
    assert detector.detect(menu_after_success, new_running) is None
    new_menu = BattleState(connected=True, conn_state="not_in_battle", timestamp=5.0)
    assert detector.detect(new_running, new_menu) is None

    fallback = BattleEndDetector()
    assert fallback.detect(BattleState(), running) is None
    offline = BattleState(connected=False, conn_state="offline", timestamp=2.0)
    assert fallback.detect(running, offline) is None
    menu = BattleState(
        connected=True,
        conn_state="not_in_battle",
        domain="menu",
        timestamp=3.0,
    )
    assert fallback.detect(offline, menu) is None


def test_ground_crew_dead_edge_emits_once_and_late_hud_does_not_duplicate() -> None:
    detector = DeathDetector()
    alive = BattleState(connected=True, in_battle=True, domain="ground", timestamp=1.0)
    dead = BattleState(
        connected=True,
        in_battle=True,
        domain="ground",
        dead=True,
        dead_source="ground_crew",
        timestamp=2.0,
    )
    event = detector.detect(alive, dead)
    assert event is not None
    assert event.payload["cause"] == "ground_crew"

    dead_with_feed = BattleState(
        connected=True,
        in_battle=True,
        domain="ground",
        dead=True,
        dead_source="ground_crew",
        timestamp=3.0,
        combat={"feed": [{"id": 7, "is_my_death": True, "killer": "enemy"}]},
    )
    assert detector.detect(dead, dead_with_feed) is None


def test_battle_end_uses_normal_output_suppression_without_preempting() -> None:
    config = WtConfig(
        dry_run=False,
        global_rate_limit_seconds=12,
        output_backpressure_seconds=20,
        output_event_max_age_seconds=8,
        dialogue_intrusion_mode="critical_only",
        battle_output_quiet_window_seconds=30,
    )
    safety = SafetyGuard(config)
    safety.mark_output(critical=False, now=99.0)
    event = BattleEvent("battle_end", ts=100.0)
    chosen, chain = Arbiter(safety).decide([event], BATTLE_ENDED, 100.0)
    assert chosen is None
    assert chain[-1]["reason"] == "rate_limited(11.0s)"

    plugin = SimpleNamespace(
        cfg=config,
        _last_user_chat_at=99.0,
        _last_battle_respond_at=99.0,
        logger=None,
    )
    dispatcher = NekoDispatcher(plugin, clock=lambda: 100.0)
    dispatcher._last_push_at = 99.0
    dispatcher._last_push_priority = 10
    assert _quiet_window_suppression(plugin, event, 100.0) == (
        "user_chat_quiet_window",
        59.0,
    )
    assert dispatcher._is_backpressured(event, 100.0)
    assert _output_event_max_age_seconds(plugin, event) == 8.0
    assert not _host_interrupt_pending(event)


def test_transient_probe_state_and_map_failures_preserve_previous_snapshot() -> None:
    client = WarThunderClient()

    def malformed_map_info(path: str):
        if path == "/indicators":
            return True, {"valid": True, "army": "air"}
        if path == "/map_info.json":
            return True, None
        raise AssertionError(path)

    client._fetch = malformed_map_info
    ok, state, _indicators, _map_info = client.get_indicators_with_status()
    assert not ok
    assert state is ConnectionState.OFFLINE

    class ProbeFailureClient:
        def get_indicators_with_status(self):
            return (
                False,
                ConnectionState.OFFLINE,
                Indicators(valid=False),
                MapInfo(valid=False),
            )

    service = TelemetryService(ProbeFailureClient())
    service._state = ConnectionState.IN_BATTLE
    service._vehicle = VehicleState(valid=True)
    service._processed = {"flags": {}, "alerts": []}
    generation = service._battle_generation
    service._poll_fast()
    assert service._state is ConnectionState.IN_BATTLE
    assert service._battle_generation == generation

    class StateFailureClient:
        def get_indicators_with_status(self):
            return (
                True,
                ConnectionState.IN_BATTLE,
                Indicators(valid=True, army="air"),
                MapInfo(valid=True),
            )

        def get_state_with_status(self):
            return False, VehicleState(valid=False)

    service = TelemetryService(StateFailureClient())
    service._state = ConnectionState.IN_BATTLE
    previous_vehicle = VehicleState(valid=True, ias_kmh=400.0)
    service._vehicle = previous_vehicle
    service._processed = {"ias_kmh": 400.0, "flags": {}, "alerts": []}
    service._poll_fast()
    assert service._vehicle is previous_vehicle
    assert service._vehicle.valid

    class MapFailureClient:
        def get_map_objects_with_status(self):
            return False, []

    service = TelemetryService(MapFailureClient())
    service._state = ConnectionState.IN_BATTLE
    service._battle_generation = 4
    previous_situation = {"has_player": True, "enemies": [{"distance_m": 1000}]}
    service._situation = previous_situation
    service._poll_map(4)
    assert service._situation is previous_situation


def test_battle_identity_survives_respawn_and_changes_after_confirmed_exit() -> None:
    class BoundaryClient:
        in_battle = True

        def get_indicators_with_status(self):
            if self.in_battle:
                return (
                    True,
                    ConnectionState.IN_BATTLE,
                    Indicators(valid=True, army="tank", speed=0.0),
                    MapInfo(valid=True),
                )
            return (
                True,
                ConnectionState.NOT_IN_BATTLE,
                Indicators(valid=False),
                MapInfo(valid=False),
            )

        def get_state_with_status(self):
            return True, VehicleState(valid=True)

    client = BoundaryClient()
    service = TelemetryService(client)
    service._poll_fast()
    first = service.get_snapshot()
    battle_id = first["battle_id"]
    assert isinstance(battle_id, str) and battle_id
    assert first["life_index"] == 1
    assert first["confirmed_respawns"] == 0

    service._combat = {"my": {"deaths": 1}}
    with service._lock:
        assert not service._update_dead_state_locked(
            Indicators(valid=True, army="tank", speed=0.0, crew_current=0, crew_total=4),
            {"ias_kmh": 0.0},
            10.0,
        )
        assert service._update_dead_state_locked(
            Indicators(valid=True, army="tank", speed=8.0, crew_current=4, crew_total=4),
            {"ias_kmh": 0.0},
            11.0,
        )

    respawned = service.get_snapshot()
    assert respawned["battle_id"] == battle_id
    assert respawned["life_index"] == 2
    assert respawned["confirmed_respawns"] == 1

    parsed = parse_telemetry(respawned)
    assert parsed.battle_id == battle_id
    assert parsed.life_index == 2
    assert parsed.confirmed_respawns == 1

    client.in_battle = False
    service._poll_fast()
    assert service.get_snapshot()["battle_id"] is None

    client.in_battle = True
    service._poll_fast()
    next_battle = service.get_snapshot()
    assert next_battle["battle_id"] != battle_id
    assert next_battle["life_index"] == 1


def test_new_generation_drain_overwrites_late_old_cursor_side_effect() -> None:
    class BlockingClient(WarThunderClient):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.hud_paths: list[str] = []
            self.hud_calls = 0

        def _fetch(self, path: str):
            if path == "/mission.json":
                return True, {"status": "running", "objectives": None}
            if path.startswith("/hudmsg"):
                self.hud_paths.append(path)
                self.hud_calls += 1
                if self.hud_calls == 1:
                    self.started.set()
                    self.release.wait(2.0)
                    return True, {"events": [{"id": 1000, "msg": "old"}], "damage": []}
                return True, {"events": [{"id": 20, "msg": "buffer"}], "damage": []}
            if path.startswith("/gamechat"):
                return True, []
            raise AssertionError(path)

    client = BlockingClient()
    service = TelemetryService(client)
    service._battle_generation = 1
    service._hud_drain_pending = False
    service._chat_drain_pending = False
    thread = threading.Thread(target=service._poll_events, args=(1,))
    thread.start()
    assert client.started.wait(1.0)
    with service._lock:
        service._battle_generation = 2
        service._hud_drain_pending = True
        service._chat_drain_pending = True
    client.release.set()
    thread.join(2.0)
    assert client._last_evt == 1000

    service._poll_events(2)
    assert client.hud_paths[-1] == "/hudmsg?lastEvt=0&lastDmg=0"
    assert client._last_evt == 20


def test_situation_derives_clock_and_contact_nose_alignment() -> None:
    map_info = MapInfo(
        valid=True,
        map_min=(0.0, 0.0),
        map_max=(10_000.0, 10_000.0),
    )
    player = MapObject(
        type="aircraft",
        icon="Player",
        faction="self",
        x=0.5,
        y=0.5,
        dx=0.0,
        dy=-1.0,
    )
    enemy = MapObject(
        type="aircraft",
        icon="Fighter",
        faction="enemy",
        x=0.5,
        y=0.6,
        dx=0.0,
        dy=-1.0,
    )

    situation = analyze_situation([player, enemy], map_info)
    contact = situation["nearest_air_threat"]
    assert contact["clock"] == 6
    assert contact["relative_deg"] == -180.0
    assert contact["nose_to_player_deg"] == 0.0


def test_proximity_tracking_adds_closing_analysis_and_exit_hysteresis() -> None:
    tracker = ProximityTracker(exit_hysteresis_ratio=1.12)

    def contact(distance_m: float) -> dict:
        return {
            "x": 0.4,
            "y": 0.4,
            "icon": "Fighter",
            "type": "aircraft",
            "distance_m": distance_m,
            "relative_deg": 180.0,
        }

    first = contact(5_100)
    assert tracker.update([first], 5_000, None, 1.0) == []
    track_id = first["track_id"]

    entering = contact(4_900)
    events = tracker.update([entering], 5_000, None, 2.0)
    assert len(events) == 1
    assert events[0]["track_id"] == track_id
    assert events[0]["approaching"] is True
    assert events[0]["closing_speed_mps"] == 200.0

    # A small threshold oscillation remains inside the 12% exit band.
    assert tracker.update([contact(5_200)], 5_000, None, 3.0) == []
    assert tracker._tracks[0]["in_range"] is True
    assert tracker.update([contact(5_700)], 5_000, None, 4.0) == []
    assert tracker._tracks[0]["in_range"] is False

    reentering = contact(4_900)
    events = tracker.update([reentering], 5_000, None, 5.0)
    assert len(events) == 1
    assert events[0]["track_id"] == track_id


def test_dense_proximity_batch_prefers_closest_same_priority_contact() -> None:
    detector = ProximityDetector()
    cur = BattleState(
        connected=True,
        conn_state="in_battle",
        in_battle=True,
        vehicle_valid=True,
        domain="air",
        timestamp=10.0,
        proximity_events=[
            {
                "id": 1,
                "type": "aircraft",
                "is_air": True,
                "distance_m": 900,
                "relative_deg": 30,
            },
            {
                "id": 2,
                "type": "aircraft",
                "is_air": True,
                "distance_m": 4_500,
                "relative_deg": 20,
            },
        ],
    )
    event = detector.detect(BattleState(), cur)
    assert event is not None
    assert event.event_id == "air_threat_nearby"
    assert event.payload["distance_m"] == 900.0


def test_dense_track_association_is_not_decided_by_contact_list_order() -> None:
    tracker = ProximityTracker(assoc_dist=0.06)

    def contact(x: float) -> dict:
        return {
            "x": x,
            "y": 0.5,
            "icon": "Fighter",
            "type": "aircraft",
            "distance_m": 6_000,
        }

    old_a, old_b = contact(0.0), contact(0.05)
    tracker.update([old_a, old_b], 5_000, None, 1.0)

    # The ambiguous item comes first. A per-item greedy matcher would consume
    # old_a and force the second item onto old_b even though it nearly overlaps A.
    ambiguous, near_a = contact(0.02), contact(0.001)
    tracker.update([ambiguous, near_a], 5_000, None, 2.0)
    assert ambiguous["track_id"] == old_b["track_id"]
    assert near_a["track_id"] == old_a["track_id"]


def test_tailing_confirmation_requires_persistent_same_contact() -> None:
    detector = AirSituationDetector(tail_confirm_frames=2, tail_distance_m=1_500)

    def state(timestamp: float, track_id: int) -> BattleState:
        contact = {
            "track_id": track_id,
            "track_samples": 3,
            "type": "aircraft",
            "icon": "Fighter",
            "distance_m": 1_000,
            "relative_deg": 180.0,
            "clock": 6,
            "approaching": True,
            "closing_speed_mps": 80.0,
            "nose_to_player_deg": 5.0,
        }
        return BattleState(
            connected=True,
            conn_state="in_battle",
            in_battle=True,
            vehicle_valid=True,
            domain="air",
            timestamp=timestamp,
            situation={"air_threat_count": 1, "enemies": [contact]},
        )

    first = state(1.0, 7)
    event = detector.detect(BattleState(), first)
    assert event is not None and event.event_id == "enemy_on_six"
    second = state(2.0, 7)
    event = detector.detect(first, second)
    assert event is not None and event.event_id == "tailing_risk"
    assert event.payload["closing_speed_mps"] == 80.0

    switched = AirSituationDetector(tail_confirm_frames=2, tail_distance_m=1_500)
    one = state(1.0, 7)
    two = state(2.0, 8)
    assert switched.detect(BattleState(), one).event_id == "enemy_on_six"
    assert switched.detect(one, two) is None
    three = state(3.0, 8)
    assert switched.detect(two, three).event_id == "tailing_risk"
