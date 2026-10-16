"""Local account-to-role binding and stage-2 pet snapshot service.

The service is a local mirror of the future community PetCare binding
contract.  OAuth supplies the account; this service owns the single local pet
record and the server-generated stable ``bound_role_id``.  A local
``character_uid`` is only a resolver key and is never used as a pet or role
primary key.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class BindingServiceError(RuntimeError):
    """A local binding operation could not be completed."""

    def __init__(self, code: str, *, status_code: int = 503):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class BindingContext:
    account_id: str
    current_character_uid: str
    binding_status: str
    pet_id: str | None
    bound_role_id: str | None
    created: bool = False
    snapshot: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "current_character_uid": self.current_character_uid,
            "binding_status": self.binding_status,
            "pet_id": self.pet_id,
            "bound_role_id": self.bound_role_id,
            "created": self.created,
            "service": "local",
            "snapshot": deepcopy(self.snapshot) if self.snapshot is not None else None,
        }


# This is a configuration boundary, not a second product rule source.  The
# values are deliberately replaceable by the local/test configuration and are
# only used to initialize a new snapshot.  Stage 2 does not calculate changes
# over time and does not settle any action.
DEFAULT_PET_CARE_SNAPSHOT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "initial_status_values": {
        "satiety": 75,
        "energy": 80,
        "cleanliness": 80,
        "mood": 64,
        "progress": 0,
    },
    "initial_negative_status": {
        "sick": {"active": False},
        "sulk": {"active": False},
    },
    "initial_inventory": {},
    "initial_tasks": {"daily": [], "state": []},
    "initial_unlocks": [],
    "initial_exploration": None,
}


class LocalPetCareBindingService:
    """Persist one account pet and one explicitly bound local role.

    The ``bound_character_uid`` column is deliberately a local resolver field.
    It is not returned as a role identity and is not used as ``pet_id`` or
    ``bound_role_id``.  The community implementation can replace this adapter
    with its role resolver while keeping the response contract unchanged.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_accounts (
        account_id TEXT PRIMARY KEY,
        pet_id TEXT NOT NULL UNIQUE,
        bound_role_id TEXT NOT NULL UNIQUE,
        bound_character_uid TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    CREATE_SNAPSHOT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_snapshots (
        pet_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL UNIQUE,
        bound_role_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        last_settled_at TEXT NOT NULL,
        status_values_json TEXT NOT NULL,
        negative_status_json TEXT NOT NULL,
        inventory_json TEXT NOT NULL,
        tasks_json TEXT NOT NULL,
        unlocks_json TEXT NOT NULL,
        exploration_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    CREATE_OPERATION_RESULTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_operation_results (
        account_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, idempotency_key)
    )
    """

    def __init__(self, config_manager: Any, snapshot_config: Mapping[str, Any] | None = None):
        self.config_manager = config_manager
        configured = snapshot_config
        if configured is None:
            configured = getattr(config_manager, "pet_care_snapshot_config", None)
        if configured is None:
            # The production ConfigManager owns user-editable JSON config. Do
            # not make the service silently depend on a private attribute that
            # most adapters do not expose; the explicit constructor override
            # remains available to tests and future server adapters.
            loader = getattr(config_manager, "load_json_config", None)
            if callable(loader):
                try:
                    configured = loader("pet_care.json", default_value={})
                except TypeError:
                    configured = loader("pet_care.json")
                except Exception as exc:
                    raise BindingServiceError("pet_care_config_unavailable") from exc
        if not isinstance(configured, Mapping):
            configured = {}
        self.snapshot_config = self._merge_config(DEFAULT_PET_CARE_SNAPSHOT_CONFIG, configured)
        self._validate_snapshot_config(self.snapshot_config)

    @staticmethod
    def _merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = deepcopy(dict(base))
        for key, value in override.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = LocalPetCareBindingService._merge_config(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    @staticmethod
    def _validate_snapshot_config(config: Mapping[str, Any]) -> None:
        """Reject malformed editable config before it can create a snapshot."""
        schema_version = config.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            raise BindingServiceError("pet_care_config_invalid")

        values = config.get("initial_status_values")
        required_values = ("satiety", "energy", "cleanliness", "mood", "progress")
        if not isinstance(values, Mapping) or any(key not in values for key in required_values):
            raise BindingServiceError("pet_care_config_invalid")
        for key in required_values:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise BindingServiceError("pet_care_config_invalid")
            if value < 0 or value > 100:
                raise BindingServiceError("pet_care_config_invalid")

        if not isinstance(config.get("initial_negative_status"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_inventory"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_tasks"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_unlocks"), list):
            raise BindingServiceError("pet_care_config_invalid")
        exploration = config.get("initial_exploration")
        if exploration is not None and not isinstance(exploration, Mapping):
            raise BindingServiceError("pet_care_config_invalid")

    @property
    def database_path(self) -> Path:
        state_dir = Path(self.config_manager.local_state_dir)
        return state_dir / "pet_care" / "pet_care.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        try:
            if not self.config_manager.ensure_local_state_directory():
                raise BindingServiceError("local_state_unavailable")
            path = self.database_path
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(path), timeout=5.0, isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute(self._SCHEMA)
            connection.execute(self.CREATE_SNAPSHOT_SCHEMA)
            connection.execute(self.CREATE_OPERATION_RESULTS_SCHEMA)
            self._migrate_existing_accounts(connection)
            return connection
        except BindingServiceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise BindingServiceError("pet_care_storage_unavailable") from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _from_json(value: str | None, fallback: Any) -> Any:
        if not value:
            return deepcopy(fallback)
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return deepcopy(fallback)

    def _initial_snapshot(self, row: sqlite3.Row, now: str | None = None) -> dict[str, Any]:
        now = now or self._now()
        return {
            "schema_version": int(self.snapshot_config.get("schema_version", 1)),
            "pet_id": str(row["pet_id"]),
            "account_id": str(row["account_id"]),
            "bound_role_id": str(row["bound_role_id"]),
            "last_settled_at": now,
            "status_values": deepcopy(self.snapshot_config["initial_status_values"]),
            "negative_status": deepcopy(self.snapshot_config["initial_negative_status"]),
            "inventory": deepcopy(self.snapshot_config["initial_inventory"]),
            "tasks": deepcopy(self.snapshot_config["initial_tasks"]),
            "unlocks": deepcopy(self.snapshot_config["initial_unlocks"]),
            "exploration": deepcopy(self.snapshot_config["initial_exploration"]),
        }

    def _insert_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row, now: str | None = None) -> None:
        snapshot = self._initial_snapshot(row, now)
        timestamp = now or self._now()
        connection.execute(
            """
            INSERT OR IGNORE INTO pet_care_snapshots
              (pet_id, account_id, bound_role_id, schema_version, last_settled_at,
               status_values_json, negative_status_json, inventory_json, tasks_json,
               unlocks_json, exploration_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["pet_id"], snapshot["account_id"], snapshot["bound_role_id"],
                snapshot["schema_version"], snapshot["last_settled_at"],
                self._json(snapshot["status_values"]), self._json(snapshot["negative_status"]),
                self._json(snapshot["inventory"]), self._json(snapshot["tasks"]),
                self._json(snapshot["unlocks"]), self._json(snapshot["exploration"]),
                timestamp, timestamp,
            ),
        )

    def _migrate_existing_accounts(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT * FROM pet_care_accounts").fetchall()
        for row in rows:
            self._insert_snapshot(connection, row)

    def _ensure_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        exists = connection.execute(
            "SELECT 1 FROM pet_care_snapshots WHERE pet_id = ?", (row["pet_id"],)
        ).fetchone()
        if exists is None:
            self._insert_snapshot(connection, row)

    def _read_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
        snapshot_row = connection.execute(
            "SELECT * FROM pet_care_snapshots WHERE pet_id = ?", (row["pet_id"],)
        ).fetchone()
        if snapshot_row is None:
            return None
        return {
            "schema_version": int(snapshot_row["schema_version"]),
            "pet_id": str(snapshot_row["pet_id"]),
            "account_id": str(snapshot_row["account_id"]),
            "bound_role_id": str(snapshot_row["bound_role_id"]),
            "last_settled_at": str(snapshot_row["last_settled_at"]),
            "status_values": self._from_json(snapshot_row["status_values_json"], {}),
            "negative_status": self._from_json(snapshot_row["negative_status_json"], {}),
            "inventory": self._from_json(snapshot_row["inventory_json"], {}),
            "tasks": self._from_json(snapshot_row["tasks_json"], {}),
            "unlocks": self._from_json(snapshot_row["unlocks_json"], []),
            "exploration": self._from_json(snapshot_row["exploration_json"], None),
        }

    @staticmethod
    def _context_from_row(
        row: sqlite3.Row | None,
        account_id: str,
        character_uid: str,
        *,
        created: bool = False,
        snapshot: dict[str, Any] | None = None,
    ) -> BindingContext:
        if row is None:
            return BindingContext(
                account_id=account_id,
                current_character_uid=character_uid,
                binding_status="needs-binding",
                pet_id=None,
                bound_role_id=None,
                created=created,
                snapshot=None,
            )
        is_bound = str(row["bound_character_uid"]) == character_uid
        return BindingContext(
            account_id=account_id,
            current_character_uid=character_uid,
            binding_status="bound" if is_bound else "unbound",
            pet_id=str(row["pet_id"]) if is_bound else None,
            bound_role_id=str(row["bound_role_id"]) if is_bound else None,
            created=created,
            snapshot=deepcopy(snapshot) if is_bound else None,
        )

    def resolve(self, account_id: str, character_uid: str) -> BindingContext:
        """Read context, creating the account's first pet/binding atomically.

        The first authenticated role is the account's initial binding.  This
        keeps the local stage-1 flow from dead-ending at a client-only
        confirmation screen while preserving one account -> one pet.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            created = row is None
            if row is None:
                now = self._now()
                connection.execute(
                    """
                    INSERT INTO pet_care_accounts
                      (account_id, pet_id, bound_role_id, bound_character_uid,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        self._new_id(),
                        self._new_id(),
                        character_uid,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            result = self._context_from_row(
                row, account_id, character_uid, created=created, snapshot=snapshot,
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def bind(self, account_id: str, character_uid: str) -> BindingContext:
        """Explicitly bind the account's existing pet to this role.

        Rebinding updates the same pet row and creates a fresh server role id;
        it never inserts a second account pet. Repeating the same request is
        idempotent and returns the existing role id.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            now = self._now()
            created = row is None
            if row is None:
                connection.execute(
                    """
                    INSERT INTO pet_care_accounts
                      (account_id, pet_id, bound_role_id, bound_character_uid,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        self._new_id(),
                        self._new_id(),
                        character_uid,
                        now,
                        now,
                    ),
                )
            elif str(row["bound_character_uid"]) != character_uid:
                new_role_id = self._new_id()
                connection.execute(
                    """
                    UPDATE pet_care_accounts
                    SET bound_role_id = ?, bound_character_uid = ?, updated_at = ?
                    WHERE account_id = ?
                    """,
                    (new_role_id, character_uid, now, account_id),
                )
                connection.execute(
                    """
                    UPDATE pet_care_snapshots
                    SET bound_role_id = ?, updated_at = ?
                    WHERE pet_id = ?
                    """,
                    (new_role_id, now, row["pet_id"]),
                )
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            result = self._context_from_row(
                row, account_id, character_uid, created=created, snapshot=snapshot,
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()
