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
"""Unit tests for telemetry daily_stats validation (F0150) and +08:00 cohort alignment (F0151/F0152)."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_SRV_DIR = Path(__file__).resolve().parents[2] / "local_server" / "telemetry_server"
if str(_SRV_DIR) not in sys.path:
    sys.path.insert(0, str(_SRV_DIR))

from models import (  # noqa: E402
    DAILY_STATS_FUTURE_DAYS,
    MAX_DAILY_BUCKETS,
    MAX_DAILY_COUNTER,
    MAX_DAILY_STATS_DAYS,
    TelemetryEvent,
    model_from_json,
)
from storage import TelemetryStorage  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path):
    return TelemetryStorage(tmp_path / "telemetry_test.db")


def _base_event_dict(daily_stats: dict) -> dict:
    return {
        "device_id": "device1234567890abcd",
        "app_version": "1.0.0",
        "daily_stats": daily_stats,
    }


def _valid_bucket(**overrides) -> dict:
    bucket = {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "cached_tokens": 0,
        "call_count": 1,
    }
    bucket.update(overrides)
    return bucket


def _valid_day(**overrides) -> dict:
    day = {
        "total_prompt_tokens": 10,
        "total_completion_tokens": 20,
        "total_tokens": 30,
        "cached_tokens": 0,
        "call_count": 1,
        "error_count": 0,
        "by_model": {"qwen-max": _valid_bucket()},
        "by_call_type": {"chat": _valid_bucket()},
    }
    day.update(overrides)
    return day


@pytest.mark.parametrize(
    "bad_date",
    [
        "9999-12-31",
        "2099-01-01",
        "k0",
        "2026-W47-7",
        # Resolved inside the test body against a frozen "today": a date
        # computed here is fixed at collection time, so a run that crosses
        # midnight would see it fall back inside the validator's window.
        "future_beyond_window",
    ],
)
def test_model_rejects_invalid_or_out_of_window_stat_date(bad_date, monkeypatch):
    """Pydantic model must reject malformed, ISO-week, or future out-of-window stat_date keys."""
    frozen_today = date(2026, 6, 15)

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return frozen_today

    # Pin the validator's clock to the same day the input is derived from.
    monkeypatch.setattr(sys.modules[TelemetryEvent.__module__], "date", _FixedDate)
    if bad_date == "future_beyond_window":
        bad_date = (frozen_today + timedelta(days=DAILY_STATS_FUTURE_DAYS + 1)).isoformat()
    payload = _base_event_dict({bad_date: _valid_day()})
    with pytest.raises(Exception):
        model_from_json(TelemetryEvent, json.dumps(payload))


def test_model_drops_expired_past_dates_while_keeping_in_window_days():
    """Expired (>90d) stat_date entries are silently stripped so pending batches do not wedge clients."""
    expired_91 = (date.today() - timedelta(days=91)).isoformat()
    today = date.today().isoformat()
    payload = _base_event_dict({
        "1970-01-01": _valid_day(),
        expired_91: _valid_day(),
        today: _valid_day(),
    })
    ev = model_from_json(TelemetryEvent, json.dumps(payload))
    assert set(ev.daily_stats.keys()) == {today}


def test_model_accepts_boundary_stat_dates():
    """Stat dates at -90d and +2d boundaries are accepted."""
    d_past = (date.today() - timedelta(days=90)).isoformat()
    d_future = (date.today() + timedelta(days=2)).isoformat()
    payload = _base_event_dict({d_past: _valid_day(), d_future: _valid_day()})
    ev = model_from_json(TelemetryEvent, json.dumps(payload))
    assert set(ev.daily_stats.keys()) == {d_past, d_future}


@pytest.mark.parametrize(
    "bad_key",
    ["_total", "", "   ", " " * 129, "bad\x00key", "bad\nkey", "bad\x7fkey", "a" * 129 + "\n"],
)
def test_model_rejects_invalid_bucket_keys(bad_key):
    """Reserved _total, empty, whitespace-only, and control-character keys (including overlong ones) must fail validation."""
    today = date.today().isoformat()
    for field in ("by_model", "by_call_type"):
        day = _valid_day(**{field: {bad_key: _valid_bucket()}})
        with pytest.raises(Exception):
            model_from_json(TelemetryEvent, json.dumps(_base_event_dict({today: day})))


def test_model_drops_overlong_bucket_keys_while_keeping_valid_buckets_and_totals():
    """Overlong (>128 chars) printable model/call_type bucket keys must be dropped instead of failing the batch."""
    today = date.today().isoformat()
    max_model = "m" * 128
    max_call_type = "c" * 128
    overlong_model = "a" * 129
    overlong_call_type = "b" * 129
    ev = model_from_json(
        TelemetryEvent,
        json.dumps(
            _base_event_dict({
                today: _valid_day(
                    total_tokens=99,
                    call_count=5,
                    by_model={
                        max_model: _valid_bucket(total_tokens=20),
                        overlong_model: _valid_bucket(total_tokens=79),
                    },
                    by_call_type={
                        max_call_type: _valid_bucket(total_tokens=30),
                        overlong_call_type: _valid_bucket(total_tokens=69),
                    },
                ),
            })
        ),
    )
    day_stats = ev.daily_stats[today]
    assert day_stats.total_tokens == 99
    assert day_stats.call_count == 5
    assert set(day_stats.by_model.keys()) == {max_model}
    assert set(day_stats.by_call_type.keys()) == {max_call_type}


@pytest.mark.parametrize(
    "bad_val",
    [-1, True, False, MAX_DAILY_COUNTER + 1, 2**63],
)
def test_model_rejects_negative_bool_and_overflow_counters(bad_val):
    """Negative, boolean, and above-cap counters must fail validation."""
    today = date.today().isoformat()
    day_bad = _valid_day(total_tokens=bad_val)
    with pytest.raises(Exception):
        model_from_json(TelemetryEvent, json.dumps(_base_event_dict({today: day_bad})))

    bucket_bad = _valid_day(by_model={"qwen": _valid_bucket(call_count=bad_val)})
    with pytest.raises(Exception):
        model_from_json(TelemetryEvent, json.dumps(_base_event_dict({today: bucket_bad})))


def test_model_enforces_cardinality_caps(monkeypatch):
    """Expired days are stripped before enforcing daily_stats cap, and excess buckets trim to MAX_DAILY_BUCKETS."""
    import models as telemetry_models

    today_date = date.today()
    offline_101d = {
        (today_date - timedelta(days=i)).isoformat(): _valid_day()
        for i in range(MAX_DAILY_STATS_DAYS + 1)
    }
    assert len(offline_101d) == MAX_DAILY_STATS_DAYS + 1
    ev = model_from_json(TelemetryEvent, json.dumps(_base_event_dict(offline_101d)))
    assert today_date.isoformat() in ev.daily_stats
    assert (today_date - timedelta(days=91)).isoformat() not in ev.daily_stats
    assert (today_date - timedelta(days=100)).isoformat() not in ev.daily_stats
    assert len(ev.daily_stats) == 91

    monkeypatch.setattr(telemetry_models, "MAX_DAILY_STATS_DAYS", 5)
    too_many_in_window = {
        (today_date - timedelta(days=i)).isoformat(): _valid_day()
        for i in range(6)
    }
    with pytest.raises(Exception, match="too many daily_stats days"):
        model_from_json(
            TelemetryEvent,
            json.dumps(_base_event_dict(too_many_in_window)),
        )

    today = today_date.isoformat()
    expected_total = MAX_DAILY_BUCKETS + 1
    too_many_models = {f"m{i}": _valid_bucket(total_tokens=1) for i in range(expected_total)}
    ev_models = model_from_json(
        TelemetryEvent,
        json.dumps(
            _base_event_dict({
                today: _valid_day(
                    total_tokens=expected_total,
                    call_count=expected_total,
                    by_model=too_many_models,
                )
            })
        ),
    )
    day_models = ev_models.daily_stats[today]
    assert day_models.total_tokens == expected_total
    assert day_models.call_count == expected_total
    assert len(day_models.by_model) == MAX_DAILY_BUCKETS
    assert "m0" in day_models.by_model
    assert f"m{MAX_DAILY_BUCKETS}" not in day_models.by_model

    too_many_call_types = {f"c{i}": _valid_bucket(total_tokens=1) for i in range(expected_total)}
    ev_call_types = model_from_json(
        TelemetryEvent,
        json.dumps(
            _base_event_dict({
                today: _valid_day(
                    total_tokens=expected_total,
                    call_count=expected_total,
                    by_call_type=too_many_call_types,
                )
            })
        ),
    )
    day_call_types = ev_call_types.daily_stats[today]
    assert day_call_types.total_tokens == expected_total
    assert day_call_types.call_count == expected_total
    assert len(day_call_types.by_call_type) == MAX_DAILY_BUCKETS
    assert "c0" in day_call_types.by_call_type
    assert f"c{MAX_DAILY_BUCKETS}" not in day_call_types.by_call_type

    invalid_after_cap = {f"m{i}": _valid_bucket() for i in range(MAX_DAILY_BUCKETS)}
    invalid_after_cap["_total"] = _valid_bucket()
    with pytest.raises(Exception, match="invalid bucket key"):
        model_from_json(
            TelemetryEvent,
            json.dumps(_base_event_dict({today: _valid_day(by_model=invalid_after_cap)})),
        )


def test_storage_store_event_caps_cardinality(store):
    """Direct store_event calls must cap daily_stats days and per-day buckets."""
    today = date.today()
    invalid_prefix = {
        (today - timedelta(days=200 + i)).isoformat(): _valid_day()
        for i in range(8)
    }
    valid_window = {
        (today - timedelta(days=90 - i)).isoformat(): _valid_day()
        for i in range(93)
    }
    daily_stats = {**invalid_prefix, **valid_window}
    assert len(daily_stats) == MAX_DAILY_STATS_DAYS + 1

    today_str = today.isoformat()
    daily_stats[today_str] = _valid_day(
        by_model={f"m{i:03d}": _valid_bucket(total_tokens=1) for i in range(MAX_DAILY_BUCKETS + 5)},
        by_call_type={f"c{i:03d}": _valid_bucket(total_tokens=1) for i in range(MAX_DAILY_BUCKETS + 5)},
    )
    store.store_event(
        device_id="device_cap_1234567890",
        app_version="1.0",
        payload_json="{}",
        daily_stats=daily_stats,
    )
    conn = store._get_conn()
    truncated_day = (today + timedelta(days=2)).isoformat()
    stored_days = {
        r["stat_date"]
        for r in conn.execute("SELECT DISTINCT stat_date FROM daily_aggregates").fetchall()
    }
    assert truncated_day not in stored_days
    assert len(stored_days) == 92

    model_count = conn.execute(
        "SELECT COUNT(*) FROM daily_aggregates WHERE stat_date = ? AND model != '_total' AND call_type = '_total'",
        (today_str,),
    ).fetchone()[0]
    call_type_count = conn.execute(
        "SELECT COUNT(*) FROM daily_aggregates WHERE stat_date = ? AND model = '_total' AND call_type != '_total'",
        (today_str,),
    ).fetchone()[0]
    assert model_count == MAX_DAILY_BUCKETS
    assert call_type_count == MAX_DAILY_BUCKETS


def test_http_submit_returns_400_for_forged_daily_stats(tmp_path, monkeypatch):
    """Forged daily_stats sent to /api/v1/telemetry must return HTTP 400."""
    from fastapi.testclient import TestClient
    import server as telemetry_server

    monkeypatch.setattr(telemetry_server, "storage", TelemetryStorage(tmp_path / "http.db"))
    client = TestClient(telemetry_server.app)

    payload = _base_event_dict({"9999-12-31": _valid_day()})
    ts = time.time()
    payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sig = hmac.new(
        telemetry_server.HMAC_SECRET.encode("utf-8"),
        f"{int(ts)}:{payload_str}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    resp = client.post(
        "/api/v1/telemetry",
        json={"timestamp": ts, "signature": sig, "payload": payload},
    )
    assert resp.status_code == 400


def test_storage_store_event_defense_in_depth(store):
    """Direct store_event calls must drop invalid dates, _total buckets, negative/bool/int64 counts, and cap cardinality."""
    today = date.today().isoformat()
    bad_daily = {
        "9999-12-31": _valid_day(total_tokens=999),
        "2026-W47-7": _valid_day(total_tokens=999),
        "1970-01-01": _valid_day(total_tokens=999),
        today: {
            "total_prompt_tokens": 5,
            "total_completion_tokens": 5,
            "total_tokens": 10,
            "cached_tokens": 0,
            "call_count": 1,
            "error_count": 0,
            "by_model": {
                "_total": _valid_bucket(total_tokens=500),
                "neg": _valid_bucket(total_tokens=-5),
                "huge": _valid_bucket(total_tokens=2**63),
                "flag": _valid_bucket(call_count=True),
                "good-model": _valid_bucket(total_tokens=10),
            },
            "by_call_type": {
                "_total": _valid_bucket(total_tokens=500),
                "chat": _valid_bucket(total_tokens=10),
            },
        },
    }
    store.store_event(
        device_id="device1234567890abcd",
        app_version="1.0",
        payload_json="{}",
        daily_stats=bad_daily,
    )
    rows = store._get_conn().execute(
        "SELECT stat_date, model, call_type, total_tokens FROM daily_aggregates ORDER BY stat_date, model, call_type"
    ).fetchall()
    got = [(r["stat_date"], r["model"], r["call_type"], r["total_tokens"]) for r in rows]
    assert got == [
        (today, "_total", "_total", 10),
        (today, "_total", "chat", 10),
        (today, "good-model", "_total", 10),
    ]


def test_first_seen_00_to_08_cst_cohort_alignment(store):
    """Devices with +08:00 first_seen in 00:00-07:59 must align to the +08:00 calendar day in metrics."""
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    day_before = (today - timedelta(days=2)).isoformat()
    d7_anchor = (today - timedelta(days=8)).isoformat()

    # Insert active aggregates on yesterday for D1 and D7 retention checks.
    devices = [
        ("dev_d1_0030", f"{day_before}T00:30:00.000+08:00", yesterday, "76561198000000011"),
        ("dev_d1_0759", f"{day_before}T07:59:59.000+08:00", None, "76561198000000012"),
        ("dev_d7_0030", f"{d7_anchor}T00:30:00.000+08:00", yesterday, "76561198000000013"),
        ("dev_today_0100", f"{today.isoformat()}T01:00:00.000+08:00", today.isoformat(), "76561198000000014"),
    ]
    for dev_id, fs_iso, active_day, steam in devices:
        daily = {active_day: {"call_count": 1, "total_tokens": 1}} if active_day else {}
        store.store_event(
            device_id=dev_id,
            app_version="1.0",
            payload_json=json.dumps({"device_id": dev_id, "steam_user_id": steam}),
            daily_stats=daily,
            steam_user_id=steam,
        )
        with store._transaction() as conn:
            conn.execute("UPDATE devices SET first_seen = ? WHERE device_id = ?", (fs_iso, dev_id))

    store.build_edges_from_events()
    store.recompute_canonical()

    user_m = store.get_user_metrics(days=30)
    assert user_m["new_device_trend"].get(today.isoformat()) == 1
    assert user_m["new_device_trend"].get(day_before) == 2
    assert user_m["new_device_trend"].get(d7_anchor) == 1
    assert user_m["d1_retention"] == 50.0
    assert user_m["d7_retention"] == 100.0

    canon_m = store.get_canonical_metrics(days=30)
    assert canon_m["canonical_d1_retention"] == 50.0
    assert canon_m["canonical_d7_retention"] == 100.0


def test_http_submit_drops_expired_days_and_acknowledges_batch(tmp_path, monkeypatch):
    """HTTP /api/v1/telemetry must return 200 on >90d expired days, store only in-window days, and dedupe batch_id."""
    from fastapi.testclient import TestClient
    from security import compute_signature
    import server as telemetry_server

    test_store = TelemetryStorage(tmp_path / "http_expired.db")
    monkeypatch.setattr(telemetry_server, "storage", test_store)
    monkeypatch.setattr(telemetry_server.rate_limiter, "is_allowed", lambda _dev: True)
    client = TestClient(telemetry_server.app)

    today = date.today().isoformat()
    expired_91 = (date.today() - timedelta(days=91)).isoformat()

    def _signed_post(payload: dict, batch_id: str):
        ts = time.time()
        payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        sig = compute_signature(payload_str, ts, telemetry_server.HMAC_SECRET)
        return client.post(
            "/api/v1/telemetry",
            json={"timestamp": ts, "signature": sig, "batch_id": batch_id, "payload": payload},
        )

    mixed_payload = _base_event_dict({
        expired_91: _valid_day(total_tokens=999),
        today: _valid_day(
            total_tokens=42,
            by_model={"qwen-max": _valid_bucket(total_tokens=42)},
            by_call_type={"chat": _valid_bucket(total_tokens=42)},
        ),
    })
    resp_mixed = _signed_post(mixed_payload, batch_id="batch-mixed-1")
    assert resp_mixed.status_code == 200
    assert resp_mixed.json()["ok"] is True

    rows = test_store._get_conn().execute(
        "SELECT stat_date, model, call_type, total_tokens FROM daily_aggregates ORDER BY stat_date, model, call_type"
    ).fetchall()
    assert [(r["stat_date"], r["model"], r["call_type"], r["total_tokens"]) for r in rows] == [
        (today, "_total", "_total", 42),
        (today, "_total", "chat", 42),
        (today, "qwen-max", "_total", 42),
    ]

    expired_only = _base_event_dict({expired_91: _valid_day(total_tokens=123)})
    resp_old = _signed_post(expired_only, batch_id="batch-expired-only-1")
    assert resp_old.status_code == 200
    assert resp_old.json()["ok"] is True
    assert test_store.is_duplicate_batch("batch-expired-only-1") is True

    resp_dup = _signed_post(expired_only, batch_id="batch-expired-only-1")
    assert resp_dup.status_code == 200
    assert resp_dup.json()["message"] == "duplicate, skipped"


def test_http_submit_accepts_over_100_day_offline_batch_after_stripping_expired_days(tmp_path, monkeypatch):
    """Offline windows >100 days containing >90d expired entries must return 200 after stripping expired days."""
    from fastapi.testclient import TestClient
    from security import compute_signature
    import server as telemetry_server

    test_store = TelemetryStorage(tmp_path / "http_offline_101d.db")
    monkeypatch.setattr(telemetry_server, "storage", test_store)
    monkeypatch.setattr(telemetry_server.rate_limiter, "is_allowed", lambda _dev: True)
    client = TestClient(telemetry_server.app)

    today_date = date.today()
    today = today_date.isoformat()
    expired_91 = (today_date - timedelta(days=91)).isoformat()
    expired_100 = (today_date - timedelta(days=100)).isoformat()
    offline_101d = {
        (today_date - timedelta(days=i)).isoformat(): _valid_day(
            total_tokens=1 if i > 0 else 42,
            by_model={"qwen-max": _valid_bucket(total_tokens=1 if i > 0 else 42)},
            by_call_type={"chat": _valid_bucket(total_tokens=1 if i > 0 else 42)},
        )
        for i in range(MAX_DAILY_STATS_DAYS + 1)
    }
    assert len(offline_101d) == MAX_DAILY_STATS_DAYS + 1

    payload = _base_event_dict(offline_101d)
    ts = time.time()
    payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sig = compute_signature(payload_str, ts, telemetry_server.HMAC_SECRET)
    resp = client.post(
        "/api/v1/telemetry",
        json={"timestamp": ts, "signature": sig, "batch_id": "batch-offline-101d", "payload": payload},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert test_store.is_duplicate_batch("batch-offline-101d") is True

    stored_days = {
        r["stat_date"]
        for r in test_store._get_conn().execute("SELECT DISTINCT stat_date FROM daily_aggregates").fetchall()
    }
    assert len(stored_days) == 91
    assert today in stored_days
    assert expired_91 not in stored_days
    assert expired_100 not in stored_days


def test_http_submit_drops_overlong_model_bucket_and_acknowledges_batch(tmp_path, monkeypatch):
    """HTTP /api/v1/telemetry must drop >128-char model buckets, keep valid totals/buckets, and return 200."""
    from fastapi.testclient import TestClient
    from security import compute_signature
    import server as telemetry_server

    test_store = TelemetryStorage(tmp_path / "http_overlong_model.db")
    monkeypatch.setattr(telemetry_server, "storage", test_store)
    monkeypatch.setattr(telemetry_server.rate_limiter, "is_allowed", lambda _dev: True)
    client = TestClient(telemetry_server.app)

    today = date.today().isoformat()
    overlong_model = "a" * 129
    payload = _base_event_dict({
        today: _valid_day(
            total_tokens=70,
            call_count=2,
            by_model={
                overlong_model: _valid_bucket(total_tokens=50),
                "short-model": _valid_bucket(total_tokens=20),
            },
            by_call_type={"chat": _valid_bucket(total_tokens=70)},
        ),
    })
    ts = time.time()
    payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sig = compute_signature(payload_str, ts, telemetry_server.HMAC_SECRET)
    resp = client.post(
        "/api/v1/telemetry",
        json={"timestamp": ts, "signature": sig, "batch_id": "batch-overlong-model-1", "payload": payload},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert test_store.is_duplicate_batch("batch-overlong-model-1") is True

    rows = test_store._get_conn().execute(
        "SELECT stat_date, model, call_type, total_tokens FROM daily_aggregates ORDER BY stat_date, model, call_type"
    ).fetchall()
    assert [(r["stat_date"], r["model"], r["call_type"], r["total_tokens"]) for r in rows] == [
        (today, "_total", "_total", 70),
        (today, "_total", "chat", 70),
        (today, "short-model", "_total", 20),
    ]


def test_http_submit_trims_over_100_model_buckets_and_acknowledges_batch(tmp_path, monkeypatch):
    """HTTP /api/v1/telemetry must trim >MAX_DAILY_BUCKETS valid models, preserve totals, and return 200."""
    from fastapi.testclient import TestClient
    from security import compute_signature
    import server as telemetry_server

    test_store = TelemetryStorage(tmp_path / "http_over_100_models.db")
    monkeypatch.setattr(telemetry_server, "storage", test_store)
    monkeypatch.setattr(telemetry_server.rate_limiter, "is_allowed", lambda _dev: True)
    client = TestClient(telemetry_server.app)

    today = date.today().isoformat()
    expected_total = MAX_DAILY_BUCKETS + 1
    by_model = {f"m{i:03d}": _valid_bucket(total_tokens=1) for i in range(expected_total)}
    payload = _base_event_dict({
        today: _valid_day(
            total_tokens=expected_total,
            call_count=expected_total,
            by_model=by_model,
            by_call_type={"chat": _valid_bucket(total_tokens=expected_total)},
        ),
    })
    ts = time.time()
    payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sig = compute_signature(payload_str, ts, telemetry_server.HMAC_SECRET)
    resp = client.post(
        "/api/v1/telemetry",
        json={"timestamp": ts, "signature": sig, "batch_id": "batch-101-models", "payload": payload},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert test_store.is_duplicate_batch("batch-101-models") is True

    rows = test_store._get_conn().execute(
        "SELECT stat_date, model, call_type, total_tokens FROM daily_aggregates WHERE stat_date = ? ORDER BY model, call_type",
        (today,),
    ).fetchall()
    total_rows = [(r["model"], r["call_type"], r["total_tokens"]) for r in rows if r["model"] == "_total"]
    model_rows = [r["model"] for r in rows if r["model"] != "_total" and r["call_type"] == "_total"]
    assert total_rows == [
        ("_total", "_total", expected_total),
        ("_total", "chat", expected_total),
    ]
    assert len(model_rows) == MAX_DAILY_BUCKETS
    assert "m000" in model_rows
    assert f"m{MAX_DAILY_BUCKETS:03d}" not in model_rows
