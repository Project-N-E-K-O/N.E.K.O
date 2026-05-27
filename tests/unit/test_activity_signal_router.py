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
def test_lanlan_name_only_payload_rejected_400(monkeypatch):
    """A payload with no signal fields must 400, not push synthetic zeros.

    Codex F6 (PR #1477): the tracker's ``push_external_system_signal``
    defaults missing numerics to ``0.0`` and unconditionally marks
    ``os_signals_available=True``. Accepting an all-None push therefore
    overwrites real state with "idle=0 / cpu=0 / no window" — actively
    biases activity classification. Defence-in-depth at the endpoint:
    require ≥ 1 signal field. Frontend client also skips empty bridge
    snapshots, but the server side closes the same hole for native /
    malicious callers.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria"})

    assert resp.status_code == 400
    assert "signal field" in resp.json()["error"]
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("blank_payload", [
    {"window_title": ""},
    {"window_title": "   "},
    {"process_name": ""},
    {"process_name": "\t\n  "},
    {"window_title": "", "process_name": ""},
    {"window_title": "  ", "process_name": "\t"},
])
def test_blank_string_payload_rejected_400(monkeypatch, blank_payload):
    """Blank / whitespace-only strings count as absent for the empty-signal guard.

    CodeRabbit F7 (PR #1477): the original F6 fix only treated ``None``
    as absent. ``{"window_title": ""}`` (or whitespace-only) would pass
    the validator, slip through the all-None check, and still pollute
    tracker state because the tracker treats them as "saw the desktop,
    no foreground window" while every numeric defaults to 0.0. Same
    poisoning as the empty payload, just with extra noise.

    Carrying a blank string + no numerics tells the tracker literally
    nothing, so reject for the same reason a fully-empty payload is
    rejected.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    full_payload = {"lanlan_name": "Aria", **blank_payload}

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=full_payload)

    assert resp.status_code == 400, resp.text
    assert "signal field" in resp.json()["error"]
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("blank_payload", [
    # Blank string PAIRED with a real numeric → not "empty payload",
    # the numeric carries the signal. Validator-level normalisation of
    # blank → None is deliberately NOT applied so downstream tracker
    # logic can still distinguish "" ("saw desktop, no title") from
    # None ("no observation") if it ever wants to.
    {"window_title": "", "idle_seconds": 5},
    {"process_name": "  ", "cpu_avg_30s": 25.0},
])
def test_blank_string_paired_with_signal_accepted(monkeypatch, blank_payload):
    """Blank strings are OK as long as at least one real signal is present."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    full_payload = {"lanlan_name": "Aria", **blank_payload}

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=full_payload)

    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("single_field,value", [
    ("window_title", "Finder"),
    ("process_name", "Code.exe"),
    ("idle_seconds", 5),
    ("cpu_avg_30s", 25.5),
    ("gpu_utilization", 50.0),
])
def test_single_field_payload_accepted(monkeypatch, single_field, value):
    """Any single signal field is enough — partial snapshot still useful.

    Bridge platforms differ in coverage (Wayland often can't read
    window, Mac without Screen Recording perm can't either, AMD/Intel
    no GPU); a partial push is better than no push as long as ≥ 1
    real datum is in there.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", single_field: value},
    )

    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
def test_lanlan_name_stripped(monkeypatch):
    """Whitespace around lanlan_name is stripped before lookup.

    Carries one signal field (``idle_seconds``) to pass the empty-payload
    guard (Codex F6); the strip behaviour itself is independent.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "  Aria  ", "idle_seconds": 0},
    )

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
        json={"lanlan_name": "Unknown", "idle_seconds": 0},
    )
    assert resp.status_code == 404
    assert "not registered" in resp.json()["error"]


@pytest.mark.unit
def test_mgr_without_tracker_returns_503(monkeypatch):
    """During boot a mgr can exist before its tracker is attached."""
    mgr, _ = _build_mgr(has_tracker=False)
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})

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


# ── Origin-present same-origin gate ──────────────────────────────────


@pytest.mark.unit
def test_no_origin_header_accepted(monkeypatch):
    """``curl`` / Node / native scripts send no Origin → must be allowed.

    The Origin gate is intentionally permissive in the no-Origin case
    because:
      - Browsers always send Origin on POST (since the 2024-ish spec
        update), so missing Origin reliably means non-browser caller
      - Mandatory CSRF tokens would block legitimate native callers
        without a clean path forward; defer to follow-up PR
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})

    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
def test_same_origin_browser_request_accepted(monkeypatch):
    """Browser fetch with matching Origin (== ``request.base_url``) passes."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Origin": "http://testserver"},  # TestClient's base_url
    )

    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("evil_origin", [
    "https://evil.com",
    "http://attacker.example.com:8080",
    "https://localhost.evil.com",
])
def test_cross_site_origin_blocked_with_403(monkeypatch, evil_origin):
    """Drive-by browser fetch from off-origin page → 403.

    This is the threat that the Origin gate exists to block: a
    malicious page on attacker.com calling our endpoint via fetch().
    Browser always sends Origin = the page's origin; since that's not
    our base_url, we reject before tracker dispatch.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Origin": evil_origin},
    )

    assert resp.status_code == 403, resp.text
    assert "origin" in resp.json()["error"].lower()
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_referer_used_when_no_origin(monkeypatch):
    """``_get_request_origin`` falls back to Referer when Origin absent.

    Drive-by CSRF mitigation must work even when an attacker omits
    Origin (some older clients / browser bugs do this) — Referer is
    the practical fallback, and our helper already implements it.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    # Referer from off-origin page → should still block
    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Referer": "https://evil.com/some/path"},
    )

    assert resp.status_code == 403
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("header_name,opaque_value", [
    ("Origin", "null"),
    ("Origin", "NULL"),  # case-insensitive
    ("Referer", "null"),  # Referer fallback also rejects "null"
])
def test_opaque_origin_null_rejected(monkeypatch, header_name, opaque_value):
    """``Origin: null`` (and Referer: null) from sandboxed iframes / file://
    contexts must be 403, not fall through to the no-Origin allow path.

    Codex P1 (F5 on PR #1477): browsers emit the literal string "null"
    for opaque origins. ``urlsplit("null")`` parses into empty scheme +
    netloc → ``_normalize_origin_value`` returns "" → the no-Origin
    allow branch fires, bypassing the gate. Without an explicit raw
    check, ``<iframe sandbox>`` on attacker.com could fetch our
    endpoint and spoof signals.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 5},
        headers={header_name: opaque_value},
    )

    assert resp.status_code == 403, resp.text
    assert "origin" in resp.json()["error"].lower()
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_unparseable_origin_treated_as_absent(monkeypatch):
    """Garbage Origin (can't parse scheme/host) → treated as no-Origin.

    Mirrors screenshot-router test
    ``test_interactive_screenshot_blocks_browser_requests_with_unparseable_origin``
    inverted: unparseable means "we can't tell where this came from",
    so we fall through to the no-Origin path (allow). The endpoint's
    rate limit + lanlan_name lookup still bound impact.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Origin": "not a url"},
    )

    # Origin couldn't be normalised → counts as no Origin → allowed
    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("field", ["idle_seconds", "cpu_avg_30s", "gpu_utilization"])
def test_oversized_integer_rejected_400_not_500(monkeypatch, field):
    """JSON-valid huge ints that ``float()`` can't represent → 400, not 500.

    Codex F9 on PR #1477: ``float(10**400)`` raises ``OverflowError``,
    which the original ``except (TypeError, ValueError)`` missed →
    request crashes as 500 instead of being a clean validation 400.
    Cheap DOS / crash-spam vector for anyone POSTing arbitrary big ints.

    JSON spec doesn't bound integer precision so this is a legit
    payload from the parser's perspective — Starlette's ``json.loads``
    happily produces a Python big-int and hands it to us.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    # JSON: huge int literal. Pass as raw bytes since ``json=`` helper
    # would serialise it as scientific notation that fits in a double.
    body = (
        '{"lanlan_name":"Aria","' + field + '":'
        + '1' + '0' * 400  # 10**400 — clearly beyond double's range
        + '}'
    ).encode()

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400, (
        f"oversized int should 400 (clean validation), got {resp.status_code}"
    )
    assert field in resp.json()["error"]
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("field", ["idle_seconds", "cpu_avg_30s", "gpu_utilization"])
@pytest.mark.parametrize("bool_value", [True, False])
def test_boolean_rejected_in_numeric_fields(monkeypatch, field, bool_value):
    """Booleans must be 400'd before ``float()`` coerces them to 0.0/1.0.

    Codex F8 on PR #1477: Python's ``bool`` is a subclass of ``int``,
    so ``float(True) == 1.0`` and ``float(False) == 0.0`` silently
    succeed and pass the range checks. A payload like
    ``{"idle_seconds": true}`` would otherwise spoof a "user just
    acted" signal; ``{"cpu_avg_30s": true}`` would spoof 1% utilisation.
    Both are fabricated telemetry that biases activity classification.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    payload = {"lanlan_name": "Aria", field: bool_value}

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json=payload)

    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert field in err, f"error should name the offending field, got: {err!r}"
    assert "number" in err.lower()
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("field", ["idle_seconds", "cpu_avg_30s", "gpu_utilization"])
@pytest.mark.parametrize("bad_token", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_rejected(monkeypatch, field, bad_token):
    """``NaN`` / ``±Infinity`` must be 400'd before they reach the tracker.

    ``float('nan') < lo`` is silently ``False``, so a missing
    ``math.isfinite`` check let these slip past the range guards
    (CodeRabbit + Codex P2 on PR #1477). Worse, downstream JSON
    serialisation of NaN/Infinity crashes since RFC 8259 forbids them.

    We bypass TestClient's ``json=`` helper (which uses httpx's strict
    ``allow_nan=False`` serialiser) and send raw bytes via ``content=``
    — Python's stdlib ``json.loads`` (what Starlette uses) accepts the
    non-standard ``NaN`` / ``Infinity`` tokens, which is exactly the
    in-the-wild path an attacker / buggy client would take.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    body = (
        '{"lanlan_name":"Aria","' + field + '":' + bad_token + '}'
    ).encode()

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert field in err, f"error should name the offending field, got: {err!r}"
    assert "finite" in err.lower(), f"error should mention 'finite', got: {err!r}"
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_tracker_exception_returns_500(monkeypatch):
    """If tracker.push_external_system_signal raises, surface 500."""
    mgr, tracker = _build_mgr()
    tracker.push_external_system_signal.side_effect = RuntimeError("boom")
    client = _build_client(monkeypatch, {"Aria": mgr})

    resp = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})

    assert resp.status_code == 500
    assert "tracker rejected" in resp.json()["error"]


# ── rate limiting ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_second_push_within_interval_returns_429(monkeypatch):
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr})
    payload = {"lanlan_name": "Aria", "idle_seconds": 0}

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

    resp_a = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})
    resp_b = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Bea", "idle_seconds": 0})

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

    resp1 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})
    assert resp1.status_code == 200

    fake_now[0] = 1006.0  # > _EXTERNAL_SIGNAL_MIN_INTERVAL (5.0)
    resp2 = client.post(ACTIVITY_SIGNAL_ENDPOINT, json={"lanlan_name": "Aria", "idle_seconds": 0})
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
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "NewArrival", "idle_seconds": 0},
    )
    assert resp.status_code == 200

    # New entry is in; total size still <= cap.
    assert "NewArrival" in system_router_module._ACTIVITY_SIGNAL_THROTTLE
    assert (
        len(system_router_module._ACTIVITY_SIGNAL_THROTTLE) <= cap
    )
    # Oldest entry should have been evicted.
    assert "old_0" not in system_router_module._ACTIVITY_SIGNAL_THROTTLE
