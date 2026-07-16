# -*- coding: utf-8 -*-
"""Tests for ``POST /api/activity_signal``.

The endpoint exposes ``UserActivityTracker.push_external_system_signal``
(PR #1015) as an HTTP channel so frontend-pushed OS signals can feed
the tracker in remote / cross-platform deployments where the Python
backend can't read foreground-window / idle / CPU / GPU directly.

Coverage focus:
  * Auth → ``_validate_local_mutation_request`` guard fires before any
    business logic (issue #1479 Step 2: unified CSRF + Origin gate
    replaces PR #1477's interim Origin-only check).
  * Happy path → tracker is called with the right kwargs.
  * Validation → 400 on bad shapes, 404 / 503 on missing tracker.
  * Rate limit → 429 with ``Retry-After`` when pushed too fast.
  * Partial payloads → all fields except ``lanlan_name`` are optional.
  * Throttle eviction → dict stays bounded under attack.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import activity_signal as system_router_module
from main_routers.system_router import _shared as system_router_shared


ACTIVITY_SIGNAL_ENDPOINT = "/api/activity_signal"

# TestClient's default base URL is ``http://testserver``; the unified
# guard's allowed-origin set always includes ``request.base_url``, so
# this Origin passes ``_get_allowed_local_origins`` automatically.
_AUTH_HEADERS = {
    "Origin": "http://testserver",
    "X-CSRF-Token": "test-csrf-token",
}


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


@pytest.fixture(autouse=True)
def _fixed_csrf_token(monkeypatch):
    """Pin AUTOSTART_CSRF_TOKEN so ``_AUTH_HEADERS`` is the matching token.

    The real config uses a per-instance random token; tests need a fixed
    value to predictably authenticate happy-path requests. Same trick
    ``test_system_screenshot_router.py`` uses.
    """
    monkeypatch.setattr(
        system_router_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf-token",
    )
    yield


def _build_client(monkeypatch, mgr_map: dict, *, authenticated: bool = True):
    """TestClient with ``get_session_manager`` monkey-patched.

    ``mgr_map`` is the same dict shape returned in production
    (lanlan_name → mgr-like object). Pass an empty dict to simulate
    "no characters registered".

    ``authenticated`` (default True): inject the matching Origin +
    X-CSRF-Token on every request so happy-path tests don't have to
    repeat them. Set to False when exercising the guard itself.
    """
    monkeypatch.setattr(
        system_router_module, "get_session_manager", lambda: mgr_map,
    )
    app = FastAPI()
    app.include_router(system_router_module.router)
    client = TestClient(app)
    if authenticated:
        client.headers.update(_AUTH_HEADERS)
    return client


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


# ── Unified CSRF + Origin guard (issue #1479 Step 2) ─────────────────
# These tests exercise ``_validate_local_mutation_request`` as plumbed
# into the activity_signal endpoint. They INTENTIONALLY use
# ``authenticated=False`` so the guard's negative paths are observable.


@pytest.mark.unit
def test_no_origin_no_csrf_blocked_with_403(monkeypatch):
    """No Origin AND no CSRF token → 403 ``csrf_validation_failed``.

    Differs from PR #1477's interim Origin-only gate, which let
    no-Origin callers (curl / Electron main-process / native scripts)
    push freely. The unified guard rejects those too — *CSRF is not
    authentication*, but pushing activity signals from outside the
    same browsing context isn't a supported deployment shape (see the
    threat model in ``docs/design/security/local-mutation-auth.md``).
    Same shape as every other browser-facing mutation endpoint now.

    Body contract (CodeRabbit Minor on PR #1532): the 403 must carry
    both ``ok: false`` + ``error_code`` (unified guard shape) AND
    ``success: false`` (this endpoint's historical shape). Cache must
    be ``no-store`` to match the rest of activity_signal's responses,
    or a transient bootstrap-window 403 could get cached and mask a
    later post-bootstrap success.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
    )

    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("error_code") == "csrf_validation_failed"
    assert body.get("success") is False, (
        "activity_signal 403 must keep success:false for backward "
        f"compatibility with the rest of this endpoint's contract; got {body!r}"
    )
    assert "no-store" in resp.headers.get("Cache-Control", "").lower()
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_no_origin_with_valid_csrf_still_blocked(monkeypatch):
    """Valid CSRF token alone (no Origin) must not bypass the guard.

    Distinguishes the *Origin AND CSRF* contract from a hypothetical
    "token-only" relaxation: a non-browser caller that somehow obtained
    the CSRF token (e.g., by reading the user's running browser's
    DevTools) still can't push activity signals if it skips the Origin
    header. Without this canary a future refactor could accidentally
    weaken the guard to "token alone is enough" and the rest of the
    suite would still pass (CodeRabbit Nitpick on PR #1532).
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("error_code") == "csrf_validation_failed"
    assert body.get("success") is False
    assert "no-store" in resp.headers.get("Cache-Control", "").lower()
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_same_origin_with_valid_csrf_accepted(monkeypatch):
    """Matching Origin + valid CSRF token → push goes through.

    The TestClient's default base_url is ``http://testserver``, which
    ``_get_allowed_local_origins`` always adds to the allowed set. With
    the matching ``X-CSRF-Token``, the guard returns None and the
    handler runs normally.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "test-csrf-token",
        },
    )

    assert resp.status_code == 200, resp.text
    tracker.push_external_system_signal.assert_called_once()


@pytest.mark.unit
def test_same_origin_without_csrf_blocked(monkeypatch):
    """Matching Origin but missing CSRF token → 403.

    This is the threat the unified guard exists to cover (and PR
    #1477's Origin-only gate did *not* cover): a malicious same-origin
    context (e.g., a compromised browser extension injecting fetch
    into the page) that has the right Origin but can't read the
    per-instance CSRF token.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Origin": "http://testserver"},
    )

    assert resp.status_code == 403, resp.text
    assert resp.json().get("error_code") == "csrf_validation_failed"
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_same_origin_with_wrong_csrf_blocked(monkeypatch):
    """Matching Origin but WRONG CSRF token → 403 (constant-time compare)."""
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "wrong-token-not-the-real-one",
        },
    )

    assert resp.status_code == 403, resp.text
    assert resp.json().get("error_code") == "csrf_validation_failed"
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("evil_origin", [
    "https://evil.com",
    "http://attacker.example.com:8080",
    "https://localhost.evil.com",
])
def test_cross_site_origin_blocked_with_403(monkeypatch, evil_origin):
    """Drive-by browser fetch from off-origin page → 403.

    The CSRF token wouldn't help an attacker because they can't read it
    cross-origin, but Origin alone is enough to reject — make sure the
    guard fires before any business code runs.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={
            "Origin": evil_origin,
            # Even if the attacker guesses the token they're still
            # off-origin → reject.
            "X-CSRF-Token": "test-csrf-token",
        },
    )

    assert resp.status_code == 403, resp.text
    assert resp.json().get("error_code") == "csrf_validation_failed"
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("header_name,opaque_value", [
    ("Origin", "null"),
    ("Origin", "NULL"),
    ("Referer", "null"),
])
def test_opaque_origin_null_rejected(monkeypatch, header_name, opaque_value):
    """``Origin: null`` / ``Referer: null`` from opaque-origin contexts → 403.

    Browsers emit literal ``"null"`` as Origin for sandboxed iframes,
    ``file://`` pages, and certain extension contexts. The unified
    guard handles this naturally: ``_normalize_origin_value("null")``
    returns ``""`` (urlsplit yields empty scheme), so the membership
    check fails. With no CSRF token in the request, the guard rejects
    before anything else runs. The combined ``has_valid_csrf AND
    has_valid_origin`` rule means even a malicious sandbox carrying
    *some* string in X-CSRF-Token can't bypass it.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 5},
        headers={header_name: opaque_value},
    )

    assert resp.status_code == 403, resp.text
    assert resp.json().get("error_code") == "csrf_validation_failed"
    tracker.push_external_system_signal.assert_not_called()


@pytest.mark.unit
def test_unparseable_origin_blocked(monkeypatch):
    """Garbage Origin (can't parse scheme/host) → 403, not allowed.

    Differs from PR #1477's interim behaviour which would have
    fallen through to the no-Origin allow branch. Under the unified
    guard ``_normalize_origin_value`` returns ``""`` for unparseable
    input, the membership check fails, and without a valid CSRF token
    we reject. This closes a fingerprinting / probing vector where an
    attacker could send garbage Origins to detect endpoint existence.
    """
    mgr, tracker = _build_mgr()
    client = _build_client(monkeypatch, {"Aria": mgr}, authenticated=False)

    resp = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "idle_seconds": 0},
        headers={"Origin": "not a url"},
    )

    assert resp.status_code == 403, resp.text
    assert resp.json().get("error_code") == "csrf_validation_failed"
    tracker.push_external_system_signal.assert_not_called()


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
        # TestClient defaults to no extra headers; merge our auth headers in.
        headers={"Content-Type": "application/json", **_AUTH_HEADERS},
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


@pytest.mark.unit
def test_activity_tracker_adapts_exact_game_at_widget_mode_boundary(monkeypatch):
    from main_logic import widget_mode_runtime

    mgr, tracker = _build_mgr()
    tracker.get_snapshot_sync.return_value = SimpleNamespace(
        state="gaming",
        os_signals_available=True,
        active_window=SimpleNamespace(category="gaming", subcategory="game"),
    )
    ingest = AsyncMock()
    monkeypatch.setattr(widget_mode_runtime.widget_mode_coordinator, "ingest_activity_signal", ingest)
    client = _build_client(monkeypatch, {"Aria": mgr})

    response = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "process_name": "KnownGame.exe"},
    )

    assert response.status_code == 200
    kwargs = ingest.await_args.kwargs
    assert kwargs["active"] is True
    assert kwargs["available"] is True
    assert set(kwargs) == {"active", "available", "observed_at"}


@pytest.mark.unit
def test_gpu_fallback_gaming_state_is_not_an_exact_game_signal(monkeypatch):
    from main_logic import widget_mode_runtime

    mgr, tracker = _build_mgr()
    tracker.get_snapshot_sync.return_value = SimpleNamespace(
        state="gaming",
        os_signals_available=True,
        active_window=SimpleNamespace(category="unknown", subcategory=None),
    )
    ingest = AsyncMock()
    monkeypatch.setattr(widget_mode_runtime.widget_mode_coordinator, "ingest_activity_signal", ingest)
    client = _build_client(monkeypatch, {"Aria": mgr})

    response = client.post(
        ACTIVITY_SIGNAL_ENDPOINT,
        json={"lanlan_name": "Aria", "gpu_utilization": 99.0},
    )

    assert response.status_code == 200
    assert ingest.await_args.kwargs["active"] is False
    assert ingest.await_args.kwargs["available"] is True
