# -*- coding: utf-8 -*-
"""Tests for ``POST /api/activity_signal``.

The endpoint exposes ``UserActivityTracker.push_external_system_signal``
(PR #1015) as an HTTP channel so frontend-pushed OS signals can feed
the tracker in remote / cross-platform deployments where the Python
backend can't read foreground-window / idle / CPU / GPU directly.

Coverage focus:
  * Happy path → tracker is called with the right kwargs.
  * Validation → 400 on bad shapes, 404 / 503 on missing tracker.
  * Rate limit → 429 with ``Retry-After`` when pushed too fast.
  * Partial payloads → all fields except ``lanlan_name`` are optional.
  * Throttle eviction → dict stays bounded under attack.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import system_router as system_router_module


ACTIVITY_SIGNAL_ENDPOINT = "/api/activity_signal"


def _build_mgr(*, has_tracker: bool = True):
    """A bare-bones session_manager value — only what the endpoint touches."""
    tracker = MagicMock(name="UserActivityTracker") if has_tracker else None
    mgr = SimpleNamespace()
    mgr._activity_tracker = tracker
    return mgr, tracker


@pytest.fixture(autouse=True)
def _isolate_throttle_state():
    """Each test starts with an empty throttle dict.

    Without this, test order changes whether the 5s rate limit trips —
    tests that don't care about throttle would still see 429s leaked
    from earlier tests' lanlan_name keys.
    """
    system_router_module._ACTIVITY_SIGNAL_THROTTLE.clear()
    yield
    system_router_module._ACTIVITY_SIGNAL_THROTTLE.clear()


def _build_client(monkeypatch, mgr_map: dict):
    """TestClient with ``get_session_manager`` monkey-patched.

    ``mgr_map`` is the same dict shape returned in production
    (lanlan_name → mgr-like object). Pass an empty dict to simulate
    "no characters registered".
    """
    monkeypatch.setattr(
        system_router_module, "get_session_manager", lambda: mgr_map,
    )
    app = FastAPI()
    app.include_router(system_router_module.router)
    return TestClient(app)


# ── happy path ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_payload_forwards_to_tracker(monkeypatch):
    """All fields present → tracker called with them as kwargs."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={
        "lanlan_name": "Aria",
        "window_title": "VS Code — neko",
        "process_name": "Code.exe",
        "idle_seconds": 3.5,
        "cpu_avg_30s": 27.4,
        "gpu_utilization": 65.0,
    })

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True}
    tracker.push_external_system_signal.assert_called_once()
    kwargs = tracker.push_external_system_signal.call_args.kwargs
    assert kwargs["window_title"] == "VS Code — neko"
    assert kwargs["process_name"] == "Code.exe"
    assert kwargs["idle_seconds"] == 3.5
    assert kwargs["cpu_avg_30s"] == 27.4
    assert kwargs["gpu_utilization"] == 65.0
    assert "now" in kwargs and isinstance(kwargs["now"], float)


@pytest.mark.unit
def test_lanlan_name_only_payload_accepted(monkeypatch):
    """Frontend may push just lanlan_name — tracker handles None fields."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})

    assert resp.status_code == 200
    tracker.push_external_system_signal.assert_called_once()
    kwargs = tracker.push_external_system_signal.call_args.kwargs
    # All optional fields default to None — let tracker decide defaults.
    assert kwargs["window_title"] is None
    assert kwargs["process_name"] is None
    assert kwargs["idle_seconds"] is None
    assert kwargs["cpu_avg_30s"] is None
    assert kwargs["gpu_utilization"] is None


@pytest.mark.unit
def test_lanlan_name_stripped(monkeypatch):
    """Whitespace around lanlan_name is stripped before lookup."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "  Aria  "})

    assert resp.status_code == 200
    tracker.push_external_system_signal.assert_called_once()


# ── validation errors ────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_lanlan_name_returns_400(monkeypatch):
    client = _build_client(monkeypatch, {})
    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"idle_seconds": 1.0})
    assert resp.status_code == 400
    assert "lanlan_name" in resp.json()["error"]


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_lanlan_name_returns_400(monkeypatch, blank):
    client = _build_client(monkeypatch, {})
    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": blank})
    assert resp.status_code == 400


@pytest.mark.unit
def test_invalid_json_body_returns_400(monkeypatch):
    client = _build_client(monkeypatch, {})
    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.unit
@pytest.mark.parametrize("non_object", [[], "string", 42, True])
def test_non_object_body_returns_400(monkeypatch, non_object):
    client = _build_client(monkeypatch, {})
    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=non_object)
    assert resp.status_code == 400


@pytest.mark.unit
def test_unknown_lanlan_name_returns_404(monkeypatch):
    client = _build_client(monkeypatch, {"Aria": _build_mgr()[0]})
    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Unknown"},
    )
    assert resp.status_code == 404
    assert "not registered" in resp.json()["error"]


@pytest.mark.unit
def test_mgr_without_tracker_returns_503(monkeypatch):
    """During boot a mgr can exist before its tracker is attached."""
    mgr, _ = _build_mgr(has_tracker=False)
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})

    assert resp.status_code == 503
    assert "tracker" in resp.json()["error"].lower()


@pytest.mark.unit
@pytest.mark.parametrize("payload,expected_error_fragment", [
    ({"idle_seconds": -0.1}, "idle_seconds"),
    ({"idle_seconds": "not a number"}, "idle_seconds"),
    ({"cpu_avg_30s": 100.01}, "cpu_avg_30s"),
    ({"cpu_avg_30s": -0.1}, "cpu_avg_30s"),
    ({"gpu_utilization": 150.0}, "gpu_utilization"),
    ({"gpu_utilization": "abc"}, "gpu_utilization"),
    ({"window_title": 123}, "window_title"),
    ({"process_name": ["array"]}, "process_name"),
])
def test_field_validation_400s(monkeypatch, payload, expected_error_fragment):
    """Out-of-range / wrong-type fields return 400 with a specific message."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    full_payload = {"lanlan_name": "Aria", **payload}

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=full_payload)

    assert resp.status_code == 400, resp.text
    assert expected_error_fragment in resp.json()["error"]
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_tracker_exception_returns_500(monkeypatch):
    """If tracker.push_external_system_signal raises, surface 500."""
    mgr, tracker = _build_mgr()
    tracker.push_external_system_signal.side_effect = RuntimeError("boom")
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})

    assert resp.status_code == 500
    assert "tracker rejected" in resp.json()["error"]


# ── rate limiting ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_second_push_within_interval_returns_429(monkeypatch):
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    payload = {"lanlan_name": "Aria"}

    resp1 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=payload)
    resp2 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=payload)

    assert resp1.status_code == 200
    assert resp2.status_code == 429
    assert "Retry-After" in resp2.headers
    # Retry-After is integer seconds, rounded up — must be a positive int.
    assert int(resp2.headers["Retry-After"]) >= 1
    body = resp2.json()
    assert body["error"] == "rate limited"
    assert body["retry_after_seconds"] > 0
    # Only the first push reached the tracker.
    assert tracker.push_external_system_signal.call_count == 1


@pytest.mark.unit
def test_throttle_independent_per_lanlan_name(monkeypatch):
    """Different lanlan_names have independent throttle buckets."""
    mgr_a, tracker_a = _build_mgr()
    mgr_b, tracker_b = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr_a, "Bea": mgr_b})

    resp_a = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})
    resp_b = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Bea"})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    tracker_a.push_external_system_signal.assert_called_once()
    tracker_b.push_external_system_signal.assert_called_once()


@pytest.mark.unit
def test_push_accepted_after_interval_elapses(monkeypatch):
    """After the throttle window passes the next push goes through.

    Drive ``time.time`` through monkeypatch so the test doesn't actually
    have to sleep 5 seconds.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    # Freeze time at t=1000 for the first push, then t=1006 for the second.
    fake_now = [1000.0]
    monkeypatch.setattr(
        system_router_module.time, "time", lambda: fake_now[0],
    )

    resp1 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})
    assert resp1.status_code == 200

    fake_now[0] = 1006.0  # > _EXTERNAL_SIGNAL_MIN_INTERVAL (5.0)
    resp2 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})
    assert resp2.status_code == 200, resp2.text
    assert tracker.push_external_system_signal.call_count == 2


# ── throttle dict bookkeeping ────────────────────────────────────────


@pytest.mark.unit
def test_throttle_dict_bounded(monkeypatch):
    """An attacker spraying lanlan_names can't grow the throttle dict
    unboundedly — oldest entries get trimmed when over MAX_ENTRIES.
    """
    cap = system_router_module._ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES
    # Pre-load the throttle with cap entries, all in the past so they're
    # candidates for eviction.
    base = time.time() - 3600
    for i in range(cap):
        system_router_module._ACTIVITY_SIGNAL_THROTTLE[f"old_{i}"] = base + i

    mgr, _ = _build_mgr()
    client = _build_client(monkeypatch, {"NewArrival": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "NewArrival"},
    )
    assert resp.status_code == 200

    # New entry is in; total size still <= cap.
    assert "NewArrival" in system_router_module._ACTIVITY_SIGNAL_THROTTLE
    assert (
        len(system_router_module._ACTIVITY_SIGNAL_THROTTLE) <= cap
    )
    # Oldest entry should have been evicted.
    assert "old_0" not in system_router_module._ACTIVITY_SIGNAL_THROTTLE
