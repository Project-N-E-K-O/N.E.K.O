"""Crash-safe local integration outbox for PetCare role-alias retirement.

This file stores only account/character routing evidence for a committed local
character deletion.  It is not a PetCare state mirror: pet state, inventory,
tasks and rewards remain exclusively on the authority service.
"""

from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main_routers import card_drop_router as card_drop
from main_routers.shared_state import get_config_manager
from utils.config_manager import get_character_uid, is_valid_character_uid
from utils.file_utils import atomic_write_json


router = APIRouter(
    prefix="/api/local-integrations/pet-care",
    tags=["local-integrations"],
)

_VERSION = 1
_FILE_NAME = "pet_care_role_alias_outbox.json"
_MAX_ACCOUNTS = 16
_MAX_EVENTS = 256
_MAX_OWNER_RECORDS = 256
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_lock = threading.RLock()
_active_preparations: dict[str, int] = {}


class PetCareRoleAliasOutboxError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_id(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 128 or _CONTROL_RE.search(candidate):
        raise PetCareRoleAliasOutboxError("invalid account id")
    return candidate


def _retirement(character_uid: object) -> dict[str, str]:
    uid = str(character_uid or "").strip()
    if not is_valid_character_uid(uid):
        raise PetCareRoleAliasOutboxError("invalid character uid")
    return {
        "character_uid": uid,
        "source_revision": f"delete:{uid}",
        "status": "retired",
    }


def _path(config_manager) -> Path:
    try:
        return Path(config_manager.local_state_dir) / _FILE_NAME
    except Exception as exc:
        raise PetCareRoleAliasOutboxError("local state path unavailable") from exc


def _empty() -> dict[str, Any]:
    return {"version": _VERSION, "owners": {}, "events": {}}


def _normalize(raw: object) -> dict[str, Any]:
    if raw in (None, {}):
        return _empty()
    if not isinstance(raw, dict) or raw.get("version") != _VERSION:
        raise PetCareRoleAliasOutboxError("unsupported outbox schema")
    owners_raw = raw.get("owners")
    events_raw = raw.get("events")
    if not isinstance(owners_raw, dict) or not isinstance(events_raw, dict):
        raise PetCareRoleAliasOutboxError("corrupt outbox")
    if len(owners_raw) > _MAX_OWNER_RECORDS:
        raise PetCareRoleAliasOutboxError("owner registry capacity exceeded")
    if len(events_raw) > _MAX_EVENTS:
        raise PetCareRoleAliasOutboxError("outbox capacity exceeded")

    owners: dict[str, list[str]] = {}
    for uid, rows in owners_raw.items():
        if not is_valid_character_uid(uid) or not isinstance(rows, list):
            raise PetCareRoleAliasOutboxError("corrupt owner registry")
        normalized_rows: list[str] = []
        for row in rows:
            account = _account_id(row)
            if account not in normalized_rows:
                normalized_rows.append(account)
        if len(normalized_rows) > _MAX_ACCOUNTS:
            raise PetCareRoleAliasOutboxError("owner capacity exceeded")
        if normalized_rows:
            owners[uid] = normalized_rows

    events: dict[str, dict[str, Any]] = {}
    for revision, value in events_raw.items():
        if not isinstance(value, dict):
            raise PetCareRoleAliasOutboxError("corrupt retirement event")
        event = _retirement(value.get("character_uid"))
        if revision != event["source_revision"] or value.get("status") != "retired":
            raise PetCareRoleAliasOutboxError("corrupt retirement identity")
        phase = value.get("phase")
        if phase not in {"prepared", "ready"}:
            raise PetCareRoleAliasOutboxError("corrupt retirement phase")
        raw_event_owners = value.get("owners")
        if not isinstance(raw_event_owners, list):
            raise PetCareRoleAliasOutboxError("corrupt event owners")
        event_owners: list[str] = []
        for row in raw_event_owners:
            account = _account_id(row)
            if account not in event_owners:
                event_owners.append(account)
        if len(event_owners) > _MAX_ACCOUNTS:
            raise PetCareRoleAliasOutboxError("event owner capacity exceeded")
        events[revision] = {
            **event,
            "phase": phase,
            "owners": event_owners,
            "created_at": str(value.get("created_at") or ""),
        }
    return {"version": _VERSION, "owners": owners, "events": events}


def _read(config_manager) -> dict[str, Any]:
    try:
        path = _path(config_manager)
        if not path.exists():
            return _empty()
        import json

        with path.open("r", encoding="utf-8") as handle:
            return _normalize(json.load(handle))
    except PetCareRoleAliasOutboxError:
        raise
    except Exception as exc:
        raise PetCareRoleAliasOutboxError("cannot read role-alias outbox") from exc


def _write(config_manager, state: dict[str, Any]) -> None:
    try:
        if not config_manager.ensure_local_state_directory():
            raise PetCareRoleAliasOutboxError("local state directory unavailable")
        atomic_write_json(_path(config_manager), state, ensure_ascii=False, indent=2)
    except PetCareRoleAliasOutboxError:
        raise
    except Exception as exc:
        raise PetCareRoleAliasOutboxError("cannot write role-alias outbox") from exc


def saved_account_id() -> str:
    """Return the persisted unified-account id without treating it as authority."""

    try:
        snapshot = card_drop._desktop_session_snapshot() or {}
        return _account_id(snapshot.get("local_user_id"))
    except (OSError, ValueError, TypeError, PetCareRoleAliasOutboxError):
        return ""


def remember_owner(config_manager, character_uid: str, account_id: str) -> None:
    event = _retirement(character_uid)
    account = _account_id(account_id)
    with _lock:
        state = _read(config_manager)
        if (event["character_uid"] not in state["owners"] and
                len(state["owners"]) >= _MAX_OWNER_RECORDS):
            raise PetCareRoleAliasOutboxError("owner registry capacity exceeded")
        owners = state["owners"].setdefault(event["character_uid"], [])
        if account not in owners:
            if len(owners) >= _MAX_ACCOUNTS:
                raise PetCareRoleAliasOutboxError("owner capacity exceeded")
            owners.append(account)
        pending = state["events"].get(event["source_revision"])
        if pending is not None and account not in pending["owners"]:
            if len(pending["owners"]) >= _MAX_ACCOUNTS:
                raise PetCareRoleAliasOutboxError("event owner capacity exceeded")
            pending["owners"].append(account)
        _write(config_manager, state)


def prepare_retirement(config_manager, character_uid: str) -> dict[str, str]:
    event = _retirement(character_uid)
    with _lock:
        state = _read(config_manager)
        existing = state["events"].get(event["source_revision"])
        if existing is None:
            if len(state["events"]) >= _MAX_EVENTS:
                raise PetCareRoleAliasOutboxError("outbox capacity exceeded")
            owners = list(state["owners"].get(event["character_uid"], []))
            state["events"][event["source_revision"]] = {
                **event,
                "phase": "prepared",
                "owners": owners,
                "created_at": _utc_now(),
            }
            _write(config_manager, state)
        else:
            owners = list(state["owners"].get(event["character_uid"], []))
            changed = False
            for owner in owners:
                if owner not in existing["owners"]:
                    if len(existing["owners"]) >= _MAX_ACCOUNTS:
                        raise PetCareRoleAliasOutboxError("event owner capacity exceeded")
                    existing["owners"].append(owner)
                    changed = True
            if changed:
                _write(config_manager, state)
        _active_preparations[event["character_uid"]] = (
            _active_preparations.get(event["character_uid"], 0) + 1
        )
        return event


def _configured_character_uids(config_manager) -> set[str]:
    try:
        characters = config_manager.load_characters(require_authoritative=True)
    except Exception as exc:
        raise PetCareRoleAliasOutboxError(
            "cannot read authoritative character config"
        ) from exc
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    if not isinstance(catgirls, dict):
        return set()
    return {
        uid
        for value in catgirls.values()
        if isinstance(value, dict)
        for uid in [get_character_uid(value)]
        if is_valid_character_uid(uid)
    }


def reconcile_retirements(config_manager) -> None:
    """Resolve prepare-vs-character-save crashes from the durable config fact."""

    with _lock:
        state = _read(config_manager)
        live_uids = _configured_character_uids(config_manager)
        changed = False
        for revision, event in list(state["events"].items()):
            if event["phase"] != "prepared":
                continue
            # A character delete can publish characters.json before later
            # cleanup either commits or rolls it back.  Do not let a parallel
            # renderer turn that transient absence into a ready retirement.
            # A process crash clears this in-memory fence; the next read then
            # resolves the durable prepared row from authoritative config.
            if _active_preparations.get(event["character_uid"], 0) > 0:
                continue
            if event["character_uid"] in live_uids:
                del state["events"][revision]
            elif event["owners"]:
                event["phase"] = "ready"
            else:
                # No successful bind ever registered an owner.  Deleting an
                # arbitrary local role must not create a server tombstone.
                del state["events"][revision]
            changed = True
        if changed:
            _write(config_manager, state)


def finish_retirement(config_manager, character_uid: str) -> None:
    """Release one in-process delete fence, then resolve its durable row."""

    uid = _retirement(character_uid)["character_uid"]
    with _lock:
        count = _active_preparations.get(uid, 0)
        if count <= 1:
            _active_preparations.pop(uid, None)
        else:
            _active_preparations[uid] = count - 1
    # Keep the prepared row when the authoritative character file cannot be
    # read.  A later lifecycle read can retry without guessing that a fallback
    # or corrupt config means the role was deleted.
    reconcile_retirements(config_manager)


def list_ready_retirements(config_manager, account_id: str) -> list[dict[str, str]]:
    account = _account_id(account_id)
    reconcile_retirements(config_manager)
    with _lock:
        state = _read(config_manager)
        return [
            {key: event[key] for key in ("character_uid", "source_revision", "status")}
            for event in state["events"].values()
            if event["phase"] == "ready" and account in event["owners"]
        ]


def acknowledge_retirement(config_manager, account_id: str, source_revision: str) -> bool:
    account = _account_id(account_id)
    revision = str(source_revision or "").strip()
    with _lock:
        state = _read(config_manager)
        event = state["events"].get(revision)
        if event is None or account not in event["owners"]:
            return False
        event["owners"] = [owner for owner in event["owners"] if owner != account]
        uid = event["character_uid"]
        known = state["owners"].get(uid, [])
        remaining = [owner for owner in known if owner != account]
        if remaining:
            state["owners"][uid] = remaining
        else:
            state["owners"].pop(uid, None)
        if not event["owners"]:
            state["events"].pop(revision, None)
        _write(config_manager, state)
        return True


def _local_request_allowed(request: Request) -> bool:
    try:
        if getattr(request, "scope", {}).get("neko.instance_identity"):
            return bool(card_drop.local_request_source_allowed(request))
        return (
            (request.headers.get("sec-fetch-site") or "").strip().lower()
            == "same-origin"
            and bool(card_drop.local_request_source_allowed(request))
        )
    except Exception:
        return False


def _no_store(content: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/role-owners")
async def register_role_owner(request: Request):
    if not _local_request_allowed(request):
        return _no_store({"detail": "loopback_only"}, 403)
    try:
        body = await request.json()
        expected = _account_id(body.get("account_id") if isinstance(body, dict) else None)
        uid = str(body.get("character_uid") if isinstance(body, dict) else "")
    except Exception:
        return _no_store({"detail": "invalid_request"}, 400)
    current = saved_account_id()
    if not current or current != expected:
        return _no_store({"detail": "account_context_changed"}, 409)
    try:
        await asyncio.to_thread(
            remember_owner, get_config_manager(), uid, current
        )
    except PetCareRoleAliasOutboxError:
        return _no_store({"detail": "local_outbox_unavailable"}, 503)
    return _no_store({"status": "remembered", "character_uid": uid})


@router.get("/role-alias-retirements")
async def pending_role_alias_retirements(request: Request):
    if not _local_request_allowed(request):
        return _no_store({"detail": "loopback_only"}, 403)
    current = saved_account_id()
    if not current:
        return _no_store({"events": []})
    try:
        expected = _account_id(request.headers.get("x-neko-expected-account"))
    except PetCareRoleAliasOutboxError:
        return _no_store({"detail": "expected_account_required"}, 422)
    if current != expected:
        return _no_store({"detail": "account_context_changed"}, 409)
    try:
        rows = await asyncio.to_thread(
            list_ready_retirements, get_config_manager(), current
        )
    except PetCareRoleAliasOutboxError:
        return _no_store({"detail": "local_outbox_unavailable"}, 503)
    return _no_store({"events": rows})


@router.post("/role-alias-retirements/ack")
async def ack_role_alias_retirement(request: Request):
    if not _local_request_allowed(request):
        return _no_store({"detail": "loopback_only"}, 403)
    current = saved_account_id()
    if not current:
        return _no_store({"detail": "account_required"}, 401)
    try:
        body = await request.json()
        revision = str(body.get("source_revision") if isinstance(body, dict) else "").strip()
        expected = _account_id(body.get("account_id") if isinstance(body, dict) else None)
    except Exception:
        return _no_store({"detail": "invalid_request"}, 400)
    if current != expected:
        return _no_store({"detail": "account_context_changed"}, 409)
    try:
        removed = await asyncio.to_thread(
            acknowledge_retirement, get_config_manager(), current, revision
        )
    except PetCareRoleAliasOutboxError:
        return _no_store({"detail": "local_outbox_unavailable"}, 503)
    return _no_store({"status": "acknowledged" if removed else "absent"})
