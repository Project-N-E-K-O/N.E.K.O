"""Installation-local forge-credit ledger used by N.E.K.O and its community UI."""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

DAILY_CAP = 6
TRIGGER_LIMITS = {"emotion_combo": None, "5rounds": 2, "idle": 2, "minigame": 1}
RARITY_WEIGHTS = {"UR": 0, "SSR": 1, "SR": 7, "R": 22, "N": 70}
_LOCK = threading.RLock()


def _ledger_path() -> Path:
    override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser() / "forge_credits.json"
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().memory_dir).parent / "forge_credits.json"


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
    return {"version": 1, "credits": []}


def _load() -> dict:
    path = _ledger_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("credits"), list):
        return _empty()
    return data


def _save(data: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
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
        if credit.get("status") in {"active", "reserved"} and (expires is None or expires <= now):
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


def list_credits(now: datetime | None = None) -> dict:
    current = now or _now()
    with _LOCK:
        data = _load()
        changed = _expire(data, current)
        active = [_public_credit(c) for c in data["credits"] if c.get("status") == "active"]
        reserved = [_public_credit(c) for c in data["credits"] if c.get("status") == "reserved"]
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


def reserve_credit(credit_id: str, operation_id: str, now: datetime | None = None) -> dict:
    current = now or _now()
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
        if operation_credit is not None and operation_credit.get("id") != credit_id:
            raise RuntimeError("forge_operation_conflict")
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if credit.get("status") == "reserved" and credit.get("operation_id") == operation_id:
            return {"operation_id": operation_id, "credit": _public_credit(credit)}
        if credit.get("status") != "active":
            raise RuntimeError("credit_not_active")
        credit.update({"status": "reserved", "operation_id": operation_id, "reserved_at": _iso(current)})
        _save(data)
        return {"operation_id": operation_id, "credit": _public_credit(credit)}


def commit_credit(credit_id: str, operation_id: str, card_id: str, now: datetime | None = None) -> dict:
    current = now or _now()
    try:
        uuid.UUID(card_id)
    except ValueError as exc:
        raise ValueError("invalid_card_id") from exc
    with _LOCK:
        data = _load()
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if credit.get("status") == "consumed":
            if credit.get("operation_id") == operation_id and credit.get("card_id") == card_id:
                return {"committed": True, "credit": _public_credit(credit)}
            raise RuntimeError("forge_operation_conflict")
        if credit.get("status") != "reserved" or credit.get("operation_id") != operation_id:
            raise RuntimeError("reservation_not_active")
        credit.update({"status": "consumed", "card_id": card_id, "consumed_at": _iso(current)})
        _save(data)
        return {"committed": True, "credit": _public_credit(credit)}


def release_credit(credit_id: str, operation_id: str) -> dict:
    with _LOCK:
        data = _load()
        credit = next((c for c in data["credits"] if c.get("id") == credit_id), None)
        if credit is None:
            raise LookupError("credit_not_found")
        if credit.get("status") == "active" and not credit.get("operation_id"):
            return {"released": True, "credit": _public_credit(credit)}
        if credit.get("status") != "reserved" or credit.get("operation_id") != operation_id:
            raise RuntimeError("reservation_not_active")
        for key in ("operation_id", "reserved_at"):
            credit.pop(key, None)
        credit["status"] = "active"
        _save(data)
        return {"released": True, "credit": _public_credit(credit)}
