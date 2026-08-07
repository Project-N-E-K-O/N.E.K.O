"""Versioned, read-only storage for the Neko WoWS ship catalog."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from plugin.plugins.neko_wows.ship_data.store import (
    CATALOG_SCHEMA_VERSION,
    NullCatalogSnapshot,
    ShipCatalogStore,
    create_catalog_schema,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_fixture_catalog(
    root: Path,
    *,
    schema_version: int = CATALOG_SCHEMA_VERSION,
    manifest_sha256: str | None = None,
) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "ship-catalog-test.sqlite3"
    conn = sqlite3.connect(db_path)
    create_catalog_schema(conn)
    conn.execute(
        "INSERT INTO catalog_meta ("
        "id, schema_version, catalog_version, game_version, channel, "
        "source_repo, source_commit, source_paths_json, source_sha256_json, "
        "generated_at_utc, builder_version, content_sha256, default_language, "
        "ship_count, profile_count"
        ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            schema_version,
            "15.6.0.0.12830008:c4f6ae75:v1",
            "15.6.0.0.12830008",
            "live",
            "https://github.com/wowsinfo/data",
            "c4f6ae751548c8e9a4887f69555a847d1cc5a300",
            json.dumps(["live/app/data/wowsinfo.json", "live/app/lang/lang.json"]),
            json.dumps({"wowsinfo.json": "a" * 64, "lang.json": "b" * 64}),
            "2026-08-07T00:00:00Z",
            "1",
            "c" * 64,
            "zh-CN",
            1,
            1,
        ),
    )
    conn.execute(
        "INSERT INTO ships ("
        "ship_id, ship_index, name_key, display_name, nation, ship_class, tier, "
        "is_premium, is_special, is_paper, availability_group"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (4276041424, "PJSC013", "IDS_PJSC013", "大和", "Japan", "Battleship",
         10, 0, 1, 0, "special"),
    )
    for alias_norm, alias, language, kind in (
        ("yamato", "Yamato", "en", "localized_name"),
        ("大和", "大和", "zh-CN", "localized_name"),
        ("pjsc013", "pjsc013", "und", "ship_index"),
    ):
        conn.execute(
            "INSERT INTO ship_aliases "
            "(alias_norm, ship_id, alias, language, alias_kind) "
            "VALUES (?, ?, ?, ?, ?)",
            (alias_norm, 4276041424, alias, language, kind),
        )
    profile = {
        "survivability": {"hit_points": 97200},
        "main_battery": {"range_m": 26630, "reload_s": 30.0, "sigma": 2.1},
    }
    conn.execute(
        "INSERT INTO ship_profiles ("
        "profile_id, ship_id, configuration, variant_key, is_primary, "
        "profile_schema_version, profile_json, profile_sha256"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "4276041424:reference_top:primary",
            4276041424,
            "reference_top",
            "primary",
            1,
            1,
            json.dumps(profile, ensure_ascii=False, sort_keys=True),
            "d" * 64,
        ),
    )
    conn.execute(
        "INSERT INTO module_selections ("
        "profile_id, slot, module_key, module_index, selection_kind, "
        "component_ids_json"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            "4276041424:reference_top:primary",
            "artillery",
            "PJUA013_460MM",
            1,
            "terminal",
            json.dumps(["PJUA013_460MM"]),
        ),
    )
    conn.commit()
    conn.close()

    manifest_path = root / "active.json"
    manifest_path.write_text(
        json.dumps({
            "manifest_version": 1,
            "active_file": db_path.name,
            "sha256": manifest_sha256 or _sha256(db_path),
            "schema_version": schema_version,
            "catalog_version": "15.6.0.0.12830008:c4f6ae75:v1",
            "game_version": "15.6.0.0.12830008",
        }),
        encoding="utf-8",
    )
    return db_path, manifest_path


def test_store_opens_valid_manifest_and_returns_primary_profile(tmp_path):
    build_fixture_catalog(tmp_path)

    snapshot = ShipCatalogStore(tmp_path).snapshot()
    try:
        assert snapshot.meta is not None
        assert snapshot.meta.game_version == "15.6.0.0.12830008"
        assert snapshot.meta.source_commit.startswith("c4f6ae75")
        ship = snapshot.ship(4276041424)
        assert ship is not None
        assert ship.display_name == "大和"
        profile = snapshot.primary_profile(ship.ship_id)
        assert profile is not None
        assert profile.data["survivability"]["hit_points"] == 97200
        assert profile.data["main_battery"]["sigma"] == 2.1
    finally:
        snapshot.close()


def test_store_snapshot_localizes_display_name_without_changing_exact_aliases(
    tmp_path,
):
    build_fixture_catalog(tmp_path)
    store = ShipCatalogStore(tmp_path)

    english = store.snapshot(language="en")
    try:
        ship = english.ship(4276041424)
        assert ship is not None
        assert ship.display_name == "Yamato"
        assert [
            candidate.ship_id
            for candidate in english.alias_candidates("yamato")
        ] == [4276041424]
        assert english.alias_candidates("yamto") == ()
    finally:
        english.close()

    missing_language = store.snapshot(language="ja")
    try:
        ship = missing_language.ship(4276041424)
        assert ship is not None
        assert ship.display_name == "大和"
    finally:
        missing_language.close()


def test_store_returns_exact_alias_candidates(tmp_path):
    build_fixture_catalog(tmp_path)
    snapshot = ShipCatalogStore(tmp_path).snapshot()
    try:
        assert [ship.ship_id for ship in snapshot.alias_candidates("yamato")] == [
            4276041424]
        assert snapshot.alias_candidates("yamto") == ()
    finally:
        snapshot.close()


def test_store_exposes_safe_active_manifest_diagnostics(tmp_path):
    build_fixture_catalog(tmp_path)

    info = ShipCatalogStore(tmp_path).active_manifest_info()

    assert info == {
        "catalog_version": "15.6.0.0.12830008:c4f6ae75:v1",
        "game_version": "15.6.0.0.12830008",
        "schema_version": CATALOG_SCHEMA_VERSION,
    }


def test_active_manifest_diagnostics_degrade_for_malformed_json(tmp_path):
    (tmp_path / "active.json").write_text("{not-json", encoding="utf-8")

    assert ShipCatalogStore(tmp_path).active_manifest_info() == {
        "catalog_version": "",
        "game_version": "",
        "schema_version": None,
    }


def test_active_manifest_diagnostics_reject_path_escape(tmp_path):
    (tmp_path / "active.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "active_file": "../escape.sqlite3",
            "catalog_version": "must-not-escape",
            "game_version": "must-not-escape",
            "schema_version": CATALOG_SCHEMA_VERSION,
        }),
        encoding="utf-8",
    )

    assert ShipCatalogStore(tmp_path).active_manifest_info() == {
        "catalog_version": "",
        "game_version": "",
        "schema_version": None,
    }


def test_missing_manifest_degrades_to_null_snapshot(tmp_path):
    snapshot = ShipCatalogStore(tmp_path).snapshot()

    assert isinstance(snapshot, NullCatalogSnapshot)
    assert snapshot.meta is None
    assert snapshot.reason == "manifest_missing"
    assert snapshot.alias_candidates("yamato") == ()
    assert snapshot.primary_profile(4276041424) is None


def test_store_rejects_manifest_path_escape(tmp_path):
    (tmp_path / "active.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "active_file": "../escape.sqlite3",
            "sha256": "0" * 64,
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": "bad",
            "game_version": "bad",
        }),
        encoding="utf-8",
    )

    snapshot = ShipCatalogStore(tmp_path).snapshot()

    assert isinstance(snapshot, NullCatalogSnapshot)
    assert snapshot.reason == "manifest_invalid_path"


def test_store_rejects_hash_mismatch_without_raising(tmp_path):
    build_fixture_catalog(tmp_path, manifest_sha256="0" * 64)

    snapshot = ShipCatalogStore(tmp_path).snapshot()

    assert isinstance(snapshot, NullCatalogSnapshot)
    assert snapshot.reason == "catalog_hash_mismatch"


def test_store_rejects_schema_mismatch_without_migrating(tmp_path):
    build_fixture_catalog(tmp_path, schema_version=CATALOG_SCHEMA_VERSION + 1)

    snapshot = ShipCatalogStore(tmp_path).snapshot()

    assert isinstance(snapshot, NullCatalogSnapshot)
    assert snapshot.reason == "schema_unsupported"


def test_catalog_queries_do_not_change_database_bytes(tmp_path):
    db_path, _ = build_fixture_catalog(tmp_path)
    before = _sha256(db_path)
    snapshot = ShipCatalogStore(tmp_path).snapshot()
    try:
        snapshot.ship(4276041424)
        snapshot.alias_candidates("大和")
        snapshot.primary_profile(4276041424)
    finally:
        snapshot.close()

    assert _sha256(db_path) == before
