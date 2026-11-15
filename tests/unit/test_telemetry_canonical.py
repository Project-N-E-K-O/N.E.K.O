"""Canonical-identity aggregation storage tests.

Cover device/Steam and device/device edge construction, union-find components, deterministic representatives,
denylist resurrection prevention, normalization type guards, and canonical-based deduplication.

telemetry_server uses flat imports (from storage import ...); insert its directory into
sys.path and import storage directly, using temporary database files without touching production.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SRV_DIR = Path(__file__).resolve().parents[2] / "local_server" / "telemetry_server"
sys.path.insert(0, str(_SRV_DIR))

from storage import TelemetryStorage, normalize_steam_id  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return TelemetryStorage(tmp_path / "t.db")


def _report(store, device_id, *, steam=None, legacy=None, day=None):
    """Simulate a report with steam_user_id / device_id_legacy in events.payload."""
    payload = {"device_id": device_id}
    if steam is not None:
        payload["steam_user_id"] = steam
    if legacy is not None:
        payload["device_id_legacy"] = legacy
    daily = {}
    if day:
        daily = {day: {"call_count": 1, "total_tokens": 1}}
    store.store_event(
        device_id=device_id,
        app_version="1.0",
        payload_json=json.dumps(payload),
        daily_stats=daily,
        steam_user_id=(steam or ""),
    )


def _canon_of(store, device_id):
    row = store._get_conn().execute(
        "SELECT canonical_id FROM canonical_map WHERE entity_type='device' AND entity_id=?",
        (device_id,),
    ).fetchone()
    return row["canonical_id"] if row else None


# ---------------- normalize ----------------

@pytest.mark.parametrize("raw,expected", [
    ("76561198000000001", "76561198000000001"),
    ("0076561198000000001", "76561198000000001"),  # Remove leading zeros.
    ("0", ""),            # Sentinel.
    ("00", ""),
    ("", ""),
    ("abc", ""),
    ("123abc", ""),
    ("9" * 21, ""),       # Oversized-input DoS guard.
    ("99999999999999999999", ""),  # Exceeds u64.
    (123, ""),            # Nonstring: number.
    (None, ""),           # Nonstring: null.
    (["x"], ""),          # Nonstring: list.
    ("²", ""),       # Unicode superscript '²': isdigit()=True but int() raises ValueError.
    ("１２３", ""),   # Fullwidth digits.
    ("١٢٣", ""),   # Arabic-Indic digits.
])
def test_normalize(raw, expected):
    # Invalid input must return '' without exceptions, avoiding ingest 500 responses or stuck build_edges.
    assert normalize_steam_id(raw) == expected


# -- Edge construction and union-find --

def test_two_devices_one_steam_merge(store):
    """Two devices logging into one Steam account share a canonical identity."""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == _canon_of(store, "deviceBBBBBBBBBBBB")


def test_device_legacy_alias_merge(store):
    """device_id_legacy creates device/device alias edges yielding one canonical identity."""
    _report(store, "deviceNEWNEWNEWNEW", legacy="deviceOLDOLDOLDOLD")
    # The old device reported independently too, giving it a devices-table row.
    _report(store, "deviceOLDOLDOLDOLD")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceNEWNEWNEWNEW") == _canon_of(store, "deviceOLDOLDOLDOLD")


def test_multihop_union(store):
    """A-X, B-X, B-Y: A B X Y share one canonical identity through a many-to-many component."""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")  # A-X
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")  # B-X
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000002")  # B-Y
    store.build_edges_from_events()
    store.recompute_canonical()
    ca = _canon_of(store, "deviceAAAAAAAAAAAA")
    cb = _canon_of(store, "deviceBBBBBBBBBBBB")
    assert ca == cb
    # Representative should be the minimum Steam node.
    assert ca == "s:76561198000000001"


def test_canonical_id_deterministic(store):
    """Repeated recomputation leaves canonical_id stable through deterministic representative selection."""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000009")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000009")
    store.build_edges_from_events()
    store.recompute_canonical()
    first = _canon_of(store, "deviceAAAAAAAAAAAA")
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == first


def test_device_only_is_own_canonical(store):
    """Devices never logged into Steam remain their own canonical identities, covering all metrics."""
    _report(store, "deviceLONELYXXXXXX")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceLONELYXXXXXX") == "d:deviceLONELYXXXXXX"


# -- Denylist prevents resurrection --

def test_denylist_blocks_resurrection(store):
    """Rescanning events containing deleted Steam IDs must not recreate edges."""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == _canon_of(store, "deviceBBBBBBBBBBBB")

    # Delete account.
    store.add_steam_id_to_denylist("76561198000000001")
    # Edges deleted.
    cnt = store._get_conn().execute(
        "SELECT COUNT(*) c FROM device_steam_edges WHERE steam_user_id='76561198000000001'"
    ).fetchone()["c"]
    assert cnt == 0
    # Reset cursor and rescan all events whose payload still contains the deleted ID; denylist must block it.
    store._get_conn().execute("UPDATE edge_build_cursor SET last_event_id=0")
    store._get_conn().commit()
    store.build_edges_from_events()
    cnt2 = store._get_conn().execute(
        "SELECT COUNT(*) c FROM device_steam_edges WHERE steam_user_id='76561198000000001'"
    ).fetchone()["c"]
    assert cnt2 == 0, "denylist 未挡住回填复活"
    store.recompute_canonical()
    # These devices no longer connect through this Steam account.
    assert _canon_of(store, "deviceAAAAAAAAAAAA") != _canon_of(store, "deviceBBBBBBBBBBBB")


def test_malformed_payload_does_not_crash(store):
    """Forged/abnormal numeric/null steam_user_id payloads must not crash edge construction."""
    store.store_event(
        device_id="deviceWEIRDXXXXXX",
        app_version="1.0",
        payload_json=json.dumps({"device_id": "deviceWEIRDXXXXXX", "steam_user_id": 123}),
        daily_stats={},
    )
    store.store_event(
        device_id="deviceWEIRD2XXXXX",
        app_version="1.0",
        payload_json='{"device_id": "deviceWEIRD2XXXXX", "steam_user_id": null}',
        daily_stats={},
    )
    store.store_event(
        device_id="deviceWEIRD3XXXXX",
        app_version="1.0",
        payload_json="not even json",
        daily_stats={},
    )
    # No exceptions or garbage edges.
    n = store.build_edges_from_events()
    assert n == 3
    edges = store._get_conn().execute("SELECT COUNT(*) c FROM device_steam_edges").fetchone()["c"]
    assert edges == 0


# -- Canonical-metric deduplication --

def test_canonical_metrics_dedup(store):
    """One person active on two devices the same day yields device DAU=2, canonical DAU=1."""
    today = date.today().isoformat()
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001", day=today)
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001", day=today)
    store.build_edges_from_events()
    store.recompute_canonical()

    device_m = store.get_user_metrics(days=30)
    canon_m = store.get_canonical_metrics(days=30)
    assert device_m["dau_today"] == 2
    assert canon_m["canonical_dau_today"] == 1
    assert canon_m["total_canonical"] == 1


def test_drain_loop_catches_up(store):
    """build_all_pending_edges must drain all pages, guarding against cursor lag."""
    for i in range(12):
        _report(store, f"device{i:012d}", steam=f"765611980000000{i:02d}")
    # Pages contain only 5 entries, but build_all_pending_edges must loop until all 12 are consumed.
    total = store.build_all_pending_edges(batch_limit=5)
    assert total == 12
    cursor = store._get_conn().execute(
        "SELECT last_event_id FROM edge_build_cursor WHERE id=1"
    ).fetchone()["last_event_id"]
    max_event = store._get_conn().execute("SELECT MAX(id) m FROM events").fetchone()["m"]
    assert cursor == max_event
    edges = store._get_conn().execute("SELECT COUNT(*) c FROM device_steam_edges").fetchone()["c"]
    assert edges == 12


def test_build_edges_normalizes_payload(store):
    """Normalize raw steam_user_id in events.payload before edge construction.

    '0076561198000000001' and '76561198000000001' must share a canonical identity;
    sentinel '0' creates no edges. This guards identity splitting/pollution.
    """
    _report(store, "deviceLEADINGZERO0", steam="0076561198000000001")  # Leading zeros.
    _report(store, "deviceNORMALXXXXXX", steam="76561198000000001")     # Canonical form.
    _report(store, "deviceSENTINELXXXX", steam="0")                     # Sentinel.
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceLEADINGZERO0") == _canon_of(store, "deviceNORMALXXXXXX")
    # Sentinel creates no edges, remaining its own canonical identity.
    assert _canon_of(store, "deviceSENTINELXXXX") == "d:deviceSENTINELXXXX"
    edges = store._get_conn().execute(
        "SELECT DISTINCT steam_user_id FROM device_steam_edges"
    ).fetchall()
    assert [r["steam_user_id"] for r in edges] == ["76561198000000001"]


def test_denylist_collapses_in_store_event(store):
    """Deleted Steam64 IDs must not reenter devices through later reports, preserving redaction."""
    store.add_steam_id_to_denylist("76561198000000001")
    _report(store, "deviceXXXXXXXXXXXX", steam="76561198000000001")
    val = store._get_conn().execute(
        "SELECT steam_user_id FROM devices WHERE device_id='deviceXXXXXXXXXXXX'"
    ).fetchone()["steam_user_id"]
    assert val == "", "denylisted ID 被上报写回 devices 列"


def test_denylist_atomic_guard_on_insert(store):
    """Denylisted IDs must not be inserted even if events contain them, through atomic WHERE NOT EXISTS."""
    # First denylist a Steam ID.
    store.add_steam_id_to_denylist("76561198000000001")
    # Then submit events containing it, simulating post-deletion reports or historical backfill.
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000002")
    store.build_edges_from_events()
    rows = store._get_conn().execute(
        "SELECT steam_user_id FROM device_steam_edges ORDER BY steam_user_id"
    ).fetchall()
    sids = [r["steam_user_id"] for r in rows]
    assert "76561198000000001" not in sids, "denylist ID 被插回"
    assert "76561198000000002" in sids


def test_device_legacy_length_validated(store):
    """device_id_legacy bypasses Pydantic; create device_alias_edges only for lengths 16..128."""
    # Reject lengths >128 or <16.
    _report(store, "deviceMAINXXXXXXX", legacy="x" * 200)
    _report(store, "deviceMAIN2XXXXXX", legacy="short")
    # Accept valid lengths.
    _report(store, "deviceMAIN3XXXXXX", legacy="deviceLEGACYOKXXX")
    # Boundaries: exactly 16 minimum / 128 maximum, preventing off-by-one errors.
    _report(store, "deviceMAIN4XXXXXX", legacy="a" * 16)
    _report(store, "deviceMAIN5XXXXXX", legacy="b" * 128)
    store.build_edges_from_events()
    rows = store._get_conn().execute("SELECT dev_lo, dev_hi FROM device_alias_edges").fetchall()
    pairs = {(r["dev_lo"], r["dev_hi"]) for r in rows}
    assert pairs == {
        tuple(sorted(("deviceMAIN3XXXXXX", "deviceLEGACYOKXXX"))),
        tuple(sorted(("deviceMAIN4XXXXXX", "a" * 16))),
        tuple(sorted(("deviceMAIN5XXXXXX", "b" * 128))),
    }


def test_alias_repointed_when_canonical_removed(store):
    """Deleting a canonical identity must redirect/remove its old aliases, never leave them dangling.

    Codex sequence: d1/d2 first become canonical, then both log into S and merge into s:S, generating aliases
    d:d1->s:S and d:d2->s:S. Denylisting S restores each device's own canonical identity;
    resolve_canonical('d:d1') must return d:d1, never deleted s:S.
    """
    # Stage 1: d1/d2 independently become canonical identities.
    _report(store, "deviceD1XXXXXXXXX")
    _report(store, "deviceD2XXXXXXXXX")
    store.build_edges_from_events()
    store.recompute_canonical()
    # Stage 2: both log into S -> merge into s:S, with old device canonical aliases pointing to s:S.
    _report(store, "deviceD1XXXXXXXXX", steam="76561198000000001")
    _report(store, "deviceD2XXXXXXXXX", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert store.resolve_canonical("d:deviceD1XXXXXXXXX") == "s:76561198000000001"
    # Stage 3: delete S -> split the component, restoring d1/d2 as their own canonical identities.
    store.add_steam_id_to_denylist("76561198000000001")
    store.recompute_canonical()
    assert store.resolve_canonical("d:deviceD1XXXXXXXXX") == "d:deviceD1XXXXXXXXX"
    assert store.resolve_canonical("d:deviceD2XXXXXXXXX") == "d:deviceD2XXXXXXXXX"
    assert _canon_of(store, "deviceD1XXXXXXXXX") == "d:deviceD1XXXXXXXXX"
    # No alias may remain pointing to deleted s:S.
    dangling = store._get_conn().execute(
        "SELECT COUNT(*) c FROM canonical_alias WHERE new_canonical_id='s:76561198000000001'"
    ).fetchone()["c"]
    assert dangling == 0


def test_edge_uses_event_time_not_now(store):
    """Use events.received_at for edge first_seen, not job wall-clock time."""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    # Set received_at one month earlier to simulate historical backfill.
    old = (date.today() - timedelta(days=30)).isoformat() + "T00:00:00.000+08:00"
    conn = store._get_conn()
    conn.execute("UPDATE events SET received_at=?", (old,))
    conn.commit()
    store.build_edges_from_events()
    fs = conn.execute(
        "SELECT first_seen FROM device_steam_edges WHERE device_id='deviceAAAAAAAAAAAA'"
    ).fetchone()["first_seen"]
    assert fs == old, "边时间戳应取事件观测时间，而非回填运行时刻"
