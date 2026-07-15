from __future__ import annotations

import importlib.util
import os
import platform
from pathlib import Path

import pytest

from plugin.plugins.neko_warthunder.core.arbiter import Arbiter
from plugin.plugins.neko_warthunder.core.contracts import IN_FLIGHT, BattleEvent, BattleState, WtConfig
from plugin.plugins.neko_warthunder.core.safety_guard import SafetyGuard
from plugin.plugins.neko_warthunder.detectors.discrete.lifecycle import BattleEndDetector


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PROCESS_DIR = PROJECT_ROOT / "plugin" / "plugins" / "neko_warthunder" / "data_layer" / "data process"
PLUGIN_TEST_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "plugin-tests.yml"


def test_warthunder_review_regressions_are_in_plugin_ci() -> None:
    workflow = PLUGIN_TEST_WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("tests/unit/test_neko_warthunder_review_regressions.py") == 3


def test_coalesced_kill_bypasses_post_flush_event_cooldown() -> None:
    config = WtConfig(global_rate_limit_seconds=0, kill_coalesce_window_seconds=6)
    arbiter = Arbiter(SafetyGuard(config))

    first = BattleEvent("you_killed", payload={"kill_count": 1}, ts=0)
    selected, chain = arbiter.decide([first], IN_FLIGHT, now=0)
    assert selected is None
    assert chain[-1]["reason"] == "kill_coalescing"

    selected, _chain = arbiter.decide([], IN_FLIGHT, now=6)
    assert selected is not None
    assert selected.event_id == "you_killed"

    second = BattleEvent("you_killed", payload={"kill_count": 1}, ts=7)
    selected, chain = arbiter.decide([second], IN_FLIGHT, now=7)
    assert selected is None
    assert chain[-1]["reason"] == "kill_coalescing"


def test_success_mission_emits_battle_end() -> None:
    detector = BattleEndDetector()
    prev = BattleState(mission_status="running")
    cur = BattleState(mission_status="success", timestamp=42)

    event = detector.detect(prev, cur)

    assert event is not None
    assert event.event_id == "battle_end"
    assert event.payload["result"] == "success"


def _load_wt_server_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(DATA_PROCESS_DIR))
    module_path = DATA_PROCESS_DIR / "wt_server.py"
    spec = importlib.util.spec_from_file_location("neko_warthunder_review_wt_server", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_data_layer_cors_only_echoes_approved_neko_origins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    wt_server_module = _load_wt_server_module(monkeypatch)
    handler = wt_server_module._Handler.__new__(wt_server_module._Handler)
    emitted: list[tuple[str, str]] = []
    handler.send_header = lambda name, value: emitted.append((name, value))

    handler.headers = {"Origin": "https://attacker.example"}
    handler._cors()
    assert ("Access-Control-Allow-Origin", "https://attacker.example") not in emitted
    assert all(value != "*" for name, value in emitted if name == "Access-Control-Allow-Origin")

    emitted.clear()
    handler.headers = {"Origin": "http://localhost:48911"}
    handler._cors()
    assert ("Access-Control-Allow-Origin", "http://localhost:48911") in emitted
    assert ("Vary", "Origin") in emitted
    assert ("Access-Control-Allow-Methods", "GET, OPTIONS") in emitted


def test_data_layer_cors_uses_configured_main_server_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "43102")
    monkeypatch.setenv("MAIN_SERVER_PORT", "43103")
    wt_server_module = _load_wt_server_module(monkeypatch)

    assert wt_server_module._ALLOWED_CORS_ORIGINS == frozenset(
        {
            "http://127.0.0.1:43102",
            "http://localhost:43102",
            "http://[::1]:43102",
        }
    )

    handler = wt_server_module._Handler.__new__(wt_server_module._Handler)
    emitted: list[tuple[str, str]] = []
    handler.send_header = lambda name, value: emitted.append((name, value))

    handler.headers = {"Origin": "http://localhost:43102"}
    handler._cors()
    assert ("Access-Control-Allow-Origin", "http://localhost:43102") in emitted

    emitted.clear()
    handler.headers = {"Origin": "http://localhost:48911"}
    handler._cors()
    assert all(name != "Access-Control-Allow-Origin" for name, _value in emitted)


def test_data_layer_cors_supports_legacy_main_server_port_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    monkeypatch.setenv("MAIN_SERVER_PORT", "43103")
    wt_server_module = _load_wt_server_module(monkeypatch)

    assert "http://localhost:43103" in wt_server_module._ALLOWED_CORS_ORIGINS
    assert "http://localhost:48911" not in wt_server_module._ALLOWED_CORS_ORIGINS


def test_data_layer_cors_uses_electron_port_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(os.path, "expanduser", lambda _path: str(tmp_path / "ignored-home"))
    config_dir = tmp_path / "Library" / "Application Support" / "N.E.K.O"
    config_dir.mkdir(parents=True)
    (config_dir / "port_config.json").write_text(
        '{"MAIN_SERVER_PORT": 43104}',
        encoding="utf-8",
    )

    wt_server_module = _load_wt_server_module(monkeypatch)

    assert "http://localhost:43104" in wt_server_module._ALLOWED_CORS_ORIGINS
    assert "http://localhost:48911" not in wt_server_module._ALLOWED_CORS_ORIGINS
