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
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("neko.community_device_hold")

HOLD_FILENAME = "community_device_hold.json"
OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
_UNREADABLE = object()


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
    if isinstance(saved_bind, dict) and saved_bind.get("error") == OWNERSHIP_CONFLICT:
        return True
    record = _read(directory)
    if record is None:
        return False
    if record is _UNREADABLE:
        return True
    if record.get("conflict"):
        return True
    owner = _account(record.get("owner"))
    current = _account(signed_in)
    return bool(owner and current and owner != current)


def record_bind_outcome(directory: Path | str | None, bind: Any, account: str | None) -> bool:
    """Update the record from one bind attempt made as ``account``.

    A refused bind marks a conflict. A successful bind confirms ``account`` as
    the owner and clears any conflict. Any other outcome (cloud unreachable,
    skipped, proof errors) says nothing about the owner and changes nothing.
    Returns False only when a required write failed.
    """
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
    else:
        return True
    return _write(directory, record)


def remember_owner(directory: Path | str | None, account: str | None) -> None:
    """Adopt a confirmed owner for installs that bound before this record existed.

    Only fills an empty record; it never overrides a recorded owner or conflict.
    """
    account = _account(account)
    if not account or _read(directory) is not None:
        return
    _write(directory, {"owner": account, "conflict": False})


__all__ = [
    "HOLD_FILENAME",
    "OWNERSHIP_CONFLICT",
    "default_directory",
    "device_held",
    "hold_path",
    "record_bind_outcome",
    "remember_owner",
]
