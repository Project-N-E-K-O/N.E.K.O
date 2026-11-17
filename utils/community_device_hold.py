# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Device-scoped hold for header-only uploads while another account owns this device.

Uploads that carry only ``X-Client-Id`` are filed by the cloud under the
account the device is bound to. When the signed-in account is not that owner,
those uploads would land in someone else's account, so they must wait.

The record lives in its own file beside the credential files, not inside
them. Logging out deletes the credentials, a later transient bind failure
rewrites their bind outcome, and a browser session sync replaces the login
without binding at all; none of these proves the device changed owner. The
record keeps two facts the cloud has confirmed:

* ``owner``: the account a successful bind last confirmed as the owner.
* ``conflict``: a bind was refused because another account owns the device.

Only a successful bind updates ``owner`` and clears ``conflict``.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("neko.community_device_hold")

HOLD_FILENAME = "community_device_hold.json"
OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
_UNREADABLE = object()
# Every read-modify-write of the record holds this, so a stale outcome read
# before a newer one cannot be written after it. Reentrant: callers may hold
# it around several record operations that each take it too.
record_lock = threading.RLock()
# ``signed_in`` values for a login whose account cannot be compared.
# Unreadable credentials always hold. A pre-v2 session (token, no account id)
# only holds when there is an owner to compare against or its own bind failed.
UNREADABLE_ACCOUNT = "unreadable"
LEGACY_ACCOUNT = "legacy"
# ``saved_bind`` value when the login's own bind outcome cannot be trusted
# (mirror missing, unreadable, or belonging to another login): never bound.
UNKNOWN_BIND = object()
# Set when a hold that had to be recorded could not be written. Uploads stay
# held for the rest of this process even if the login that carried the
# conflict is replaced or cleared; only a written successful bind resets it.
_unrecorded_hold = False


def default_directory() -> Path | None:
    """The credential directory (beside ``community_auth.json``)."""
    try:
        from utils.config_manager import get_config_manager

        return Path(get_config_manager().memory_dir).parent
    except Exception as exc:  # noqa: BLE001
        logger.warning("community_device_hold: credential directory unavailable: %s", exc)
        return None


def hold_path(directory: Path | str | None) -> Path | None:
    return Path(directory) / HOLD_FILENAME if directory else None


def _account(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return None


def _read(directory: Path | str | None) -> Any:
    path = hold_path(directory)
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("community_device_hold: cannot read hold record: %s", exc)
        return _UNREADABLE
    try:
        data = json.loads(raw)
    except ValueError:
        return _UNREADABLE
    return data if isinstance(data, dict) else _UNREADABLE


def _write(directory: Path | str | None, record: dict) -> bool:
    path = hold_path(directory)
    if path is None:
        return False
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("community_device_hold: cannot write hold record: %s", exc)
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def device_held(
    directory: Path | str | None,
    signed_in: str | None = None,
    *,
    saved_bind: Any = None,
) -> bool:
    """Whether header-only uploads must wait.

    Held after a refused bind, or while a different account than the confirmed
    owner is signed in. Unreadable records fail closed. ``saved_bind`` is the
    signed-in login's own bind outcome: a refused bind there also holds, so a
    hold record that could not be written does not open the gate.
    """
    if _unrecorded_hold:
        return True
    if isinstance(saved_bind, dict) and saved_bind.get("error") == OWNERSHIP_CONFLICT:
        return True
    record = _read(directory)
    if record is _UNREADABLE:
        return True
    if isinstance(record, dict) and record.get("conflict"):
        return True
    owner = _account(record.get("owner")) if isinstance(record, dict) else None
    if signed_in == UNREADABLE_ACCOUNT:
        # Credentials exist but cannot be read: never treat that as safe.
        return True
    if signed_in == LEGACY_ACCOUNT:
        # A pre-v2 session cannot be compared with an owner on file, so hold
        # then; without an owner, its own bind decides as below.
        if owner is not None:
            return True
        return not _bind_counts_as_bound(saved_bind)
    current = _account(signed_in)
    if current is None:
        # Nobody signed in: uploads land with the device's owner, and no
        # other account's use of the device is on record (that would be a
        # conflict above).
        return False
    if owner is not None:
        return owner != current
    # No confirmed owner on file. The signed-in login's own bind decides.
    return not _bind_counts_as_bound(saved_bind)


def _bind_counts_as_bound(saved_bind: Any) -> bool:
    """Sessions saved before bind outcomes existed count as bound, matching
    /auth-status. A login that never bound (browser session sync) does not."""
    return saved_bind is None or (isinstance(saved_bind, dict) and saved_bind.get("bound") is True)


def record_bind_outcome(directory: Path | str | None, bind: Any, account: str | None) -> bool:
    """Update the record from one bind attempt made as ``account``.

    A refused bind marks a conflict. A successful bind confirms ``account`` as
    the owner and clears any conflict. Any other outcome (cloud unreachable,
    skipped, proof errors) says nothing about the owner and changes nothing.
    Returns False only when a required write failed.
    """
    with record_lock:
        if not isinstance(bind, dict):
            return True
        record = _read(directory)
        if not isinstance(record, dict):
            record = {}
        account = _account(account)
        if bind.get("error") == OWNERSHIP_CONFLICT:
            if record.get("owner") and _account(record.get("owner")) == account:
                # The cloud says this account is not the owner after all.
                record["owner"] = None
            record["conflict"] = True
        elif bind.get("bound") is True and account:
            record = {"owner": account, "conflict": False}
            if _write(directory, record):
                _set_unrecorded_hold(False)
                return True
            return False
        else:
            return True
        return _write_hold(directory, record)


def persist_latched_hold(directory: Path | str | None) -> bool:
    """Write an in-memory hold to disk before the login that knew of it goes.

    Returns True when nothing was latched or the hold is now on disk.
    """
    with record_lock:
        if not _unrecorded_hold:
            return True
        record = _read(directory)
        if record is _UNREADABLE:
            return True  # already holds on disk
        record = record if isinstance(record, dict) else {}
        if _write(directory, {**record, "conflict": True}):
            _set_unrecorded_hold(False)
            return True
        return False


def hold_latched() -> bool:
    """Whether a hold that had to be recorded is held only in this process."""
    return _unrecorded_hold


def normalize_account(value: object) -> str | None:
    return _account(value)


def _set_unrecorded_hold(value: bool) -> None:
    global _unrecorded_hold
    _unrecorded_hold = value


def _write_hold(directory: Path | str | None, record: dict) -> bool:
    """Write a record that holds uploads; latch the hold in memory if it fails."""
    if _write(directory, record):
        return True
    _set_unrecorded_hold(True)
    return False


def record_session_account(directory: Path | str | None, account: str | None) -> bool:
    """Note that ``account`` now uses this device, whatever its bind showed.

    Unless ``account`` is the confirmed owner, hold uploads until a bind
    confirms who owns the device. Logging that account out later does not
    release the hold, since its unsynced local memories are still on the
    device.
    """
    with record_lock:
        account = _account(account)
        if not account:
            return True
        record = _read(directory)
        if record is _UNREADABLE:
            return True  # already held
        record = record if isinstance(record, dict) else {}
        owner = _account(record.get("owner"))
        if owner == account or record.get("conflict"):
            return True
        # Another account, or nobody confirmed yet: this account's local
        # memories must not reach whoever the cloud lists as the owner.
        return _write_hold(directory, {**record, "conflict": True})


def remember_owner(directory: Path | str | None, account: str | None) -> None:
    """Adopt a confirmed owner for installs that bound before this record existed.

    Only fills an empty record; it never overrides a recorded owner or conflict.
    """
    with record_lock:
        account = _account(account)
        if not account or _read(directory) is not None:
            return
        _write(directory, {"owner": account, "conflict": False})


    __all__ = [
        "HOLD_FILENAME",
        "OWNERSHIP_CONFLICT",
        "LEGACY_ACCOUNT",
        "UNKNOWN_BIND",
        "hold_latched",
        "persist_latched_hold",
    "record_lock",
        "normalize_account",
        "UNREADABLE_ACCOUNT",
        "default_directory",
        "device_held",
        "hold_path",
        "record_bind_outcome",
        "record_session_account",
        "remember_owner",
    ]
