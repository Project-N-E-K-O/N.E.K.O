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
* ``checking``: a login asked the cloud about the device and its answer has
  not been recorded yet.

Only a successful bind updates ``owner`` and clears ``conflict``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import uuid
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("neko.community_device_hold")

HOLD_FILENAME = "community_device_hold.json"
OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
# A bind request that failed after it may have reached the cloud: the cloud may
# have bound the device without this install hearing back.
CLOUD_UNREACHABLE = "cloud_unreachable"
# Key in a logout tombstone of community_auth.json: the device record could not
# take this hold, so the tombstone is its copy on disk.
DEVICE_HOLD_PENDING = "device_hold_pending"
_UNREADABLE = object()
# The main server's facts worker and login routes share an event loop. Hold
# this across each header-only upload or cloud bind and its local commit.
ownership_lock = asyncio.Lock()
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
# because the auth mirror belongs to another login: never counts as bound.
# (A missing mirror or bind field counts as bound, matching /auth-status; an
# unreadable mirror holds before this is consulted.)
UNKNOWN_BIND = object()
# Set when a hold that had to be recorded could not be written. Uploads stay
# held for the rest of this process even if the login that carried the
# conflict is replaced or cleared; only a written successful bind resets it.
_unrecorded_hold = False
# Installed by the login layer: asks the cloud again with the signed-in login
# when a check on disk never got a definite answer.
_check_resolver: Callable[[], Awaitable[None]] | None = None


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
    except (OSError, UnicodeDecodeError) as exc:
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
    if isinstance(record, dict) and (record.get("conflict") or record.get("checking")):
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
        # An unreadable record holds only while it stays unreadable; a read
        # error can pass. Overwrite it so the hold is actually on disk.
        record = _read(directory)
        record = record if isinstance(record, dict) else {}
        if _write(directory, {**record, "conflict": True}):
            _set_unrecorded_hold(False)
            return True
        return False


def begin_ownership_check(directory: Path | str | None) -> bool:
    """Hold uploads on disk before asking the cloud who owns the device.

    A rebind can discover that another account owns the device. If every
    write after that answer failed, nothing on disk would remember it once
    the process restarts. Recording the hold first means the answer can only
    release it (a written successful bind), never be lost. Returns False when
    the hold cannot be written; the caller must not start the check then.
    """
    with record_lock:
        record = _read(directory)
        if record is _UNREADABLE:
            return False
        record = record if isinstance(record, dict) else {}
        return _write(directory, {**record, "conflict": True})


def begin_bind_check(directory: Path | str | None) -> bool | None:
    """Hold uploads on disk before a login asks the cloud to bind the device.

    The answer can show that the cloud owner is not the one on file. If every
    write after it failed, nothing on disk would remember it once the process
    restarts. Unlike ``begin_ownership_check`` this does not mark a conflict,
    so a login whose answer is recorded leaves no hold behind for the owner.

    Checks run one at a time under the ownership lock, so a
    check already on disk never got a definite answer. Returns None when the
    hold cannot be written; the caller must not bind then. Returns False when
    this check takes over such a leftover one, True when it started a new one.
    Pass the result to ``end_bind_check``.
    """
    with record_lock:
        record = _read(directory)
        if record is _UNREADABLE:
            return None
        record = record if isinstance(record, dict) else {}
        if record.get("checking"):
            return False
        return True if _write(directory, {**record, "checking": True}) else None


def end_bind_check(directory: Path | str | None, check: bool | None, bind: Any) -> bool:
    """Release a check's hold once its answer ``bind`` is recorded.

    The hold stays while the answer leaves the owner unknown (the request may
    have reached the cloud). A check that took over a leftover one only ends
    on an answer that settles the owner whatever the lost answer was: a bind,
    or a refusal now held as a conflict. Returns False only when a required
    write failed.
    """
    with record_lock:
        if check is None or not isinstance(bind, dict) or bind.get("error") == CLOUD_UNREACHABLE:
            return True
        record = _read(directory)
        if not check and bind.get("bound") is not True and not (
            bind.get("error") == OWNERSHIP_CONFLICT and isinstance(record, dict) and record.get("conflict")
        ):
            return True
        if not isinstance(record, dict):
            return record is None
        if not record.get("checking"):
            return True
        return _write(directory, {key: value for key, value in record.items() if key != "checking"})


def check_pending(directory: Path | str | None) -> bool:
    """Whether a check on disk is still waiting for a definite answer."""
    record = _read(directory)
    return isinstance(record, dict) and bool(record.get("checking"))


def install_check_resolver(resolver: Callable[[], Awaitable[None]]) -> None:
    global _check_resolver
    _check_resolver = resolver


async def resolve_pending_check() -> None:
    """Ask the cloud again when a check on disk never got a definite answer.

    Otherwise a login that stopped mid-check (or lost its answer to a network
    error) would hold uploads until someone happened to log in again.
    """
    resolver = _check_resolver
    if resolver is None or not await asyncio.to_thread(check_pending, default_directory()):
        return
    try:
        await resolver()
    except Exception as exc:  # noqa: BLE001
        logger.warning("community_device_hold: re-asking the cloud failed: %s", exc)


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
        # An unreadable record is rewritten rather than trusted to keep
        # holding: the read error may pass and leave no conflict behind.
        record = record if isinstance(record, dict) else {}
        owner = _account(record.get("owner"))
        if owner == account or record.get("conflict"):
            return True
        # Another account, or nobody confirmed yet: this account's local
        # memories must not reach whoever the cloud lists as the owner.
        return _write_hold(directory, {**record, "conflict": True})


def record_unsaved_bind(directory: Path | str | None, bind: Any, account: str | None) -> bool:
    """Record a bind answer for a login whose credentials were not saved.

    That login never used this device, so unlike ``record_bind_outcome`` it
    does not pause the confirmed owner's uploads, and a successful bind does
    not make ``account`` the owner. It holds only when the answer shows the
    record is out of date, since the login left in place keeps uploading by
    it: the cloud refused ``account`` while the record names it (or nobody),
    or bound the device to ``account`` while the record names someone else.
    An unreadable record holds either way. Other answers change nothing.
    """
    with record_lock:
        if not isinstance(bind, dict):
            return True
        refused = bind.get("error") == OWNERSHIP_CONFLICT
        if not refused and bind.get("bound") is not True:
            return True
        account = _account(account)
        record = _read(directory)
        readable = isinstance(record, dict)
        record = record if readable else {}
        owner = _account(record.get("owner"))
        if readable and (owner not in (None, account) if refused else owner == account):
            return True
        return _write_hold(directory, {**record, "conflict": True})


def remember_owner(directory: Path | str | None, account: str | None) -> None:
    """Adopt a confirmed owner for installs that bound before this record existed.

    Only fills a record without an owner or conflict; it never overrides them.
    """
    with record_lock:
        account = _account(account)
        record = _read(directory)
        if not account or record is _UNREADABLE:
            return
        record = record if isinstance(record, dict) else {}
        if record.get("owner") or record.get("conflict"):
            return
        _write(directory, {**record, "owner": account, "conflict": False})


__all__ = [
    "CLOUD_UNREACHABLE",
    "DEVICE_HOLD_PENDING",
    "HOLD_FILENAME",
    "OWNERSHIP_CONFLICT",
    "LEGACY_ACCOUNT",
    "UNKNOWN_BIND",
    "begin_bind_check",
    "begin_ownership_check",
    "check_pending",
    "end_bind_check",
    "hold_latched",
    "install_check_resolver",
    "persist_latched_hold",
    "record_lock",
    "normalize_account",
    "ownership_lock",
    "UNREADABLE_ACCOUNT",
    "default_directory",
    "device_held",
    "hold_path",
    "record_bind_outcome",
    "record_session_account",
    "record_unsaved_bind",
    "resolve_pending_check",
    "remember_owner",
]
