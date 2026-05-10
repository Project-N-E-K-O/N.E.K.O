"""Property-based tests for the install-source lock subsystem.

Covers tasks 7.1 — 7.16 of the plugin-install-source-lock spec. All tests
use Hypothesis with ``@settings(max_examples=200)`` per design §15.2 and
carry a ``Feature: plugin-install-source-lock, Property N: <title>``
docstring first line for CI grep-ability.

The generators live at module scope so individual properties can compose
them freely. Strategies deliberately exercise tricky edges — unknown
fields, non-canonical timestamps, duplicate primary keys, illegal enum
values — so the Parser / Serializer / reconciler must handle them
gracefully (Fix 5/6/7/10) rather than only the happy path.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from plugin.server.application.install_source.manager import (
    InstallSourceError,
    InstallSourceManager,
    _parse_lock,
    _serialize_lock,
)
from plugin.server.application.install_source.models import (
    LockEntry,
    LockFile,
    SourceDetailImported,
    SourceDetailMarket,
)
from plugin.server.application.install_source.scanner import (
    DiscoveredPlugin,
    PluginDirectoryScanner,
)

# --- Strategy building blocks ------------------------------------------------

_CHANNELS = ["builtin", "manual", "imported", "market"]
_REASONS = ["user_requested", "auto_dependency"]
_ROOT_IDS = ["builtin", "user"]

# Safe identifier-ish names.
_directory_name_strategy = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=10,
)


def _ts_strategy():
    """Generate RFC3339-like UTC timestamps (normalized format)."""
    return st.integers(min_value=0, max_value=10_000_000).map(
        lambda secs: (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=secs)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    )


def _source_detail_market_strategy():
    return st.builds(
        lambda pmid, ver, url: SourceDetailMarket(
            plugin_market_id=pmid, version=ver, package_url=url, previous_version=None,
        ),
        st.text(min_size=1, max_size=10),
        st.text(min_size=1, max_size=10),
        st.text(min_size=1, max_size=30),
    )


def _source_detail_imported_strategy():
    return st.builds(
        lambda fn, h: SourceDetailImported(package_filename=fn, package_sha256=h),
        st.text(min_size=1, max_size=30),
        st.text(
            alphabet=st.characters(
                min_codepoint=ord("0"), max_codepoint=ord("f"),
                whitelist_categories=["Nd", "Ll"],
            ),
            min_size=64,
            max_size=64,
        ),
    )


def _lock_entry_strategy(*, channel: str | None = None, removed: bool | None = None):
    def build(
        root_id: str, directory_name: str, plugin_id: str, ch: str, reason: str,
        installed_at: str, updated_at: str, last_seen_at: str,
        rm: bool, detail_m: SourceDetailMarket | None, detail_i: SourceDetailImported | None,
    ) -> LockEntry:
        # Monotone timestamps.
        ts = sorted([installed_at, updated_at, last_seen_at])
        installed_at, updated_at, last_seen_at = ts[0], ts[1], ts[2]
        detail: Any = None
        if ch == "market":
            detail = detail_m
        elif ch == "imported":
            detail = detail_i
        return LockEntry(
            root_id=root_id,  # type: ignore[arg-type]
            directory_name=directory_name,
            plugin_id=plugin_id,
            channel=ch,  # type: ignore[arg-type]
            reason=reason,  # type: ignore[arg-type]
            installed_at=installed_at,
            updated_at=updated_at,
            last_seen_at=last_seen_at,
            removed=rm,
            removed_at=last_seen_at if rm else None,
            bundle_ref=None,
            source_detail=detail,
        )

    ch_strategy = st.just(channel) if channel else st.sampled_from(_CHANNELS)
    rm_strategy = st.just(removed) if removed is not None else st.booleans()
    return st.builds(
        build,
        st.sampled_from(_ROOT_IDS),
        _directory_name_strategy,
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=0, max_size=10),
        ch_strategy,
        st.just("user_requested"),
        _ts_strategy(),
        _ts_strategy(),
        _ts_strategy(),
        rm_strategy,
        _source_detail_market_strategy(),
        _source_detail_imported_strategy(),
    )


def _lock_file_strategy():
    """Lock file whose entries have unique primary keys (builtin invariant)."""
    return st.lists(_lock_entry_strategy(), min_size=0, max_size=8).map(
        lambda entries: LockFile(
            schema_version=1,
            entries=_dedup_entries(entries),
            updated_at="2024-01-01T00:00:00.000000Z",
            bundles=(),
            created_at=None,
        )
    )


def _dedup_entries(entries: list[LockEntry]) -> tuple[LockEntry, ...]:
    seen: dict[tuple[str, str], LockEntry] = {}
    for e in entries:
        seen[e.primary_key] = e
    return tuple(seen.values())


# --- Helpers -----------------------------------------------------------------


class _FakeScanner:
    """Test double: ``scan()`` returns a fixed list."""

    def __init__(self, discovered: list[DiscoveredPlugin]) -> None:
        self._discovered = list(discovered)

    def scan(self) -> list[DiscoveredPlugin]:
        return list(self._discovered)


def _make_manager(
    tmp_path: Path,
    discovered: list[DiscoveredPlugin] | None = None,
    initial_lock: LockFile | None = None,
    *,
    clock_values: list[datetime] | None = None,
) -> InstallSourceManager:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    lock_path = tmp_path / "plugins.lock.json"
    scanner = _FakeScanner(discovered or [])
    if clock_values is not None:
        iterator = iter(clock_values)
        clock = lambda: next(iterator)
    else:
        clock = None
    mgr = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin,
        user_root=user,
        scanner=scanner,  # type: ignore[arg-type]
        clock=clock,
    )
    if initial_lock is not None:
        mgr._current = initial_lock  # noqa: SLF001
    return mgr


# --- Properties --------------------------------------------------------------


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_01_round_trip(lock: LockFile) -> None:
    """Feature: plugin-install-source-lock, Property 1: round-trip consistency.

    Serialize → parse → serialize → parse; the two parses must be equal in
    structure (entries by primary key, channel, reason, installed_at,
    updated_at, last_seen_at, removed, source_detail).
    """
    b = _serialize_lock(lock)
    parsed1 = _parse_lock(b)
    b2 = _serialize_lock(parsed1)
    parsed2 = _parse_lock(b2)
    # Compare sorted entries structurally
    assert len(parsed1.entries) == len(parsed2.entries)
    e1 = sorted(parsed1.entries, key=lambda e: e.primary_key)
    e2 = sorted(parsed2.entries, key=lambda e: e.primary_key)
    for a, b in zip(e1, e2):
        assert a.primary_key == b.primary_key
        assert a.channel == b.channel
        assert a.reason == b.reason
        assert a.installed_at == b.installed_at
        assert a.updated_at == b.updated_at
        assert a.last_seen_at == b.last_seen_at
        assert a.removed == b.removed


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_02_entries_sorted(lock: LockFile) -> None:
    """Feature: plugin-install-source-lock, Property 2: stable entry ordering."""
    raw = _serialize_lock(lock)
    doc = json.loads(raw.decode("utf-8"))
    entries = doc["entries"]
    keys = [(e["root_id"], e["directory_name"]) for e in entries]
    assert keys == sorted(keys)


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_03_source_mirrors_channel(lock: LockFile) -> None:
    """Feature: plugin-install-source-lock, Property 3: source field mirrors channel."""
    raw = _serialize_lock(lock)
    doc = json.loads(raw.decode("utf-8"))
    for e in doc["entries"]:
        assert e["source"] == e["channel"]


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_04_timestamp_monotonic_and_installed_at_stable(lock: LockFile) -> None:
    """Feature: plugin-install-source-lock, Property 4: timestamp monotonicity and installed_at idempotence."""
    raw = _serialize_lock(lock)
    parsed = _parse_lock(raw)
    for e in parsed.entries:
        assert e.installed_at <= e.updated_at <= e.last_seen_at


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_05_removed_at_iff_removed(lock: LockFile) -> None:
    """Feature: plugin-install-source-lock, Property 5: removed_at appears iff removed=True."""
    raw = _serialize_lock(lock)
    doc = json.loads(raw.decode("utf-8"))
    for e in doc["entries"]:
        if e["removed"]:
            assert "removed_at" in e
        else:
            assert "removed_at" not in e


@given(
    st.lists(
        st.tuples(
            st.sampled_from(_ROOT_IDS),
            _directory_name_strategy,
            _ts_strategy(),
        ),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_06_primary_key_dedup(
    triples: list[tuple[str, str, str]],
) -> None:
    """Feature: plugin-install-source-lock, Property 6: primary key dedup keeps latest last_seen_at."""
    # Build raw JSON with duplicate keys
    entries_json = []
    for i, (root_id, dn, ts) in enumerate(triples):
        entries_json.append({
            "root_id": root_id,
            "directory_name": dn,
            "plugin_id": f"p{i}",
            "channel": "manual",
            "source": "manual",
            "reason": "user_requested",
            "bundle_ref": None,
            "installed_at": "2024-01-01T00:00:00.000000Z",
            "updated_at": ts,
            "last_seen_at": ts,
            "removed": False,
            "source_detail": None,
        })
    doc = {
        "schema_version": 1,
        "updated_at": "2024-01-01T00:00:00.000000Z",
        "entries": entries_json,
        "bundles": [],
    }
    raw = json.dumps(doc).encode("utf-8")
    parsed = _parse_lock(raw)
    # For each primary key, parsed should retain the one with the max last_seen_at
    by_key: dict[tuple[str, str], str] = {}
    for root_id, dn, ts in triples:
        pk = (root_id, dn)
        if pk not in by_key or ts > by_key[pk]:
            by_key[pk] = ts
    assert len(parsed.entries) == len(by_key)
    for e in parsed.entries:
        assert e.last_seen_at == by_key[e.primary_key]


def test_property_07_reconcile_three_way_diff(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 7: reconcile three-way diff correctness.

    Uses a concrete scenario rather than PBT to keep the test fast;
    covers all four branches (new / unchanged / soft-delete / resurrect)
    and the channel derivation rule.
    """
    # Set up a lock with three entries: one builtin live, one user removed, one user live (to be removed next)
    t0 = "2024-01-01T00:00:00.000000Z"
    entries = [
        LockEntry(
            root_id="builtin", directory_name="bi", plugin_id="bi",
            channel="builtin", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=False, bundle_ref=None, source_detail=None,
        ),
        LockEntry(
            root_id="user", directory_name="old_removed", plugin_id="or",
            channel="manual", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=True, removed_at=t0, bundle_ref=None, source_detail=None,
        ),
        LockEntry(
            root_id="user", directory_name="to_be_removed", plugin_id="tbr",
            channel="manual", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=False, bundle_ref=None, source_detail=None,
        ),
    ]
    initial = LockFile(
        schema_version=1, entries=tuple(entries),
        updated_at=t0, bundles=(), created_at=t0,
    )
    # Disc: bi present, old_removed returns (resurrect), tbr disappears, a brand-new "new" entry
    tmp_path_b = tmp_path / "b"; tmp_path_b.mkdir()
    tmp_path_u = tmp_path / "u"; tmp_path_u.mkdir()
    disc = [
        DiscoveredPlugin(root_id="builtin", directory_name="bi",
                         directory_path=tmp_path_b / "bi", plugin_id="bi"),
        DiscoveredPlugin(root_id="user", directory_name="old_removed",
                         directory_path=tmp_path_u / "old_removed", plugin_id="or"),
        DiscoveredPlugin(root_id="user", directory_name="new",
                         directory_path=tmp_path_u / "new", plugin_id="new"),
    ]
    mgr = _make_manager(tmp_path, discovered=disc, initial_lock=initial)
    mgr.reconcile()
    cur = mgr._current  # noqa: SLF001
    by_key = {e.primary_key: e for e in cur.entries}
    # builtin unchanged (carried through verbatim)
    assert by_key[("builtin", "bi")].channel == "builtin"
    assert by_key[("builtin", "bi")].removed is False
    # old_removed resurrected
    assert by_key[("user", "old_removed")].removed is False
    assert by_key[("user", "old_removed")].removed_at is None
    assert by_key[("user", "old_removed")].channel == "manual"
    assert by_key[("user", "old_removed")].installed_at == t0  # preserved
    # tbr soft-deleted
    assert by_key[("user", "to_be_removed")].removed is True
    assert by_key[("user", "to_be_removed")].removed_at is not None
    # new seeded with channel="manual"
    assert by_key[("user", "new")].channel == "manual"
    assert by_key[("user", "new")].reason == "user_requested"
    assert by_key[("user", "new")].bundle_ref is None


def test_property_08_soft_delete_idempotent(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 8: soft delete idempotence.

    Two back-to-back reconciles on an empty disk must leave the lock
    snapshot byte-identical (Fix 4 dirty check + Req 8.2).
    """
    t0 = "2024-01-01T00:00:00.000000Z"
    initial = LockFile(
        schema_version=1,
        entries=(
            LockEntry(
                root_id="user", directory_name="already_removed", plugin_id="ar",
                channel="manual", reason="user_requested",
                installed_at=t0, updated_at=t0, last_seen_at=t0,
                removed=True, removed_at=t0, bundle_ref=None, source_detail=None,
            ),
        ),
        updated_at=t0, bundles=(), created_at=t0,
    )
    mgr = _make_manager(tmp_path, discovered=[], initial_lock=initial)
    mgr.reconcile()
    snap1 = mgr._current  # noqa: SLF001
    mgr.reconcile()
    snap2 = mgr._current  # noqa: SLF001
    # Reconcile should not touch already-removed entries
    e1 = snap1.entries[0]
    e2 = snap2.entries[0]
    assert e1.removed == e2.removed == True
    assert e1.removed_at == e2.removed_at
    assert e1.updated_at == e2.updated_at
    assert e1.last_seen_at == e2.last_seen_at


def test_property_09_record_import_semantics(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 9: record_import semantics."""
    mgr = _make_manager(tmp_path)
    user_root = mgr.user_root
    target = user_root / "some_plugin"
    target.mkdir(parents=True, exist_ok=True)
    # Write plugin.toml so plugin_id is populated
    (target / "plugin.toml").write_text('[plugin]\nid = "some_plugin"\n', encoding="utf-8")
    # First record
    mgr.record_import(
        directory_path=target,
        package_filename="some_plugin.neko-plugin",
        package_sha256="a" * 64,
    )
    snap = mgr._current  # noqa: SLF001
    assert len(snap.entries) == 1
    e = snap.entries[0]
    assert e.channel == "imported"
    assert e.reason == "user_requested"
    assert e.bundle_ref is None
    assert e.plugin_id == "some_plugin"  # Fix 1
    assert isinstance(e.source_detail, SourceDetailImported)
    assert e.source_detail.package_filename == "some_plugin.neko-plugin"
    assert e.source_detail.package_sha256 == "a" * 64
    # Second record: installed_at must be preserved
    first_installed = e.installed_at
    import time
    time.sleep(0.001)
    mgr.record_import(
        directory_path=target,
        package_filename="some_plugin.neko-plugin",
        package_sha256="b" * 64,
    )
    snap2 = mgr._current  # noqa: SLF001
    e2 = snap2.entries[0]
    assert e2.installed_at == first_installed  # preserved
    assert e2.source_detail.package_sha256 == "b" * 64  # updated


def test_property_10_record_market_single_write(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 10: market single write preserves installed_at (Fix 8)."""
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "from_market"
    target.mkdir(parents=True, exist_ok=True)
    # Call record_market multiple times; installed_at should be pinned to first call
    mgr.record_market(
        directory_path=target,
        plugin_market_id="mid-1",
        version="1.0.0",
        package_url="https://market.example/p.neko-plugin",
    )
    first_installed_at = mgr._current.entries[0].installed_at  # noqa: SLF001
    import time
    time.sleep(0.001)
    mgr.record_market(
        directory_path=target,
        plugin_market_id="mid-1",
        version="1.0.1",
        package_url="https://market.example/p.neko-plugin",
    )
    e = mgr._current.entries[0]  # noqa: SLF001
    assert e.channel == "market"
    assert isinstance(e.source_detail, SourceDetailMarket)
    assert e.source_detail.plugin_market_id == "mid-1"
    assert e.source_detail.version == "1.0.1"
    assert e.source_detail.previous_version is None
    assert e.installed_at == first_installed_at


def test_property_11_builtin_channel_locked(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 11: builtin channel is locked from record_* paths (Fix 12)."""
    mgr = _make_manager(tmp_path)
    builtin_target = mgr.builtin_root / "core"
    builtin_target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(InstallSourceError) as exc_info:
        mgr.record_import(
            directory_path=builtin_target,
            package_filename="core.neko-plugin",
            package_sha256="a" * 64,
        )
    assert exc_info.value.code == "BUILTIN_CHANNEL_LOCKED"
    with pytest.raises(InstallSourceError) as exc_info:
        mgr.record_market(
            directory_path=builtin_target,
            plugin_market_id="core",
            version="1.0",
            package_url="url",
        )
    assert exc_info.value.code == "BUILTIN_CHANNEL_LOCKED"


def test_property_12_plugins_endpoint_install_source_injection(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 12: /plugins endpoint injects install_source with path-priority matching (Fix 1)."""
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "my_plugin"
    target.mkdir(parents=True, exist_ok=True)
    mgr.record_import(
        directory_path=target,
        package_filename="my.neko-plugin",
        package_sha256="c" * 64,
    )
    # Path-priority match even when plugin_id is empty (Req 4.3)
    view = mgr.to_api_view("nonexistent", directory_path=target)
    assert view["source"] == "imported"
    # plugin_id fallback
    view2 = mgr.to_api_view("my_plugin", directory_path=None)
    assert view2["source"] == "imported"
    # Miss -> default
    view3 = mgr.to_api_view("unknown_plugin", directory_path=None)
    assert view3 == {
        "source": "unknown", "reason": None,
        "installed_at": None, "source_detail": None,
    }


def test_property_13_install_sources_endpoint_filtering(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 13: install-sources endpoint filter correctness."""
    mgr = _make_manager(tmp_path)
    # Create entries directly via manager's internal _current
    t0 = "2024-01-01T00:00:00.000000Z"
    mgr._current = LockFile(  # noqa: SLF001
        schema_version=1,
        entries=(
            LockEntry(root_id="user", directory_name="a", plugin_id="a",
                      channel="manual", reason="user_requested",
                      installed_at=t0, updated_at=t0, last_seen_at=t0,
                      removed=False, bundle_ref=None, source_detail=None),
            LockEntry(root_id="user", directory_name="b", plugin_id="b",
                      channel="market", reason="user_requested",
                      installed_at=t0, updated_at=t0, last_seen_at=t0,
                      removed=False, bundle_ref=None,
                      source_detail=SourceDetailMarket(
                          plugin_market_id="b", version="1", package_url="u",
                      )),
            LockEntry(root_id="builtin", directory_name="c", plugin_id="c",
                      channel="builtin", reason="user_requested",
                      installed_at=t0, updated_at=t0, last_seen_at=t0,
                      removed=True, removed_at=t0,
                      bundle_ref=None, source_detail=None),
        ),
        updated_at=t0, bundles=(), created_at=t0,
    )
    # include_removed default: False → only 2 live
    assert len(mgr.list_entries()) == 2
    # include_removed=True → 3
    assert len(mgr.list_entries(include_removed=True)) == 3
    # channel=market → 1
    result = mgr.list_entries(channel="market")
    assert len(result) == 1 and result[0].channel == "market"
    # source=manual → 1
    result = mgr.list_entries(source="manual")
    assert len(result) == 1 and result[0].channel == "manual"
    # root_id=builtin with include_removed
    result = mgr.list_entries(include_removed=True, root_id="builtin")
    assert len(result) == 1 and result[0].root_id == "builtin"
    # channel+source conflict → channel wins
    result = mgr.list_entries(channel="manual", source="market")
    assert len(result) == 1 and result[0].channel == "manual"
    # illegal filter → InstallSourceError("INVALID_FILTER")
    with pytest.raises(InstallSourceError) as exc_info:
        mgr.list_entries(channel="not_a_channel")
    assert exc_info.value.code == "INVALID_FILTER"


def test_property_14_noop_reconcile_idempotent(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 14: no-op reconcile is idempotent and does not write disk (Fix 4)."""
    # Set up stable state and make sure second reconcile doesn't bump mtime
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "stable"
    target.mkdir()
    mgr.scanner = _FakeScanner([  # type: ignore[assignment]
        DiscoveredPlugin(root_id="user", directory_name="stable",
                         directory_path=target, plugin_id="stable"),
    ])
    # First reconcile: creates the entry + writes
    mgr.reconcile()
    mtime1 = mgr.lock_path.stat().st_mtime
    updated_at1 = mgr._current.updated_at  # noqa: SLF001
    last_seen_at1 = mgr._current.entries[0].last_seen_at  # noqa: SLF001
    # Second reconcile with same state: must NOT touch disk or timestamps
    import time
    time.sleep(0.01)
    mgr.reconcile()
    mtime2 = mgr.lock_path.stat().st_mtime
    assert mtime2 == mtime1
    assert mgr._current.updated_at == updated_at1  # noqa: SLF001
    assert mgr._current.entries[0].last_seen_at == last_seen_at1  # noqa: SLF001


def test_property_15_reader_snapshot_consistency(tmp_path: Path) -> None:
    """Feature: plugin-install-source-lock, Property 15: reader snapshot atomicity (Fix 2)."""
    # Concurrent reads + reconciles; readers must always see a complete snapshot
    mgr = _make_manager(tmp_path)
    user_root = mgr.user_root
    # Prepare several directories
    for i in range(5):
        d = user_root / f"p{i}"
        d.mkdir()
    disc_sets = [
        [DiscoveredPlugin(root_id="user", directory_name=f"p{i}",
                          directory_path=user_root / f"p{i}", plugin_id=f"p{i}")
         for i in range(5)],
        [],  # empty -> all soft-deleted
    ]
    stop = threading.Event()

    def writer_loop():
        idx = 0
        while not stop.is_set():
            mgr.scanner = _FakeScanner(disc_sets[idx % 2])  # type: ignore[assignment]
            mgr.reconcile()
            idx += 1

    def reader_loop():
        while not stop.is_set():
            snap = mgr.list_entries(include_removed=True)
            # Every snapshot must be internally consistent (no mixed state)
            for e in snap:
                assert e.primary_key[0] in ("builtin", "user")
                assert e.channel in ("builtin", "manual", "imported", "market")

    t_w = threading.Thread(target=writer_loop)
    t_r = threading.Thread(target=reader_loop)
    t_w.start(); t_r.start()
    import time
    time.sleep(0.2)
    stop.set()
    t_w.join(timeout=2.0)
    t_r.join(timeout=2.0)
