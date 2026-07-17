# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Activity signal ingestion endpoint (/activity_signal).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _json_no_store_response, _set_no_store_headers, _validate_local_mutation_request, logger, router
import math
import time
from fastapi import Request
from ..shared_state import get_session_manager
from main_logic.activity.tracker import _EXTERNAL_SIGNAL_MIN_INTERVAL


# ── Frontend-pushed activity signals (cross-platform OS signal channel) ──
#
# Per-lanlan throttle for ``/api/activity_signal``. Keyed by lanlan_name,
# value is the timestamp of the last accepted push. Bounded — see
# ``_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES`` — to defend against an
# attacker spraying lanlan_names; in practice the dict has 1-3 entries.
# Concurrent access from FastAPI's worker pool is safe because Python
# dict ops are atomic under the GIL and we tolerate occasional rate-limit
# slippage (worst case: an extra push slips through).
_ACTIVITY_SIGNAL_THROTTLE: dict[str, float] = {}


_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES = 64


def _activity_signal_validate_float(
    data: dict, key: str, lo: float | None, hi: float | None,
) -> tuple[float | None, str | None]:
    """Coerce ``data[key]`` to a bounded float; ``None`` means absent.

    Tracker treats absent fields as neutral defaults (see
    ``UserActivityTracker.push_external_system_signal`` docstring), so
    we keep ``None`` distinct from a present-but-invalid value — the
    latter is a 400 with a specific error.

    Non-finite values (``NaN`` / ``±Infinity``) are rejected explicitly
    before range comparison — ``float('nan') < lo`` is silently
    ``False``, so they'd otherwise bypass the bounds check. Worse,
    serialising them downstream (state-machine logs, JSON responses)
    crashes since standard JSON forbids them. ``math.isfinite`` is the
    correct guard: it rejects NaN and both infinities while accepting
    every normal/subnormal float.
    """
    raw = data.get(key)
    if raw is None:
        return None, None
    # Reject booleans before float coercion (Codex F8 on PR #1477).
    # ``bool`` is a subclass of ``int`` in Python, so ``float(True)``
    # silently returns ``1.0`` and ``float(False)`` returns ``0.0``,
    # which would slip past the range checks below as legitimate signal
    # values. ``isinstance(raw, bool)`` catches both before the int
    # / float fast paths in ``float()``.
    if isinstance(raw, bool):
        return None, f"{key} must be a number"
    try:
        val = float(raw)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is raised by ``float()`` when the integer is
        # too large to fit in a C double (Codex F9 on PR #1477) —
        # JSON allows arbitrary-precision ints which Python loads as
        # native big ints, and ``float(10**400)`` blows up. Without
        # this case the request becomes a 500 instead of a clean 400,
        # giving a low-cost crash vector to anyone POSTing oversized
        # numeric literals.
        return None, f"{key} must be a number"
    if not math.isfinite(val):
        return None, f"{key} must be finite"
    if lo is not None and val < lo:
        return None, f"{key} must be >= {lo}"
    if hi is not None and val > hi:
        return None, f"{key} must be <= {hi}"
    return val, None


def _activity_signal_validate_str(
    data: dict, key: str,
) -> tuple[str | None, str | None]:
    raw = data.get(key)
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f"{key} must be a string"
    return raw, None


@router.post('/activity_signal')
async def push_activity_signal(request: Request):
    """Accept OS-activity signals pushed by the frontend on a heartbeat.

    Companion to ``UserActivityTracker.push_external_system_signal()``
    (PR #1015 ``main_logic/activity/tracker.py:347``), exposing the
    push channel as HTTP for the "backend doesn't run on the user's
    machine" deployments. The frontend (Electron preload reading
    ``powerMonitor.getSystemIdleTime`` + npm ``active-win`` +
    ``os.cpus()`` + ``nvidia-smi``) POSTs here every ``~6s``; the
    tracker treats anything fresher than ``_EXTERNAL_SIGNAL_TTL_SECONDS``
    (20s) as the authoritative OS view, falling back to the local
    collector when the heartbeat stops. Same fresh-then-fallback path
    feeds both the async ``get_snapshot`` and the sync variant — see
    ``tracker._select_system_snapshot``.

    Auth:

    * Unified ``_validate_local_mutation_request`` guard (issue #1479
      Step 2): same Origin + ``X-CSRF-Token`` contract every other
      browser-facing mutation endpoint uses (tutorial-prompt,
      screenshot, autostart-prompt, …). Replaces PR #1477's interim
      Origin-only gate. Same-origin Electron renderers and browser
      tabs send ``X-CSRF-Token`` via
      ``window.nekoLocalMutationSecurity`` (token is exposed by
      ``GET /api/config/page_config``); curl / Electron main-process /
      native scripts that don't run the token bootstrap are now
      rejected because *CSRF ≠ authentication* — pushing activity
      from outside the same browsing context isn't a supported path
      (see ``docs/design/security/local-mutation-auth.md`` for the
      threat model). The guard already rejects ``Origin: null`` /
      opaque origins because ``_normalize_origin_value`` returns
      ``""`` for them, which then fails the membership check.
    * Per-lanlan 5s rate limit below + the tracker's per-character
      lookup raise spam cost and bound the impact even if the guard
      somehow passes.

    Body fields (all optional except ``lanlan_name``):
      * ``lanlan_name`` (required) — which character's tracker to update
      * ``window_title`` — string, raw active-window title
      * ``process_name`` — string, owning process exe name (e.g. ``"Code.exe"``)
      * ``idle_seconds`` — float ≥ 0, OS-wide keyboard/mouse idle
      * ``cpu_avg_30s`` — float in ``[0, 100]``, rolling CPU average
      * ``gpu_utilization`` — float in ``[0, 100]``, primary GPU utilisation

    Returns 200 on success, 400 on malformed payload, 403 on
    Origin/CSRF rejection, 404 if ``lanlan_name`` isn't registered,
    429 if pushed faster than ``_EXTERNAL_SIGNAL_MIN_INTERVAL`` (5s),
    503 if the character's tracker hasn't initialised yet.
    """
    # ``error_defaults`` so the 403 body includes ``success: false``
    # alongside the unified guard's ``ok/error_code`` fields — keeps
    # the contract consistent with this endpoint's other error
    # branches (existing frontend / tests grep ``success``). Also
    # apply ``_set_no_store_headers`` since the rest of this handler's
    # responses use that and a cached 403 would mask post-bootstrap
    # success on the next tick (CodeRabbit Minor on PR #1532).
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    try:
        data = await request.json()
    except Exception:
        return _json_no_store_response(
            {"success": False, "error": "invalid JSON body"},
            status_code=400,
        )
    if not isinstance(data, dict):
        return _json_no_store_response(
            {"success": False, "error": "body must be a JSON object"},
            status_code=400,
        )

    lanlan_name = data.get("lanlan_name")
    if not isinstance(lanlan_name, str) or not lanlan_name.strip():
        return _json_no_store_response(
            {"success": False, "error": "lanlan_name required"},
            status_code=400,
        )
    lanlan_name = lanlan_name.strip()

    idle_seconds, err = _activity_signal_validate_float(
        data, "idle_seconds", 0.0, None,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    cpu_avg_30s, err = _activity_signal_validate_float(
        data, "cpu_avg_30s", 0.0, 100.0,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    gpu_utilization, err = _activity_signal_validate_float(
        data, "gpu_utilization", 0.0, 100.0,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )

    window_title, err = _activity_signal_validate_str(data, "window_title")
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    process_name, err = _activity_signal_validate_str(data, "process_name")
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )

    # ── Empty-signal guard (Codex F6 + CodeRabbit F7 on PR #1477) ──
    # If every signal field is absent, the tracker's
    # ``push_external_system_signal`` would still mark
    # ``os_signals_available=True`` and default missing numerics to
    # ``0.0`` — i.e., a payload of ``{"lanlan_name": "X"}`` would
    # silently overwrite real state with synthetic "idle=0 / cpu=0 /
    # no window". The frontend client already skips empty bridge
    # snapshots, but a malicious or buggy native caller could still
    # POST an empty payload, so we reject server-side too.
    #
    # Blank-string handling (CodeRabbit F7): ``"window_title": ""`` or
    # whitespace-only strings carry no information and have the same
    # poisoning effect as ``None``. Treat them as absent for the
    # all-empty check; non-blank strings (legit "no foreground window
    # right now" semantics with explicit ``""``) would still trip the
    # check if every other field is also None — which is the right
    # outcome, that payload tells the tracker literally nothing.
    if all(
        v is None or (isinstance(v, str) and not v.strip())
        for v in (
            idle_seconds, cpu_avg_30s, gpu_utilization,
            window_title, process_name,
        )
    ):
        return _json_no_store_response(
            {
                "success": False,
                "error": "at least one signal field required",
            },
            status_code=400,
        )

    # Per-lanlan throttle — kept below the frontend's 6s heartbeat. The
    # 20s TTL keeps the last accepted snapshot fresh through two missed
    # heartbeats (next attempt at ~18s) plus normal scheduling jitter.
    # Spam control, not auth — the character lookup below is the real
    # integrity check.
    now = time.time()
    last_push = _ACTIVITY_SIGNAL_THROTTLE.get(lanlan_name)
    if last_push is not None and (now - last_push) < _EXTERNAL_SIGNAL_MIN_INTERVAL:
        retry_after = max(
            0.0, _EXTERNAL_SIGNAL_MIN_INTERVAL - (now - last_push),
        )
        resp = _json_no_store_response(
            {
                "success": False,
                "error": "rate limited",
                "retry_after_seconds": round(retry_after, 3),
            },
            status_code=429,
        )
        # Header is integer seconds per RFC 9110; round up so clients
        # don't retry into the same window.
        resp.headers["Retry-After"] = str(int(retry_after) + 1)
        return resp

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if not mgr:
        return _json_no_store_response(
            {
                "success": False,
                "error": f"lanlan_name {lanlan_name!r} not registered",
            },
            status_code=404,
        )
    tracker = getattr(mgr, "_activity_tracker", None)
    if tracker is None:
        return _json_no_store_response(
            {
                "success": False,
                "error": "activity tracker not initialised for this character",
            },
            status_code=503,
        )

    try:
        tracker.push_external_system_signal(
            window_title=window_title,
            process_name=process_name,
            idle_seconds=idle_seconds,
            cpu_avg_30s=cpu_avg_30s,
            gpu_utilization=gpu_utilization,
            now=now,
        )
    except Exception as e:
        logger.exception(
            "push_external_system_signal failed for %s", lanlan_name,
        )
        return _json_no_store_response(
            {"success": False, "error": f"tracker rejected push: {e}"},
            status_code=500,
        )

    _ACTIVITY_SIGNAL_THROTTLE[lanlan_name] = now
    # Bound the dict: in practice lanlan_names are 1-3, but if an
    # attacker sprays unique names we trim oldest. Sorted ascending by
    # timestamp; keep the freshest MAX entries.
    if len(_ACTIVITY_SIGNAL_THROTTLE) > _ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES:
        excess = sorted(
            _ACTIVITY_SIGNAL_THROTTLE.items(), key=lambda kv: kv[1],
        )[:-_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES]
        for key, _ in excess:
            _ACTIVITY_SIGNAL_THROTTLE.pop(key, None)

    return _json_no_store_response({"success": True})
