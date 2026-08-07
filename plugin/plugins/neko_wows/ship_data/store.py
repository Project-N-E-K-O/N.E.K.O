"""Read-only, versioned SQLite storage for WoWS ship reference data."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import CatalogMeta, CatalogShip, ShipProfile

CATALOG_SCHEMA_VERSION = 1
PROFILE_SCHEMA_VERSION = 1
MANIFEST_VERSION = 1
MANIFEST_NAME = "active.json"
MAX_MANIFEST_BYTES = 64 * 1024

_LOCALIZED_SHIP_SELECT = (
    "s.ship_id, s.ship_index, s.name_key, "
    "COALESCE(("
    "SELECT localized.alias FROM ship_aliases localized "
    "WHERE localized.ship_id = s.ship_id "
    "AND localized.language = ? "
    "AND localized.alias_kind = 'localized_name' "
    "ORDER BY localized.alias_norm COLLATE BINARY, "
    "localized.alias COLLATE BINARY LIMIT 1"
    "), s.display_name) AS display_name, "
    "s.nation, s.ship_class, s.tier, s.is_premium, s.is_special, "
    "s.is_paper, s.availability_group"
)


def create_catalog_schema(conn: sqlite3.Connection) -> None:
    """Create the immutable catalog schema in a new SQLite database."""
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE catalog_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER NOT NULL,
            catalog_version TEXT NOT NULL,
            game_version TEXT NOT NULL,
            channel TEXT NOT NULL,
            source_repo TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            source_paths_json TEXT NOT NULL,
            source_sha256_json TEXT NOT NULL,
            generated_at_utc TEXT NOT NULL,
            builder_version TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            default_language TEXT NOT NULL,
            ship_count INTEGER NOT NULL CHECK (ship_count >= 0),
            profile_count INTEGER NOT NULL CHECK (profile_count >= 0)
        );

        CREATE TABLE ships (
            ship_id INTEGER PRIMARY KEY,
            ship_index TEXT NOT NULL UNIQUE,
            name_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            nation TEXT NOT NULL,
            ship_class TEXT NOT NULL,
            tier INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 20),
            is_premium INTEGER NOT NULL CHECK (is_premium IN (0, 1)),
            is_special INTEGER NOT NULL CHECK (is_special IN (0, 1)),
            is_paper INTEGER NOT NULL CHECK (is_paper IN (0, 1)),
            availability_group TEXT NOT NULL
        );

        CREATE TABLE ship_aliases (
            alias_norm TEXT NOT NULL,
            ship_id INTEGER NOT NULL REFERENCES ships(ship_id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            language TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            PRIMARY KEY (alias_norm, ship_id)
        );
        CREATE INDEX ship_aliases_lookup_idx ON ship_aliases(alias_norm);

        CREATE TABLE ship_profiles (
            profile_id TEXT PRIMARY KEY,
            ship_id INTEGER NOT NULL REFERENCES ships(ship_id) ON DELETE CASCADE,
            configuration TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
            profile_schema_version INTEGER NOT NULL,
            profile_json TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            UNIQUE (ship_id, configuration, variant_key)
        );
        CREATE UNIQUE INDEX ship_profiles_one_primary_idx
            ON ship_profiles(ship_id, configuration)
            WHERE is_primary = 1;

        CREATE TABLE module_selections (
            profile_id TEXT NOT NULL REFERENCES ship_profiles(profile_id)
                ON DELETE CASCADE,
            slot TEXT NOT NULL,
            module_key TEXT NOT NULL,
            module_index INTEGER NOT NULL,
            selection_kind TEXT NOT NULL,
            component_ids_json TEXT NOT NULL,
            PRIMARY KEY (profile_id, slot)
        );
        """
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NullCatalogSnapshot:
    """Safe empty catalog used for every unavailable or rejected database."""

    def __init__(self, reason: str = "catalog_unavailable") -> None:
        self.reason = str(reason or "catalog_unavailable")
        self.meta: None = None

    def alias_candidates(self, alias_norm: str) -> tuple[CatalogShip, ...]:
        del alias_norm
        return ()

    def ship(self, ship_id: int) -> CatalogShip | None:
        del ship_id
        return None

    def primary_profile(self, ship_id: int) -> ShipProfile | None:
        del ship_id
        return None

    def close(self) -> None:
        return None


class SQLiteCatalogSnapshot:
    """One immutable connection that may be pinned for a whole battle."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        meta: CatalogMeta,
        *,
        language: str | None = None,
    ) -> None:
        self._conn = conn
        self._lock = threading.RLock()
        self._closed = False
        self._language = language
        self.meta = meta

    @staticmethod
    def _ship_from_row(row: sqlite3.Row | None) -> CatalogShip | None:
        if row is None:
            return None
        return CatalogShip(
            ship_id=int(row["ship_id"]),
            ship_index=str(row["ship_index"]),
            name_key=str(row["name_key"]),
            display_name=str(row["display_name"]),
            nation=str(row["nation"]),
            ship_class=str(row["ship_class"]),
            tier=int(row["tier"]),
            is_premium=bool(row["is_premium"]),
            is_special=bool(row["is_special"]),
            is_paper=bool(row["is_paper"]),
            availability_group=str(row["availability_group"]),
        )

    def alias_candidates(self, alias_norm: str) -> tuple[CatalogShip, ...]:
        if not alias_norm:
            return ()
        with self._lock:
            if self._closed:
                return ()
            rows = self._conn.execute(
                f"SELECT {_LOCALIZED_SHIP_SELECT} "
                "FROM ship_aliases lookup "
                "JOIN ships s ON s.ship_id = lookup.ship_id "
                "WHERE lookup.alias_norm = ? ORDER BY s.ship_id",
                (self._language, alias_norm),
            ).fetchall()
        return tuple(
            ship for row in rows
            if (ship := self._ship_from_row(row)) is not None
        )

    def ship(self, ship_id: int) -> CatalogShip | None:
        with self._lock:
            if self._closed:
                return None
            row = self._conn.execute(
                f"SELECT {_LOCALIZED_SHIP_SELECT} FROM ships s "
                "WHERE s.ship_id = ?",
                (self._language, int(ship_id)),
            ).fetchone()
        return self._ship_from_row(row)

    def primary_profile(self, ship_id: int) -> ShipProfile | None:
        with self._lock:
            if self._closed:
                return None
            row = self._conn.execute(
                "SELECT * FROM ship_profiles "
                "WHERE ship_id = ? AND configuration = 'reference_top' "
                "AND is_primary = 1",
                (int(ship_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(str(row["profile_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return ShipProfile(
            profile_id=str(row["profile_id"]),
            ship_id=int(row["ship_id"]),
            configuration=str(row["configuration"]),
            variant_key=str(row["variant_key"]),
            is_primary=bool(row["is_primary"]),
            profile_schema_version=int(row["profile_schema_version"]),
            data=data,
            profile_sha256=str(row["profile_sha256"]),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()


class ShipCatalogStore:
    """Validate the active manifest and create battle-pinnable snapshots."""

    def __init__(self, root: str | Path, *, logger: Any = None) -> None:
        self.root = Path(root)
        self.logger = logger
        self._last_failure = ""

    def active_manifest_info(self) -> dict[str, str | int | None]:
        """Return bounded, non-sensitive diagnostics from the active manifest."""
        empty: dict[str, str | int | None] = {
            "catalog_version": "",
            "game_version": "",
            "schema_version": None,
        }
        manifest_path = self.root / MANIFEST_NAME
        try:
            if not manifest_path.is_file():
                return empty
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                return empty
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return empty
            if manifest.get("manifest_version") != MANIFEST_VERSION:
                return empty
            name = manifest.get("active_file")
            if not self._safe_filename(name):
                return empty
            db_path = (self.root / str(name)).resolve()
            if db_path.parent != self.root.resolve():
                return empty

            catalog_version = manifest.get("catalog_version")
            game_version = manifest.get("game_version")
            schema_version = manifest.get("schema_version")
            if (
                not isinstance(catalog_version, str)
                or not catalog_version
                or not isinstance(game_version, str)
                or not game_version
                or type(schema_version) is not int
            ):
                return empty
            return {
                "catalog_version": catalog_version,
                "game_version": game_version,
                "schema_version": schema_version,
            }
        except (OSError, ValueError, TypeError):
            return empty

    def snapshot(
        self,
        language: str | None = None,
    ) -> SQLiteCatalogSnapshot | NullCatalogSnapshot:
        manifest_path = self.root / MANIFEST_NAME
        if not manifest_path.is_file():
            return self._null("manifest_missing")
        try:
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                return self._null("manifest_too_large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return self._null("manifest_invalid")
            if manifest.get("manifest_version") != MANIFEST_VERSION:
                return self._null("manifest_unsupported")
            name = manifest.get("active_file")
            if not self._safe_filename(name):
                return self._null("manifest_invalid_path")
            db_path = (self.root / str(name)).resolve()
            if db_path.parent != self.root.resolve():
                return self._null("manifest_invalid_path")
            if not db_path.is_file():
                return self._null("catalog_missing")
            expected_hash = manifest.get("sha256")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                return self._null("manifest_invalid_hash")
            if not hmac.compare_digest(file_sha256(db_path), expected_hash.lower()):
                return self._null("catalog_hash_mismatch")

            conn = sqlite3.connect(
                db_path.as_uri() + "?mode=ro&immutable=1",
                uri=True,
                check_same_thread=False,
            )
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                check = conn.execute("PRAGMA quick_check").fetchone()
                if check is None or str(check[0]).lower() != "ok":
                    conn.close()
                    return self._null("catalog_integrity_failed")
                row = conn.execute("SELECT * FROM catalog_meta WHERE id = 1").fetchone()
                if row is None:
                    conn.close()
                    return self._null("catalog_meta_missing")
                schema_version = int(row["schema_version"])
                if (
                    schema_version != CATALOG_SCHEMA_VERSION
                    or manifest.get("schema_version") != CATALOG_SCHEMA_VERSION
                ):
                    conn.close()
                    return self._null("schema_unsupported")
                if (
                    row["catalog_version"] != manifest.get("catalog_version")
                    or row["game_version"] != manifest.get("game_version")
                ):
                    conn.close()
                    return self._null("manifest_catalog_mismatch")
                meta = CatalogMeta(
                    schema_version=schema_version,
                    catalog_version=str(row["catalog_version"]),
                    game_version=str(row["game_version"]),
                    channel=str(row["channel"]),
                    source_repo=str(row["source_repo"]),
                    source_commit=str(row["source_commit"]),
                    content_sha256=str(row["content_sha256"]),
                    default_language=str(row["default_language"]),
                    ship_count=int(row["ship_count"]),
                    profile_count=int(row["profile_count"]),
                )
                self._last_failure = ""
                return SQLiteCatalogSnapshot(conn, meta, language=language)
            except Exception:
                conn.close()
                raise
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._null("catalog_unavailable", exc)

    @staticmethod
    def _safe_filename(value: Any) -> bool:
        if not isinstance(value, str) or not value or len(value) > 240:
            return False
        if "/" in value or "\\" in value or value in (".", ".."):
            return False
        path = Path(value)
        return not path.is_absolute() and path.name == value and path.suffix == ".sqlite3"

    def _null(self, reason: str, exc: Exception | None = None) -> NullCatalogSnapshot:
        signature = f"{reason}:{type(exc).__name__ if exc else ''}"
        if signature != self._last_failure:
            self._last_failure = signature
            warning = getattr(self.logger, "warning", None)
            if callable(warning):
                detail = f": {type(exc).__name__}" if exc else ""
                try:
                    warning(f"ship catalog unavailable ({reason}){detail}")
                except Exception:
                    pass
        return NullCatalogSnapshot(reason)


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "MANIFEST_NAME",
    "MANIFEST_VERSION",
    "NullCatalogSnapshot",
    "PROFILE_SCHEMA_VERSION",
    "SQLiteCatalogSnapshot",
    "ShipCatalogStore",
    "create_catalog_schema",
    "file_sha256",
]
