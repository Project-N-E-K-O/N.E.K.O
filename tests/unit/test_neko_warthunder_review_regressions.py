from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_warthunder.core.arbiter import Arbiter
from plugin.plugins.neko_warthunder.core.contracts import IN_FLIGHT, BattleEvent, BattleState, WtConfig
from plugin.plugins.neko_warthunder.core.safety_guard import SafetyGuard
from plugin.plugins.neko_warthunder.detectors.discrete.lifecycle import BattleEndDetector


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PROCESS_DIR = PROJECT_ROOT / "plugin" / "plugins" / "neko_warthunder" / "data_layer" / "data process"


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


def _make_handler(wt_server_module, server):
    handler = wt_server_module._Handler.__new__(wt_server_module._Handler)
    emitted: list[tuple[str, str]] = []
    handler.send_header = lambda name, value: emitted.append((name, value))
    handler.server = server
    return handler, emitted


def test_data_layer_cors_is_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Origin is echoed unless cors_origins was configured explicitly."""
    # 数据层只被 Python 侧消费（adapters/telemetry_client.py 的 HTTP 客户端与
    # data_layer_process 的 health check），浏览器不直连，所以默认拒绝一切跨源
    # 读取是正确的默认值。放行范围只能通过 create_http_server(cors_origins=...)
    # 或 CLI 的 --cors-origin 显式给出。
    wt_server_module = _load_wt_server_module(monkeypatch)
    handler, emitted = _make_handler(wt_server_module, SimpleNamespace())

    for origin in (
        "https://attacker.example",
        "http://localhost:48911",
        "http://127.0.0.1:48911",
        "http://[::1]:48911",
    ):
        emitted.clear()
        handler.headers = {"Origin": origin}
        handler._cors()
        assert all(name != "Access-Control-Allow-Origin" for name, _value in emitted), origin


def test_data_layer_cors_echoes_only_explicitly_allowed_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once configured, only whitelisted Origins are echoed — never a wildcard."""
    wt_server_module = _load_wt_server_module(monkeypatch)
    handler, emitted = _make_handler(
        wt_server_module,
        SimpleNamespace(cors_origins=frozenset({"http://localhost:48911"})),
    )

    handler.headers = {"Origin": "http://localhost:48911"}
    handler._cors()
    assert ("Access-Control-Allow-Origin", "http://localhost:48911") in emitted
    assert ("Vary", "Origin") in emitted
    assert ("Access-Control-Allow-Methods", "GET, OPTIONS") in emitted

    emitted.clear()
    handler.headers = {"Origin": "https://attacker.example"}
    handler._cors()
    assert all(name != "Access-Control-Allow-Origin" for name, _value in emitted)
    assert all(value != "*" for name, value in emitted if name == "Access-Control-Allow-Origin")


def test_data_layer_cors_has_no_implicit_origin_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard fail-closed: the module must expose no implicit Origin whitelist."""
    # 历史回归：曾有一个模块级 _ALLOWED_CORS_ORIGINS 在 cors_origins 为空时兜底，
    # 而两条启动路径（adapters/data_layer_process.py 的 in-process 与子进程）都不
    # 传 cors_origins，等于该兜底恒生效，把上游 #2371 定的默认拒绝改成了默认放行。
    wt_server_module = _load_wt_server_module(monkeypatch)

    assert not hasattr(wt_server_module, "_ALLOWED_CORS_ORIGINS")
    assert not hasattr(wt_server_module, "_build_allowed_cors_origins")
    assert not hasattr(wt_server_module, "_read_main_server_port")
