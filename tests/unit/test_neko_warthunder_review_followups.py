from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_warthunder import NekoWarthunderPlugin
from plugin.plugins.neko_warthunder.adapters.data_layer_process import (
    DataLayerProcessManager,
)
from plugin.plugins.neko_warthunder.core.contracts import (
    COMBAT_STRESS,
    BattleEvent,
    BattleState,
    WtConfig,
)
from plugin.plugins.neko_warthunder.core.scenario import ScenarioResolver
from plugin.plugins.neko_warthunder.detectors._base import (
    ConditionDetector,
    DetectorEngine,
)


def _load_wt_server():
    module_name = "_neko_warthunder_review_followup_wt_server"
    if module_name in sys.modules:
        return sys.modules[module_name]
    data_dir = (
        Path(__file__).resolve().parents[2]
        / "plugin"
        / "plugins"
        / "neko_warthunder"
        / "data_layer"
        / "data process"
    )
    spec = importlib.util.spec_from_file_location(module_name, data_dir / "wt_server.py")
    assert spec is not None and spec.loader is not None
    old_path = list(sys.path)
    sys.path.insert(0, str(data_dir))
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def test_invalid_url_does_not_retry_stale_python_runner(tmp_path: Path) -> None:
    data_dir = tmp_path / "data_layer" / "data process"
    data_dir.mkdir(parents=True)
    (data_dir / "wt_server.py").write_text("", encoding="utf-8")
    manager = DataLayerProcessManager(
        WtConfig(
            data_layer_auto_start=True,
            data_layer_url="http://192.0.2.10:8112",
        ),
        plugin_root=tmp_path,
        health_check=lambda _url, _timeout: False,
    )
    manager._python_cmd = ["stale-python"]

    status = manager.start_if_needed()

    assert status["mode"] == "failed"
    assert "managed_data_layer_requires_loopback_url" in status["last_error"]
    assert manager._failed_python_prefixes == set()
    assert status["python_cmd"] == ""


def test_invalid_port_does_not_blacklist_python_runners(tmp_path: Path) -> None:
    data_dir = tmp_path / "data_layer" / "data process"
    data_dir.mkdir(parents=True)
    (data_dir / "wt_server.py").write_text("", encoding="utf-8")
    manager = DataLayerProcessManager(
        WtConfig(
            data_layer_auto_start=True,
            data_layer_url="http://127.0.0.1:not-a-port",
        ),
        plugin_root=tmp_path,
        health_check=lambda _url, _timeout: False,
    )

    status = manager.start_if_needed()

    assert status["mode"] == "failed"
    assert "port" in status["last_error"].lower()
    assert manager._failed_python_prefixes == set()
    assert status["python_cmd"] == ""


def test_large_replay_scrub_is_not_mistaken_for_midnight_wrap() -> None:
    module = _load_wt_server()
    service = object.__new__(module.TelemetryService)
    service._replay = False
    service._last_game_time = None
    service._mission_status = "running"
    service._mission_running_seen = True
    service._battle_entry_ts = 0.0

    service._detect_replay_locked(SimpleNamespace(game_time_sec=20 * 3600.0), 1000.0)
    service._detect_replay_locked(SimpleNamespace(game_time_sec=1 * 3600.0), 1001.0)

    assert service._replay is True


@pytest.mark.parametrize(
    ("previous_game_time", "current_game_time", "expected_replay"),
    [
        (23 * 3600.0 + 59 * 60.0 + 59.0, 0.0, False),
        (23 * 3600.0, 1 * 3600.0 + 1.0, True),
    ],
)
def test_midnight_wrap_boundaries(
    previous_game_time: float,
    current_game_time: float,
    expected_replay: bool,
) -> None:
    module = _load_wt_server()
    service = object.__new__(module.TelemetryService)
    service._replay = False
    service._last_game_time = None
    service._mission_status = "running"
    service._mission_running_seen = True
    service._battle_entry_ts = 0.0

    service._detect_replay_locked(
        SimpleNamespace(game_time_sec=previous_game_time),
        1000.0,
    )
    service._detect_replay_locked(
        SimpleNamespace(game_time_sec=current_game_time),
        1001.0,
    )

    assert service._replay is expected_replay


def test_nonfatal_owned_feed_entry_enters_combat_stress() -> None:
    resolver = ScenarioResolver()

    def state(**changes) -> BattleState:
        values = {
            "connected": True,
            "conn_state": "in_battle",
            "in_battle": True,
            "vehicle_valid": True,
            "domain": "air",
        }
        values.update(changes)
        return BattleState(**values)

    resolver.resolve(state(), 1000.0, 6)
    resolver.resolve(state(), 1007.0, 6)
    damaged = state(
        combat={
            "feed": [
                {
                    "id": 8,
                    "action_type": "severely_damaged",
                    "is_kill": False,
                    "involves_me": True,
                }
            ]
        }
    )

    assert resolver.resolve(damaged, 1008.0, 6) == COMBAT_STRESS
    assert resolver.current_stress_reasons(1008.0) == frozenset({"damage"})


def test_once_per_battle_detector_spends_only_after_committed_delivery() -> None:
    detector = ConditionDetector(
        "low_fuel",
        [("fuel_low", "fuel_critical")],
        confirm_enter=1,
        confirm_exit=2,
        once_per_battle=True,
    )
    engine = DetectorEngine([detector])
    clear = BattleState()
    low = BattleState(flags={"fuel_low": True})

    assert [event.event_id for event in engine.feed(clear, low)] == ["low_fuel"]
    assert engine.feed(low, clear) == []
    assert engine.feed(clear, clear) == []
    assert [event.event_id for event in engine.feed(clear, low)] == ["low_fuel"]

    engine.mark_delivered("low_fuel")
    assert engine.feed(low, clear) == []
    assert engine.feed(clear, clear) == []
    assert engine.feed(clear, low) == []


def test_once_per_battle_detector_rearms_after_dry_run_mode_switch() -> None:
    detector = ConditionDetector(
        "low_fuel",
        [("fuel_low", "fuel_critical")],
        confirm_enter=1,
        confirm_exit=2,
        once_per_battle=True,
    )
    engine = DetectorEngine([detector])
    low = BattleState(flags={"fuel_low": True})

    assert [event.event_id for event in engine.feed(BattleState(), low)] == ["low_fuel"]

    engine.rearm_uncommitted_once_per_battle()

    assert [event.event_id for event in engine.feed(low, low)] == ["low_fuel"]
    engine.mark_delivered("low_fuel")
    engine.rearm_uncommitted_once_per_battle()
    assert engine.feed(low, low) == []


def test_dry_run_does_not_mark_once_per_battle_event_delivered() -> None:
    event = BattleEvent("low_fuel", ts=time.time())
    marked: list[str] = []
    pushed: list[tuple[BattleEvent, bool]] = []
    plugin = object.__new__(NekoWarthunderPlugin)
    plugin.cfg = WtConfig(dry_run=True, global_rate_limit_seconds=0)
    plugin.engine = SimpleNamespace(
        feed=lambda _prev, _cur: [event],
        reset=lambda: None,
        mark_delivered=marked.append,
    )
    plugin.resolver = SimpleNamespace(
        resolve=lambda _cur, _now, _grace: "IN_FLIGHT",
        reset=lambda: None,
        current_stress_reasons=lambda _now: frozenset(),
    )
    plugin.arbiter = SimpleNamespace(
        checkpoint=lambda: None,
        decide=lambda _candidates, _scenario, _now: (event, []),
        restore=lambda _checkpoint: None,
        reset=lambda: None,
    )
    plugin.safety = SimpleNamespace(
        output_clock_checkpoint=lambda: None,
        restore_output_clock=lambda _checkpoint: None,
        status=lambda: {},
        record_failure=lambda _now: None,
    )
    plugin.timeline = SimpleNamespace(
        record_stage=lambda **_kwargs: None,
        record_decision=lambda **_kwargs: None,
    )
    def push_event(selected: BattleEvent, *, dry_run: bool) -> str:
        pushed.append((selected, dry_run))
        return (
            "dry_run(event=low_fuel/enter/warning)" if dry_run else "pushed()"
        )

    plugin.dispatcher = SimpleNamespace(push_event=push_event)
    plugin.logger = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    plugin._pending_dispatch_event = None
    plugin._record_blocked_free_text_sources = lambda _cur: None
    plugin._record_deferred_hud_notices = lambda _cur: None
    plugin._suppress_takeoff_grace = lambda candidates, _cur, _now: candidates
    plugin._annotate_runtime_context = lambda candidates, _cur, _now: candidates

    plugin._evaluate(BattleState(), BattleState(connected=True, in_battle=True))

    assert pushed == [(event, True)]
    assert marked == []


def test_enabling_real_output_rearms_uncommitted_once_per_battle_event() -> None:
    rearmed: list[bool] = []
    plugin = object.__new__(NekoWarthunderPlugin)
    plugin.cfg = WtConfig(dry_run=True)
    plugin._session_dry_run_override = None
    plugin.engine = SimpleNamespace(
        rearm_uncommitted_once_per_battle=lambda: rearmed.append(True)
    )

    asyncio.run(plugin.set_dry_run(False))

    assert plugin.cfg.dry_run is False
    assert rearmed == [True]
