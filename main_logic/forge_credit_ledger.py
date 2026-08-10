"""Installation-local forge-credit ledger used by N.E.K.O and its community UI."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import random
import secrets
import threading
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

DAILY_CAP = 6
TRIGGER_LIMITS = {"emotion_combo": None, "5rounds": 2, "idle": 2, "minigame": 1}
RARITY_WEIGHTS = {"UR": 0, "SSR": 0.5, "SR": 3.5, "R": 26, "N": 70}
LEDGER_VERSION = 2
_INTEGRITY_ALGORITHM = "hmac-sha256"
_SIGNING_KEY_BYTES = 32
_LOCK = threading.RLock()


class LedgerIntegrityError(RuntimeError):
    """The persisted credit ledger cannot be authenticated safely."""


def _ledger_path() -> Path:
    override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser() / "forge_credits.json"
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().memory_dir).parent / "forge_credits.json"


def _signing_key_path() -> Path:
    return _ledger_path().with_name("forge_credits.key")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _empty() -> dict:
    return {"version": LEDGER_VERSION, "credits": []}


def _read_or_create_signing_key() -> tuple[bytes, bool]:
    path = _signing_key_path()
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(_SIGNING_KEY_BYTES)
        try:
            with path.open("xb") as handle:
                handle.write(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return key, True
        except FileExistsError:
            key = path.read_bytes()
    except OSError as exc:
        raise LedgerIntegrityError("ledger_signing_key_unavailable") from exc
    if len(key) != _SIGNING_KEY_BYTES:
        raise LedgerIntegrityError("ledger_signing_key_invalid")
    return key, False


def _canonical_ledger(data: dict) -> bytes:
    payload = {
        "credits": data.get("credits"),
        "version": LEDGER_VERSION,
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _ledger_mac(data: dict, key: bytes) -> str:
    return hmac.new(key, _canonical_ledger(data), hashlib.sha256).hexdigest()


def _valid_uuid(value: object) -> bool:
    try:
        return str(uuid.UUID(str(value))) == str(value).strip().lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _validate_ledger(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("credits"), list):
        raise LedgerIntegrityError("ledger_schema_invalid")
    seen_ids: set[str] = set()
    seen_idem_keys: set[str] = set()
    for credit in data["credits"]:
        if not isinstance(credit, dict):
            raise LedgerIntegrityError("ledger_credit_schema_invalid")
        credit_id = credit.get("id")
        idem_key = credit.get("idem_key")
        if not _valid_uuid(credit_id) or credit_id in seen_ids:
            raise LedgerIntegrityError("ledger_credit_id_invalid")
        if not isinstance(idem_key, str) or not 8 <= len(idem_key) <= 128:
            raise LedgerIntegrityError("ledger_credit_idem_invalid")
        if idem_key in seen_idem_keys:
            raise LedgerIntegrityError("ledger_credit_idem_duplicate")
        if credit.get("rarity") not in RARITY_WEIGHTS:
            raise LedgerIntegrityError("ledger_credit_rarity_invalid")
        if credit.get("trigger_type") not in TRIGGER_LIMITS:
            raise LedgerIntegrityError("ledger_credit_trigger_invalid")
        if credit.get("status") not in {"active", "reserved", "consumed", "expired"}:
            raise LedgerIntegrityError("ledger_credit_status_invalid")
        if _parse(credit.get("created_at")) is None or _parse(credit.get("expires_at")) is None:
            raise LedgerIntegrityError("ledger_credit_timestamp_invalid")
        for field in ("operation_id", "reservation_owner_id", "card_id"):
            if field in credit and not _valid_uuid(credit.get(field)):
                raise LedgerIntegrityError(f"ledger_credit_{field}_invalid")
        for field in ("reserved_at", "consumed_at", "expired_at"):
            if field in credit and _parse(credit.get(field)) is None:
                raise LedgerIntegrityError(f"ledger_credit_{field}_invalid")
        seen_ids.add(credit_id)
        seen_idem_keys.add(idem_key)


def _load() -> dict:
    path = _ledger_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if _signing_key_path().exists():
            raise LedgerIntegrityError("ledger_missing")
        return _empty()
    except (OSError, ValueError, TypeError) as exc:
        raise LedgerIntegrityError("ledger_unreadable") from exc

    key, key_created = _read_or_create_signing_key()
    version = data.get("version") if isinstance(data, dict) else None
    if version == 1 and key_created:
        # One-time upgrade path. Once the key exists, an unsigned/version-1
        # ledger is a downgrade attempt and must fail closed.
        migrated = {"version": LEDGER_VERSION, "credits": data.get("credits")}
        _validate_ledger(migrated)
        _save(migrated, signing_key=key)
        return migrated
    if version != LEDGER_VERSION:
        raise LedgerIntegrityError("ledger_version_invalid")
    supplied = data.get("integrity")
    if not isinstance(supplied, dict) or supplied.get("algorithm") != _INTEGRITY_ALGORITHM:
        raise LedgerIntegrityError("ledger_integrity_missing")
    digest = supplied.get("digest")
    expected = _ledger_mac(data, key)
    if not isinstance(digest, str) or not hmac.compare_digest(digest, expected):
        raise LedgerIntegrityError("ledger_integrity_failed")
    _validate_ledger(data)
    return data


def _save(data: dict, *, signing_key: bytes | None = None) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = LEDGER_VERSION
    _validate_ledger(data)
    key = signing_key or _read_or_create_signing_key()[0]
    data["integrity"] = {
        "algorithm": _INTEGRITY_ALGORITHM,
        "digest": _ledger_mac(data, key),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _expire(data: dict, now: datetime) -> bool:
    changed = False
    for credit in data["credits"]:
        expires = _parse(credit.get("expires_at"))
        if credit.get("status") == "active" and (expires is None or expires <= now):
            credit["status"] = "expired"
            credit["expired_at"] = _iso(now)
            changed = True
    return changed


def _public_credit(credit: dict) -> dict:
    return {
        key: credit.get(key)
        for key in (
            "id", "rarity", "lanlan_name", "trigger_type", "status",
            "created_at", "expires_at", "operation_id", "reserved_at",
            "consumed_at", "card_id",
        )
        if credit.get(key) is not None
    }


def _normalize_owner_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        return ""


def _require_owner_id(value: object) -> str:
    owner_id = _normalize_owner_id(value)
    if not owner_id:
        raise ValueError("invalid_reservation_owner_id")
    return owner_id


def _require_reservation_owner(credit: dict, owner_id: str) -> None:
    reservation_owner_id = _normalize_owner_id(
        credit.get("reservation_owner_id")
    )
    if not reservation_owner_id or reservation_owner_id != owner_id:
        # Pre-owner-schema reservations deliberately fail closed.  A current
        # account must never be able to adopt or release an unbound reservation.
        raise RuntimeError("reservation_owner_mismatch")


def list_credits(
    now: datetime | None = None,
    *,
    reservation_owner_id: str | None = None,
) -> dict:
    current = now or _now()
    owner_id = _normalize_owner_id(reservation_owner_id)
    with _LOCK:
        data = _load()
        changed = _expire(data, current)
        active = [_public_credit(c) for c in data["credits"] if c.get("status") == "active"]
        reserved = [
            _public_credit(c)
            for c in data["credits"]
            if c.get("status") == "reserved"
            and owner_id
            and _normalize_owner_id(c.get("reservation_owner_id")) == owner_id
        ]
        if changed:
            _save(data)
        active.sort(key=lambda item: item.get("created_at") or "")
        reserved.sort(key=lambda item: item.get("reserved_at") or "")
        return {"count": len(active), "credits": active, "reservations": reserved}


def grant_credit(payload: dict, now: datetime | None = None, rarity: str | None = None) -> dict:
    current = now or _now()
    trigger = str(payload.get("trigger_type") or "")
    idem_key = str(payload.get("idem_key") or "")
    if trigger not in TRIGGER_LIMITS:
        raise ValueError("invalid_trigger_type")
    if not 8 <= len(idem_key) <= 128:
        raise ValueError("invalid_idem_key")
    with _LOCK:
        data = _load()
        _expire(data, current)
        existing = next((c for c in data["credits"] if c.get("idem_key") == idem_key), None)
        if existing is not None:
            snapshot = list_credits(current)
            return {
                "granted": True,
                "reason": "duplicate",
                "rarity": existing.get("rarity"),
                "expires_at": existing.get("expires_at"),
                "available": max(0, DAILY_CAP - _granted_today(data, current)),
                "active_count": snapshot["count"],
            }
        granted_today = _granted_today(data, current)
        if granted_today >= DAILY_CAP:
            _save(data)
            return {"granted": False, "reason": "daily_cap", "available": 0, "active_count": list_credits(current)["count"]}
        trigger_count = _granted_today(data, current, trigger)
        trigger_limit = TRIGGER_LIMITS[trigger]
        if trigger_limit is not None and trigger_count >= trigger_limit:
            _save(data)
            return {
                "granted": False,
                "reason": "trigger_daily_cap",
                "available": DAILY_CAP - granted_today,
                "active_count": list_credits(current)["count"],
            }
        selected = rarity or random.choices(
            list(RARITY_WEIGHTS), weights=list(RARITY_WEIGHTS.values()), k=1
        )[0]
        if selected not in RARITY_WEIGHTS:
            raise ValueError("invalid_rarity")
        tomorrow = current.astimezone(UTC).date() + timedelta(days=1)
        expires_at = datetime.combine(tomorrow, time.min, tzinfo=UTC)
        credit = {
            "id": str(uuid.uuid4()),
            "rarity": selected,
            "lanlan_name": payload.get("lanlan_name"),
            "trigger_type": trigger,
            "idem_key": idem_key,
            "status": "active",
            "created_at": _iso(current),
            "expires_at": _iso(expires_at),
        }
        data["credits"].append(credit)
        _save(data)
        return {
            "granted": True,
            "reason": "ok",
            "rarity": selected,
            "expires_at": credit["expires_at"],
            "available": DAILY_CAP - granted_today - 1,
            "active_count": list_credits(current)["count"],
        }


def _granted_today(data: dict, now: datetime, trigger: str | None = None) -> int:
    day = now.astimezone(UTC).date()
    return sum(
        1
        for credit in data["credits"]
        if (_parse(credit.get("created_at")) or datetime.min.replace(tzinfo=UTC)).date() == day
        and (trigger is None or credit.get("trigger_type") == trigger)
    )


def reserve_credit(
    credit_id: str,
    operation_id: str,
    reservation_owner_id: str,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    owner_id = _require_owner_id(reservation_owner_id)
    try:
        uuid.UUID(credit_id)
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise ValueError("invalid_credit_or_operation_id") from exc
    with _LOCK:
        data = _load()
        _expire(data, current)
        operation_credit = next(
            (c for c in data["credits"] if c.get("operation_id") == operation_id), None
        )
        if operation_credit is not None:
            _require_reservation_owner(operation_credit, owner_id)
            if operation_credit.get("id") != credit_id:
                raise RuntimeError("forge_operation_conflict")
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if credit.get("status") == "reserved" and credit.get("operation_id") == operation_id:
            _require_reservation_owner(credit, owner_id)
            return {"operation_id": operation_id, "credit": _public_credit(credit)}
        if credit.get("status") != "active":
            raise RuntimeError("credit_not_active")
        for key in ("last_released_operation_id", "last_released_owner_id"):
            credit.pop(key, None)
        credit.update(
            {
                "status": "reserved",
                "operation_id": operation_id,
                "reservation_owner_id": owner_id,
                "reserved_at": _iso(current),
            }
        )
        _save(data)
        return {"operation_id": operation_id, "credit": _public_credit(credit)}


def commit_credit(
    credit_id: str,
    operation_id: str,
    card_id: str,
    reservation_owner_id: str,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    owner_id = _require_owner_id(reservation_owner_id)
    try:
        uuid.UUID(card_id)
    except ValueError as exc:
        raise ValueError("invalid_card_id") from exc
    with _LOCK:
        data = _load()
        if _expire(data, current):
            _save(data)
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if credit.get("status") == "consumed":
            _require_reservation_owner(credit, owner_id)
            if credit.get("operation_id") == operation_id and credit.get("card_id") == card_id:
                return {"committed": True, "credit": _public_credit(credit)}
            raise RuntimeError("forge_operation_conflict")
        if credit.get("status") != "reserved" or credit.get("operation_id") != operation_id:
            raise RuntimeError("reservation_not_active")
        _require_reservation_owner(credit, owner_id)
        credit.update({"status": "consumed", "card_id": card_id, "consumed_at": _iso(current)})
        _save(data)
        return {"committed": True, "credit": _public_credit(credit)}


def release_credit(
    credit_id: str,
    operation_id: str,
    reservation_owner_id: str,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    owner_id = _require_owner_id(reservation_owner_id)
    with _LOCK:
        data = _load()
        if _expire(data, current):
            _save(data)
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if (
            credit.get("status") in {"active", "expired"}
            and not credit.get("operation_id")
        ):
            if credit.get("last_released_operation_id") != operation_id:
                raise RuntimeError("reservation_not_active")
            if _normalize_owner_id(credit.get("last_released_owner_id")) != owner_id:
                raise RuntimeError("reservation_owner_mismatch")
            return {"released": True, "credit": _public_credit(credit)}
        if credit.get("status") != "reserved" or credit.get("operation_id") != operation_id:
            raise RuntimeError("reservation_not_active")
        _require_reservation_owner(credit, owner_id)
        credit.update(
            {
                "last_released_operation_id": operation_id,
                "last_released_owner_id": owner_id,
            }
        )
        for key in ("operation_id", "reservation_owner_id", "reserved_at"):
            credit.pop(key, None)
        expires = _parse(credit.get("expires_at"))
        if expires is None or expires <= current:
            credit.update({"status": "expired", "expired_at": _iso(current)})
        else:
            credit["status"] = "active"
        _save(data)
        return {"released": True, "credit": _public_credit(credit)}
