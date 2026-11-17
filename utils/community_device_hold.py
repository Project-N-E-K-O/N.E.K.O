# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Device-scoped hold for header-only uploads while another account owns this device.

Uploads that carry only ``X-Client-Id`` are filed by the cloud under the
account the device is bound to. Once a login learns the device belongs to a
different account, those uploads must stop until the device is bound to the
signed-in account again.

The hold lives in its own file beside the credential files, not inside them:
logging out deletes the credentials, and a later transient bind failure
rewrites their bind outcome, yet neither proves the device changed owner.
Only a successful bind clears it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger("neko.community_device_hold")

HOLD_FILENAME = "community_device_hold.json"
OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"


def hold_path(directory: Path | str | None) -> Path | None:
    return Path(directory) / HOLD_FILENAME if directory else None


def device_held(directory: Path | str | None) -> bool:
    """Whether header-only uploads must wait. Unreadable records fail closed."""
    path = hold_path(directory)
    if path is None:
        return False
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("community_device_hold: cannot read hold record: %s", exc)
        return True
    try:
        data = json.loads(raw)
    except ValueError:
        return True
    return not (isinstance(data, dict) and data.get("held") is False)


def set_held(directory: Path | str | None) -> bool:
    path = hold_path(directory)
    if path is None:
        return False
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"held": True, "reason": OWNERSHIP_CONFLICT}), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("community_device_hold: cannot record hold: %s", exc)
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear_held(directory: Path | str | None) -> bool:
    path = hold_path(directory)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("community_device_hold: cannot clear hold: %s", exc)
        return False


def record_bind_outcome(directory: Path | str | None, bind: Any) -> None:
    """Hold on an ownership conflict, release on a successful bind, else keep.

    Any other outcome (cloud unreachable, skipped, proof errors) says nothing
    about who owns the device, so it leaves the current hold as it is.
    """
    if not isinstance(bind, dict):
        return
    if bind.get("error") == OWNERSHIP_CONFLICT:
        set_held(directory)
    elif bind.get("bound") is True:
        clear_held(directory)


__all__ = [
    "HOLD_FILENAME",
    "OWNERSHIP_CONFLICT",
    "clear_held",
    "device_held",
    "hold_path",
    "record_bind_outcome",
    "set_held",
]
