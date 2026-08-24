from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_warthunder import NekoWarthunderPlugin
from plugin.plugins.neko_warthunder.adapters import data_layer_process
from plugin.plugins.neko_warthunder.adapters.data_layer_process import (
    DataLayerProcessManager,
)
from plugin.plugins.neko_warthunder.adapters.neko_dispatcher import (
    NekoDispatcher,
    PushMessageSubmissionRejected,
    ensure_push_message_submitted,
)
from plugin.plugins.neko_warthunder.core.contracts import (
    COMBAT_STRESS,
    BattleEvent,
    BattleState,
    WtConfig,
)
from plugin.plugins.neko_warthunder.data_layer.data_process.wt_events import parse_award
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
        / "data_process"
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
    data_dir = tmp_path / "data_layer" / "data_process"
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
    data_dir = tmp_path / "data_layer" / "data_process"
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


def test_windows_store_python_alias_is_not_a_runtime() -> None:
    alias = r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\python.exe"

    assert data_layer_process._looks_like_python(alias) is False
    assert data_layer_process._looks_like_python(r"C:\Python311\python.exe") is True


def test_windows_python_path_is_recognized_on_posix_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(data_layer_process, "Path", PurePosixPath)

    assert data_layer_process._looks_like_python(r"C:\Python311\python.exe") is True


def test_windows_store_py_launcher_is_not_a_runtime_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias = r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\py.exe"
    monkeypatch.setattr(data_layer_process.sys, "executable", "projectneko_server.exe")
    monkeypatch.setattr(data_layer_process.sys, "_base_executable", "", raising=False)
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setattr(
        data_layer_process.shutil,
        "which",
        lambda name: alias if name == "py" else None,
    )

    assert data_layer_process._python_command_prefixes() == []


def test_base_python_precedes_uv_venv_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = r"C:\Python311\python.exe"
    launcher = r"C:\project\.venv\Scripts\python.exe"
    monkeypatch.setattr(data_layer_process.sys, "_base_executable", base, raising=False)
    monkeypatch.setattr(data_layer_process.sys, "executable", launcher)
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setattr(data_layer_process.shutil, "which", lambda _name: None)

    assert data_layer_process._python_command_prefixes() == [[base], [launcher]]


def test_explicit_push_rejection_is_not_committed() -> None:
    observed: list[dict[str, object]] = []
    plugin = SimpleNamespace(
        cfg=WtConfig(
            dry_run=False,
            global_rate_limit_seconds=0,
            output_backpressure_seconds=0,
            dialogue_intrusion_mode="allow_interrupt",
            user_chat_quiet_window_seconds=0,
            battle_output_quiet_window_seconds=0,
        ),
        logger=SimpleNamespace(warning=lambda _message: None),
        push_message=lambda **_kwargs: {"submitted": False, "reason": "backpressure"},
        _last_user_chat_at=0.0,
        _last_user_chat_mode="unknown",
        _last_battle_respond_at=0.0,
    )
    dispatcher = NekoDispatcher(plugin, clock=lambda: 100.0)
    dispatcher._observer = SimpleNamespace(
        record_event=lambda _event, **kwargs: observed.append(kwargs)
    )

    with pytest.raises(PushMessageSubmissionRejected, match="backpressure"):
        dispatcher.push_event(BattleEvent("spawn", ts=100.0), dry_run=False)

    assert dispatcher._last_push_at is None
    assert observed[-1]["stage"] == "dispatcher_failed"
    assert observed[-1]["reason"] == "backpressure"
    assert observed[-1]["pushed"] is False


def test_push_submission_receipt_remains_compatible_with_old_sdk() -> None:
    ensure_push_message_submitted(None)
    ensure_push_message_submitted({"submitted": True})


def test_runtime_state_migrates_to_external_data_without_touching_legacy(tmp_path: Path) -> None:
    legacy = tmp_path / "plugin" / ".runtime_state.json"
    primary = tmp_path / "storage" / "data" / ".runtime_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"player_name": "legacy"}', encoding="utf-8")
    plugin = object.__new__(NekoWarthunderPlugin)
    plugin.logger = SimpleNamespace(warning=lambda _message: None)
    plugin._legacy_runtime_state_path = legacy
    plugin._runtime_state_path = primary

    plugin._save_runtime_state({"broadcast_frequency": "normal"})

    assert json.loads(primary.read_text(encoding="utf-8")) == {
        "broadcast_frequency": "normal",
        "player_name": "legacy",
    }
    assert legacy.read_text(encoding="utf-8") == '{"player_name": "legacy"}'


def test_runtime_state_uses_new_sdk_data_path_when_available(tmp_path: Path) -> None:
    plugin = object.__new__(NekoWarthunderPlugin)
    plugin.logger = SimpleNamespace(warning=lambda _message: None)
    plugin._legacy_runtime_state_path = tmp_path / "plugin" / ".runtime_state.json"
    plugin.data_path = lambda *parts: tmp_path / "storage" / "data" / Path(*parts)

    assert plugin._resolve_runtime_state_path() == tmp_path / "storage" / "data" / ".runtime_state.json"


def test_startup_log_error_code_excludes_runner_paths() -> None:
    raw = r"all_data_layer_runners_failed: C:\Users\tester\python.exe: FileNotFoundError"

    assert NekoWarthunderPlugin._diagnostic_error_code(raw) == "all_data_layer_runners_failed"


def test_packaged_runtime_uses_embedded_data_layer_without_system_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data_layer" / "data_process"
    data_dir.mkdir(parents=True)
    (data_dir / "wt_server.py").write_text("", encoding="utf-8")
    embedded_process = SimpleNamespace(poll=lambda: None, pid=4321)
    seen: dict[str, object] = {}

    monkeypatch.setattr(data_layer_process, "_is_packaged_runtime", lambda: True)
    monkeypatch.setattr(
        data_layer_process,
        "_python_command_prefixes",
        lambda: pytest.fail("packaged runtime must not inspect system Python"),
    )

    def fake_spawn(data_process_dir: Path, *, host: str, port: int):
        seen.update(path=data_process_dir, host=host, port=port)
        return embedded_process

    monkeypatch.setattr(data_layer_process, "_spawn_embedded_data_layer", fake_spawn)
    manager = DataLayerProcessManager(WtConfig(), plugin_root=tmp_path)

    process = manager._spawn()

    assert process is embedded_process
    assert manager._python_cmd == ["embedded"]
    assert seen == {"path": data_dir, "host": "127.0.0.1", "port": 8112}


def test_embedded_loader_imports_packaged_data_layer_module() -> None:
    module = data_layer_process._load_wt_server_module()

    assert module.__name__.endswith("data_layer.data_process.wt_server")
    assert callable(module.create_http_server)


def test_embedded_data_layer_serves_health_without_system_python(tmp_path: Path) -> None:
    process = data_layer_process._spawn_embedded_data_layer(
        tmp_path,
        host="127.0.0.1",
        port=0,
    )
    port = int(process.httpd.server_address[1])
    try:
        assert data_layer_process.check_data_layer_health(
            f"http://127.0.0.1:{port}",
            1.0,
        )
    finally:
        process.terminate()
        process.wait(timeout=3.0)


@pytest.mark.parametrize(
    ("message", "code", "label"),
    [
        ("Ace (P-51D) First Strike!", "first_blood", "First Strike"),
        ("Ace (P-51D) first blood!", "first_blood", "first blood"),
        ("Ace (P-51D) FINAL BLOW!", "final_blow", "FINAL BLOW"),
        (
            'Ace (P-51D) earned the award "Double Strike!"',
            "double_kill",
            "Double Strike",
        ),
        (
            "Ace (P-51D) earned an award 'Triple Kill!'",
            "triple_kill",
            "Triple Kill",
        ),
        (
            'Ace (P-51D) EARNED THE AWARD "Multi Strike x4!"',
            "multi_kill",
            "Multi Strike x4",
        ),
    ],
)
def test_english_awards_are_recognized(
    message: str,
    code: str,
    label: str,
) -> None:
    award = parse_award(message, event_id=7, time=11)

    assert award is not None
    assert award.player == "Ace"
    assert award.vehicle == "P-51D"
    assert award.code == code
    assert award.label == label
    assert award.notable is True


def test_final_blow_kill_description_is_not_misclassified_as_award() -> None:
    assert (
        parse_award("Ace (P-51D) dealt the final blow Rival (Bf 109)")
        is None
    )


def test_english_award_offsets_are_taken_from_original_unicode_text() -> None:
    award = parse_award("Straße first blood!", event_id=8, time=12)

    assert award is not None
    assert award.player == "Straße"
    assert award.code == "first_blood"
    assert award.label == "first blood"


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
    clear = BattleState(flags={})
    assert engine.feed(low, clear) == []
    assert engine.feed(clear, clear) == []
    assert engine.feed(clear, low) == []


def test_dry_run_does_not_mark_once_per_battle_event_delivered() -> None:
    event = BattleEvent("low_fuel", ts=time.time())
    marked: list[str] = []
    pushed: list[tuple[BattleEvent, bool]] = []
    restored_arbiter: list[object] = []
    restored_output_clock: list[object] = []
    arbiter_checkpoint = object()
    output_clock_checkpoint = object()
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
        checkpoint=lambda: arbiter_checkpoint,
        decide=lambda _candidates, _scenario, _now: (event, []),
        restore=restored_arbiter.append,
        reset=lambda: None,
    )
    plugin.safety = SimpleNamespace(
        output_clock_checkpoint=lambda: output_clock_checkpoint,
        restore_output_clock=restored_output_clock.append,
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
    assert restored_arbiter == [arbiter_checkpoint]
    assert restored_output_clock == [output_clock_checkpoint]


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
