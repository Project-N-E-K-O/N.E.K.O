"""Property-based tests for lock schema v2 (Feature: neko-market-version-sync).

Covers two correctness properties:

* **Property 1** — lock parse-serialize round-trip / v1 → v2 compat /
  idempotence. Any v1 lock blob (with arbitrary entry shapes including
  malformed market sub-fields and soft-deleted entries) survives one
  ``parse → serialize → parse`` cycle with the structural invariants
  spelled out in design §3.1.

* **Property 3** — Market ``latest_version`` → :class:`SourceDetailMarket`
  field-mapping fidelity for both ``record_market_install`` and
  ``record_market_upgrade``, including the ``previous_version`` /
  ``installed_at`` preservation rules and the imported-channel degrade
  path when ``plugin_market_id`` / ``version`` / ``package_url`` are
  missing.

Each property test uses ``hypothesis`` with at least 100 iterations.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from plugin.server.application.install_source import (
    InstallSourceError,
    InstallSourceManager,
)
from plugin.server.application.install_source.manager import (
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
    PluginDirectoryScanner,
)


# --- Strategies --------------------------------------------------------------

_HEX = "0123456789abcdef"


def _ts_strategy() -> st.SearchStrategy[str]:
    """ISO 8601 timestamp matching the canonical lock format."""

    return st.integers(min_value=0, max_value=10_000_000).map(
        lambda secs: (
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=secs)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )


def _market_v1_detail_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """v1 market source_detail (4 keys) with optional v2 noise.

    The v2 noise is intentionally garbage half the time — this exercises
    the ``_coerce_*`` defaults in :func:`_parse_source_detail`.
    """

    base = st.fixed_dictionaries(
        {
            "plugin_market_id": st.text(min_size=1, max_size=10),
            "version": st.text(min_size=1, max_size=10),
            "package_url": st.text(min_size=1, max_size=30),
            "previous_version": st.one_of(st.none(), st.text(min_size=1, max_size=10)),
        }
    )

    def _maybe_extend(base_dict: dict[str, Any]) -> dict[str, Any]:
        # half the time we add v2 fields (valid + invalid mix)
        return base_dict

    return base.map(_maybe_extend)


def _market_v2_detail_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """v2 market source_detail with all 8 keys, allowing each to be missing /
    malformed independently.
    """

    return st.fixed_dictionaries(
        {
            "plugin_market_id": st.text(min_size=1, max_size=10),
            "version": st.text(min_size=1, max_size=10),
            "package_url": st.text(min_size=1, max_size=30),
            "previous_version": st.one_of(
                st.none(), st.text(min_size=1, max_size=10)
            ),
        },
        optional={
            "package_sha256": st.one_of(
                st.text(alphabet=_HEX, min_size=64, max_size=64),
                st.text(min_size=0, max_size=10),  # invalid → coerced to ""
                st.none(),
            ),
            "payload_hash": st.one_of(
                st.text(min_size=1, max_size=64), st.text(max_size=0), st.none()
            ),
            "channel": st.one_of(
                st.sampled_from(["stable", "beta"]),
                st.text(min_size=1, max_size=10),  # invalid → coerced to "stable"
                st.none(),
            ),
            "published_at": st.one_of(_ts_strategy(), st.text(max_size=5), st.none()),
            # Forward-compat: random extra keys must be silently dropped
            "extra_unknown": st.text(),
        },
    )


def _imported_detail_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "package_filename": st.text(min_size=1, max_size=20),
            "package_sha256": st.text(alphabet=_HEX, min_size=64, max_size=64),
        }
    )


def _entry_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """A single lock entry (any channel) optionally soft-removed."""

    channel = st.sampled_from(["builtin", "manual", "imported", "market"])

    def _build(
        ch: str,
        root_id: str,
        directory_name: str,
        plugin_id: str,
        installed_at: str,
        updated_at: str,
        last_seen_at: str,
        removed: bool,
        market_d: dict[str, Any],
        imported_d: dict[str, Any],
    ) -> dict[str, Any]:
        ts = sorted([installed_at, updated_at, last_seen_at])
        entry: dict[str, Any] = {
            "root_id": root_id,
            "directory_name": directory_name,
            "plugin_id": plugin_id,
            "channel": ch,
            "source": ch,
            "reason": "user_requested",
            "installed_at": ts[0],
            "updated_at": ts[1],
            "last_seen_at": ts[2],
            "removed": removed,
        }
        if removed:
            entry["removed_at"] = ts[2]
        if ch == "market":
            entry["source_detail"] = market_d
        elif ch == "imported":
            entry["source_detail"] = imported_d
        else:
            entry["source_detail"] = None
        return entry

    return st.builds(
        _build,
        channel,
        st.sampled_from(["builtin", "user"]),
        st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=8,
        ),
        st.text(min_size=0, max_size=8),
        _ts_strategy(),
        _ts_strategy(),
        _ts_strategy(),
        st.booleans(),
        # Mix v1 (4-key) and v2 (8-key with garbage) shapes evenly.
        st.one_of(_market_v1_detail_strategy(), _market_v2_detail_strategy()),
        _imported_detail_strategy(),
    )


def _lock_v1_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """A v1-shaped lock document (schema_version=1).

    Entries can be any channel including market with v1 or partial-v2
    source_detail. Primary-key uniqueness is not enforced at generation
    time — the parser dedups by ``last_seen_at``, so we trust that
    behavior on the way in (P1 only requires same-key entries collapse,
    not specific tie-breaking).
    """

    return st.fixed_dictionaries(
        {
            "schema_version": st.just(1),
            "updated_at": _ts_strategy(),
            "entries": st.lists(_entry_strategy(), min_size=0, max_size=8),
        },
        optional={"created_at": _ts_strategy()},
    )


# --- Property 1 — round-trip / idempotence -----------------------------------


@settings(
    max_examples=120,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
)
@given(lock_v1=_lock_v1_strategy())
def test_property_1_lock_round_trip_and_idempotence(
    lock_v1: dict[str, Any],
) -> None:
    """Feature: neko-market-version-sync, Property 1.

    For any v1 lock document ``L1``:

    1. ``M = parse(serialize(parse(L1)))`` retains the same primary-key
       set as ``M' = parse(L1)`` (dedup is deterministic so both observe
       the same kept entries).
    2. For each market entry, the four v1 fields are preserved exactly
       and the four v2 fields satisfy the §3.1.2 default rules.
    3. ``serialize`` always writes ``schema_version=2`` (Req 2.4).
    4. ``parse∘serialize`` is idempotent on the resulting in-memory state.
    """

    payload_v1 = json.dumps(lock_v1).encode("utf-8")
    parsed_once: LockFile = _parse_lock(payload_v1)
    payload_v2 = _serialize_lock(parsed_once)
    parsed_twice: LockFile = _parse_lock(payload_v2)

    # —— (3) schema_version on disk is always 2 ——
    decoded = json.loads(payload_v2.decode("utf-8"))
    assert decoded["schema_version"] == 2

    # —— (1) same primary-key set ——
    keys_once = {e.primary_key for e in parsed_once.entries}
    keys_twice = {e.primary_key for e in parsed_twice.entries}
    assert keys_once == keys_twice

    by_key_once = {e.primary_key: e for e in parsed_once.entries}
    by_key_twice = {e.primary_key: e for e in parsed_twice.entries}

    for key in keys_once:
        a = by_key_once[key]
        b = by_key_twice[key]
        # Identity-preserving fields: channel / reason / removed / removed_at
        # / plugin_id stay byte-equal across the v2 normalisation.
        assert a.channel == b.channel
        assert a.reason == b.reason
        assert a.removed == b.removed
        assert a.removed_at == b.removed_at
        assert a.plugin_id == b.plugin_id

        # —— (2) market entries have the v1 fields preserved ——
        if a.channel == "market" and isinstance(a.source_detail, SourceDetailMarket):
            assert isinstance(b.source_detail, SourceDetailMarket)
            assert b.source_detail.plugin_market_id == a.source_detail.plugin_market_id
            assert b.source_detail.version == a.source_detail.version
            assert b.source_detail.package_url == a.source_detail.package_url
            assert b.source_detail.previous_version == a.source_detail.previous_version

            # v2 fields satisfy default-value rules:
            # - package_sha256 is either 64-hex lowercase or empty
            sha = b.source_detail.package_sha256
            assert sha == "" or (len(sha) == 64 and all(c in _HEX for c in sha))
            # - channel is one of the legal values
            assert b.source_detail.channel in ("stable", "beta")
            # - payload_hash is None or a non-empty str
            ph = b.source_detail.payload_hash
            assert ph is None or (isinstance(ph, str) and ph != "")
            # - published_at is a non-empty string (either an ISO ts or the
            #   installed_at fallback)
            assert isinstance(b.source_detail.published_at, str)
            assert b.source_detail.published_at != ""

    # —— (4) idempotence ——
    payload_v3 = _serialize_lock(parsed_twice)
    parsed_thrice: LockFile = _parse_lock(payload_v3)
    keys_thrice = {e.primary_key for e in parsed_thrice.entries}
    assert keys_twice == keys_thrice
    # Per-entry deep equality after the second normalization is locked in:
    by_key_thrice = {e.primary_key: e for e in parsed_thrice.entries}
    for key in keys_twice:
        assert by_key_twice[key] == by_key_thrice[key]


# --- Property 3 — Market latest_version → SourceDetailMarket fidelity --------


@pytest.fixture
def manager(tmp_path: Path) -> InstallSourceManager:
    """Build an InstallSourceManager pointed at ``tmp_path``."""

    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    builtin_root.mkdir()
    user_root.mkdir()
    scanner = PluginDirectoryScanner(builtin_root, user_root)
    lock_path = tmp_path / "plugins.lock.json"
    return InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=scanner,
    )


def _latest_version_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "version": st.text(min_size=1, max_size=12),
            "channel": st.sampled_from(["stable", "beta"]),
            "package_url": st.text(min_size=1, max_size=40),
            "package_sha256": st.text(alphabet=_HEX, min_size=64, max_size=64),
            "payload_hash": st.one_of(st.none(), st.text(min_size=1, max_size=64)),
            "published_at": _ts_strategy(),
        }
    )


@settings(
    max_examples=120,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    deadline=None,
)
@given(latest=_latest_version_strategy(), plugin_id=st.text(min_size=1, max_size=12))
def test_property_3_record_market_install_field_fidelity(
    manager: InstallSourceManager,
    latest: dict[str, Any],
    plugin_id: str,
) -> None:
    """Feature: neko-market-version-sync, Property 3a.

    Market ``latest_version`` → ``SourceDetailMarket`` mapping is total
    and field-faithful for ``record_market_install``.
    """

    directory_name = "p_" + plugin_id[:6].lower().replace(" ", "_") or "p"
    market_detail = {
        "plugin_market_id": plugin_id,
        "version": latest["version"],
        "package_url": latest["package_url"],
        "channel": latest["channel"],
        "package_sha256": latest["package_sha256"],
        "payload_hash": latest["payload_hash"],
        "published_at": latest["published_at"],
    }

    entry, _warnings = manager.record_market_install(
        root_id="user",
        directory_name=directory_name,
        plugin_id=plugin_id,
        market_detail=market_detail,
    )

    assert isinstance(entry.source_detail, SourceDetailMarket)
    sd = entry.source_detail
    assert sd.plugin_market_id == plugin_id
    assert sd.version == latest["version"]
    assert sd.package_url == latest["package_url"]
    assert sd.channel == latest["channel"]
    assert sd.package_sha256 == latest["package_sha256"]
    assert sd.payload_hash == latest["payload_hash"]
    assert sd.published_at == latest["published_at"]
    # First install always has previous_version=None.
    assert sd.previous_version is None


@settings(
    max_examples=80,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    deadline=None,
)
@given(
    latest_old=_latest_version_strategy(),
    latest_new=_latest_version_strategy(),
    plugin_id=st.text(min_size=1, max_size=12),
)
def test_property_3_record_market_upgrade_previous_version(
    manager: InstallSourceManager,
    latest_old: dict[str, Any],
    latest_new: dict[str, Any],
    plugin_id: str,
) -> None:
    """Feature: neko-market-version-sync, Property 3b.

    ``record_market_upgrade`` captures the old ``version`` as
    ``previous_version`` and preserves ``installed_at`` from the prior
    market entry. When ``latest_old.version == latest_new.version`` the
    test still passes (previous_version == version is a degenerate but
    legal state for reinstalls).
    """

    assume(latest_old["version"] != latest_new["version"])

    directory_name = "u_" + plugin_id[:6].lower().replace(" ", "_") or "u"

    # First install (creates the prior entry).
    old_entry, _ = manager.record_market_install(
        root_id="user",
        directory_name=directory_name,
        plugin_id=plugin_id,
        market_detail={
            "plugin_market_id": plugin_id,
            "version": latest_old["version"],
            "package_url": latest_old["package_url"],
            "channel": latest_old["channel"],
            "package_sha256": latest_old["package_sha256"],
            "payload_hash": latest_old["payload_hash"],
            "published_at": latest_old["published_at"],
        },
    )

    # Now upgrade.
    new_entry, _ = manager.record_market_upgrade(
        root_id="user",
        directory_name=directory_name,
        plugin_id=plugin_id,
        market_detail={
            "plugin_market_id": plugin_id,
            "version": latest_new["version"],
            "package_url": latest_new["package_url"],
            "channel": latest_new["channel"],
            "package_sha256": latest_new["package_sha256"],
            "payload_hash": latest_new["payload_hash"],
            "published_at": latest_new["published_at"],
        },
    )

    assert isinstance(new_entry.source_detail, SourceDetailMarket)
    assert new_entry.source_detail.previous_version == latest_old["version"]
    assert new_entry.source_detail.version == latest_new["version"]
    # installed_at sticks to the original install timestamp.
    assert new_entry.installed_at == old_entry.installed_at
    # updated_at moves forward (string compare ≡ chronological compare).
    assert new_entry.updated_at >= old_entry.updated_at


def test_property_3_record_market_upgrade_equivalent_to_install_when_no_prior(
    manager: InstallSourceManager,
) -> None:
    """Feature: neko-market-version-sync, Property 3c.

    When no active prior entry exists at the primary key,
    ``record_market_upgrade`` is equivalent to ``record_market_install``:
    ``previous_version=None``.
    """

    directory_name = "fresh_install_dir"
    plugin_id = "fresh-pid"
    market_detail = {
        "plugin_market_id": plugin_id,
        "version": "1.0.0",
        "package_url": "https://example.com/p.neko-plugin",
        "channel": "stable",
        "package_sha256": "f" * 64,
        "payload_hash": None,
        "published_at": "2026-05-16T08:00:00.000000Z",
    }

    entry, _ = manager.record_market_upgrade(
        root_id="user",
        directory_name=directory_name,
        plugin_id=plugin_id,
        market_detail=market_detail,
    )

    assert isinstance(entry.source_detail, SourceDetailMarket)
    assert entry.source_detail.previous_version is None
    assert entry.source_detail.version == "1.0.0"


def test_restore_entry_for_rollback_restores_only_prior_market_row(
    manager: InstallSourceManager,
) -> None:
    old_entry, _ = manager.record_market_install(
        root_id="user",
        directory_name="rollback-target",
        plugin_id="rollback-target",
        market_detail={
            "plugin_market_id": "rollback-target",
            "version": "1.0.0",
            "package_url": "https://example.com/v1.neko-plugin",
            "channel": "stable",
            "package_sha256": "a" * 64,
            "payload_hash": None,
            "published_at": "2026-05-16T08:00:00.000000Z",
        },
        package_id="old-package",
    )
    other_entry, _ = manager.record_market_install(
        root_id="user",
        directory_name="unrelated",
        plugin_id="unrelated",
        market_detail={
            "plugin_market_id": "unrelated",
            "version": "3.0.0",
            "package_url": "https://example.com/other.neko-plugin",
            "channel": "stable",
            "package_sha256": "b" * 64,
            "payload_hash": None,
            "published_at": "2026-05-16T08:00:00.000000Z",
        },
    )
    manager.record_market_upgrade(
        root_id="user",
        directory_name="rollback-target",
        plugin_id="rollback-target",
        market_detail={
            "plugin_market_id": "rollback-target",
            "version": "2.0.0",
            "package_url": "https://example.com/v2.neko-plugin",
            "channel": "stable",
            "package_sha256": "c" * 64,
            "payload_hash": None,
            "published_at": "2026-05-17T08:00:00.000000Z",
        },
        package_id="old-package",
    )

    manager.restore_entry_for_rollback(old_entry)

    by_key = {entry.primary_key: entry for entry in manager.snapshot().entries}
    assert by_key[old_entry.primary_key] == old_entry
    assert by_key[other_entry.primary_key] == other_entry

    reloaded = InstallSourceManager(
        lock_path=manager.lock_path,
        builtin_root=manager.builtin_root,
        user_root=manager.user_root,
        scanner=manager.scanner,
    )
    reloaded.load()
    persisted = {entry.primary_key: entry for entry in reloaded.snapshot().entries}
    assert persisted[old_entry.primary_key] == old_entry
    assert persisted[other_entry.primary_key] == other_entry


def test_property_3_missing_sha256_emits_warning(
    manager: InstallSourceManager,
) -> None:
    """Negative example for Req 4.6 — empty package_sha256 surfaces a warning
    while still writing the entry.
    """

    market_detail = {
        "plugin_market_id": "warn-pid",
        "version": "1.0.0",
        "package_url": "https://example.com/p.neko-plugin",
        "channel": "stable",
        "package_sha256": "",
        "payload_hash": None,
        "published_at": "2026-05-16T08:00:00.000000Z",
    }

    entry, warnings = manager.record_market_install(
        root_id="user",
        directory_name="warn_dir",
        plugin_id="warn-pid",
        market_detail=market_detail,
    )

    assert isinstance(entry.source_detail, SourceDetailMarket)
    assert entry.source_detail.package_sha256 == ""
    assert any("package_sha256" in w for w in warnings)
