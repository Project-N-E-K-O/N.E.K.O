"""Property-based tests for the install-source lock subsystem.

Covers the correctness invariants that directly support the four user
journeys (install provenance, uninstall audit, startup reconcile, version
upgrade tracking). Implementation-detail invariants (serializer ordering,
dirty-check, lock-free reader semantics) have been dropped — they test
how we wrote the code, not what it promises to users.
"""

from __future__ import annotations

import json
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
)

# --- Strategies --------------------------------------------------------------

_CHANNELS = ["builtin", "manual", "imported", "market"]
_ROOT_IDS = ["builtin", "user"]

_directory_name_strategy = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=10,
)


def _ts_strategy():
    return st.integers(min_value=0, max_value=10_000_000).map(
        lambda secs: (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=secs)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    )


def _source_detail_market_strategy():
    return st.builds(
        lambda pmid, ver, url, sha, ph, ch, pa: SourceDetailMarket(
            plugin_market_id=pmid,
            version=ver,
            package_url=url,
            package_sha256=sha,
            payload_hash=ph,
            channel=ch,
            published_at=pa,
            previous_version=None,
        ),
        st.text(min_size=1, max_size=10),
        st.text(min_size=1, max_size=10),
        st.text(min_size=1, max_size=30),
        st.text(
            alphabet=st.characters(
                min_codepoint=ord("0"), max_codepoint=ord("f"),
                whitelist_categories=["Nd", "Ll"],
            ),
            min_size=64,
            max_size=64,
        ),
        st.one_of(st.none(), st.text(min_size=1, max_size=64)),
        st.sampled_from(["stable", "beta"]),
        st.builds(
            lambda secs: (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=secs)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            st.integers(min_value=0, max_value=10**9),
        ),
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


def _lock_entry_strategy():
    def build(
        root_id: str, directory_name: str, plugin_id: str, ch: str,
        installed_at: str, updated_at: str, last_seen_at: str,
        rm: bool, detail_m: SourceDetailMarket, detail_i: SourceDetailImported,
    ) -> LockEntry:
        # Enforce monotone timestamps installed_at <= updated_at <= last_seen_at.
        ts = sorted([installed_at, updated_at, last_seen_at])
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
            reason="user_requested",
            installed_at=ts[0],
            updated_at=ts[1],
            last_seen_at=ts[2],
            removed=rm,
            removed_at=ts[2] if rm else None,
            source_detail=detail,
        )

    return st.builds(
        build,
        st.sampled_from(_ROOT_IDS),
        _directory_name_strategy,
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=0, max_size=10),
        st.sampled_from(_CHANNELS),
        _ts_strategy(),
        _ts_strategy(),
        _ts_strategy(),
        st.booleans(),
        _source_detail_market_strategy(),
        _source_detail_imported_strategy(),
    )


def _lock_file_strategy():
    return st.lists(_lock_entry_strategy(), min_size=0, max_size=8).map(
        lambda entries: LockFile(
            schema_version=1,
            entries=_dedup_entries(entries),
            updated_at="2024-01-01T00:00:00.000000Z",
            created_at=None,
        )
    )


def _dedup_entries(entries: list[LockEntry]) -> tuple[LockEntry, ...]:
    seen: dict[tuple[str, str], LockEntry] = {}
    for e in entries:
        seen[e.primary_key] = e
    return tuple(seen.values())


# --- Test helpers ------------------------------------------------------------


class _FakeScanner:
    def __init__(self, discovered: list[DiscoveredPlugin]) -> None:
        self._discovered = list(discovered)

    def scan(self) -> list[DiscoveredPlugin]:
        return list(self._discovered)


def _make_manager(
    tmp_path: Path,
    discovered: list[DiscoveredPlugin] | None = None,
    initial_lock: LockFile | None = None,
) -> InstallSourceManager:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    mgr = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin,
        user_root=user,
        scanner=_FakeScanner(discovered or []),  # type: ignore[arg-type]
    )
    if initial_lock is not None:
        mgr._current = initial_lock  # noqa: SLF001
    return mgr


# --- Properties --------------------------------------------------------------


@given(_lock_file_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_01_round_trip(lock: LockFile) -> None:
    """Round-trip: serialize → parse → serialize → parse preserves structure."""
    b = _serialize_lock(lock)
    parsed1 = _parse_lock(b)
    b2 = _serialize_lock(parsed1)
    parsed2 = _parse_lock(b2)
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
def test_property_04_timestamps_monotone(lock: LockFile) -> None:
    """installed_at <= updated_at <= last_seen_at in every parsed entry."""
    parsed = _parse_lock(_serialize_lock(lock))
    for e in parsed.entries:
        assert e.installed_at <= e.updated_at <= e.last_seen_at


@given(
    st.lists(
        st.tuples(st.sampled_from(_ROOT_IDS), _directory_name_strategy, _ts_strategy()),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_06_primary_key_dedup(triples: list[tuple[str, str, str]]) -> None:
    """Duplicate (root_id, directory_name) rows are deduped by last_seen_at max."""
    entries_json = [
        {
            "root_id": root_id,
            "directory_name": dn,
            "plugin_id": f"p{i}",
            "channel": "manual",
            "source": "manual",
            "reason": "user_requested",
            "installed_at": "2024-01-01T00:00:00.000000Z",
            "updated_at": ts,
            "last_seen_at": ts,
            "removed": False,
            "source_detail": None,
        }
        for i, (root_id, dn, ts) in enumerate(triples)
    ]
    raw = json.dumps(
        {
            "schema_version": 1,
            "updated_at": "2024-01-01T00:00:00.000000Z",
            "entries": entries_json,
        }
    ).encode("utf-8")
    parsed = _parse_lock(raw)
    by_key: dict[tuple[str, str], str] = {}
    for root_id, dn, ts in triples:
        pk = (root_id, dn)
        if pk not in by_key or ts > by_key[pk]:
            by_key[pk] = ts
    assert len(parsed.entries) == len(by_key)
    for e in parsed.entries:
        assert e.last_seen_at == by_key[e.primary_key]


def test_property_07_reconcile_three_way_diff(tmp_path: Path) -> None:
    """Reconcile covers all four branches: new / resurrect / soft-delete / stable."""
    t0 = "2024-01-01T00:00:00.000000Z"
    entries = [
        LockEntry(
            root_id="builtin", directory_name="bi", plugin_id="bi",
            channel="builtin", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=False, source_detail=None,
        ),
        LockEntry(
            root_id="user", directory_name="old_removed", plugin_id="or",
            channel="manual", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=True, removed_at=t0, source_detail=None,
        ),
        LockEntry(
            root_id="user", directory_name="to_be_removed", plugin_id="tbr",
            channel="manual", reason="user_requested",
            installed_at=t0, updated_at=t0, last_seen_at=t0,
            removed=False, source_detail=None,
        ),
    ]
    initial = LockFile(
        schema_version=1, entries=tuple(entries),
        updated_at=t0, created_at=t0,
    )
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
    by_key = {e.primary_key: e for e in mgr._current.entries}  # noqa: SLF001
    # builtin carries through, still live
    assert by_key[("builtin", "bi")].channel == "builtin"
    assert by_key[("builtin", "bi")].removed is False
    # old_removed resurrected
    assert by_key[("user", "old_removed")].removed is False
    assert by_key[("user", "old_removed")].removed_at is None
    assert by_key[("user", "old_removed")].installed_at == t0
    # to_be_removed soft-deleted
    assert by_key[("user", "to_be_removed")].removed is True
    assert by_key[("user", "to_be_removed")].removed_at is not None
    # new seeded as manual
    assert by_key[("user", "new")].channel == "manual"
    assert by_key[("user", "new")].reason == "user_requested"


def test_property_08_soft_delete_idempotent(tmp_path: Path) -> None:
    """Already-removed entries survive reconcile untouched."""
    t0 = "2024-01-01T00:00:00.000000Z"
    initial = LockFile(
        schema_version=1,
        entries=(
            LockEntry(
                root_id="user", directory_name="already_removed", plugin_id="ar",
                channel="manual", reason="user_requested",
                installed_at=t0, updated_at=t0, last_seen_at=t0,
                removed=True, removed_at=t0, source_detail=None,
            ),
        ),
        updated_at=t0, created_at=t0,
    )
    mgr = _make_manager(tmp_path, discovered=[], initial_lock=initial)
    mgr.reconcile()
    e1 = mgr._current.entries[0]  # noqa: SLF001
    mgr.reconcile()
    e2 = mgr._current.entries[0]  # noqa: SLF001
    assert e1.removed == e2.removed == True
    assert e1.removed_at == e2.removed_at
    assert e1.updated_at == e2.updated_at


def test_property_09_record_import_semantics(tmp_path: Path) -> None:
    """record_import sets channel/reason/source_detail; installed_at preserved on re-call."""
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "some_plugin"
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text('[plugin]\nid = "some_plugin"\n', encoding="utf-8")

    mgr.record_import(
        directory_path=target,
        package_filename="some_plugin.neko-plugin",
        package_sha256="a" * 64,
    )
    e = mgr._current.entries[0]  # noqa: SLF001
    assert e.channel == "imported"
    assert e.reason == "user_requested"
    assert e.plugin_id == "some_plugin"
    assert isinstance(e.source_detail, SourceDetailImported)
    assert e.source_detail.package_sha256 == "a" * 64
    first_installed = e.installed_at

    import time
    time.sleep(0.001)
    mgr.record_import(
        directory_path=target,
        package_filename="some_plugin.neko-plugin",
        package_sha256="b" * 64,
    )
    e2 = mgr._current.entries[0]  # noqa: SLF001
    assert e2.installed_at == first_installed
    assert e2.source_detail.package_sha256 == "b" * 64


def test_property_10_record_market_captures_previous_version(tmp_path: Path) -> None:
    """Version upgrade via record_market captures previous_version; installed_at preserved."""
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "from_market"
    target.mkdir(parents=True, exist_ok=True)

    mgr.record_market(
        directory_path=target,
        plugin_market_id="mid-1",
        version="1.0.0",
        package_url="https://market.example/p.neko-plugin",
    )
    first_installed = mgr._current.entries[0].installed_at  # noqa: SLF001

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
    assert e.source_detail.version == "1.0.1"
    assert e.source_detail.previous_version == "1.0.0"
    assert e.installed_at == first_installed


def test_property_11_builtin_channel_locked(tmp_path: Path) -> None:
    """record_import / record_market / mark_removed all refuse builtin paths."""
    mgr = _make_manager(tmp_path)
    builtin_target = mgr.builtin_root / "core"
    builtin_target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(InstallSourceError) as info:
        mgr.record_import(
            directory_path=builtin_target,
            package_filename="core.neko-plugin",
            package_sha256="a" * 64,
        )
    assert info.value.code == "BUILTIN_CHANNEL_LOCKED"
    with pytest.raises(InstallSourceError) as info:
        mgr.record_market(
            directory_path=builtin_target,
            plugin_market_id="core",
            version="1.0",
            package_url="url",
        )
    assert info.value.code == "BUILTIN_CHANNEL_LOCKED"
    with pytest.raises(InstallSourceError) as info:
        mgr.mark_removed(directory_path=builtin_target)
    assert info.value.code == "BUILTIN_CHANNEL_LOCKED"


def test_property_12_to_api_view_path_priority(tmp_path: Path) -> None:
    """to_api_view: path match beats plugin_id match; miss returns default shape."""
    mgr = _make_manager(tmp_path)
    target = mgr.user_root / "my_plugin"
    target.mkdir(parents=True, exist_ok=True)
    mgr.record_import(
        directory_path=target,
        package_filename="my.neko-plugin",
        package_sha256="c" * 64,
    )
    # Path matches even if plugin_id input is wrong
    view = mgr.to_api_view("nonexistent", directory_path=target)
    assert view["source"] == "imported"
    # plugin_id fallback
    view2 = mgr.to_api_view("my_plugin", directory_path=None)
    assert view2["source"] == "imported"
    # Miss returns default
    view3 = mgr.to_api_view("unknown_plugin", directory_path=None)
    assert view3 == {
        "source": "unknown", "reason": None,
        "installed_at": None, "source_detail": None,
    }
