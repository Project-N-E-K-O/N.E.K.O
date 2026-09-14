# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Inventory the legacy selected-root files owned by community login.

Community credentials and PKCE state now live below the fixed local-state
anchor.  Older releases wrote these exact filenames at the selected root,
outside the runtime-entry inventory.  Storage status and cleanup share this
metadata-only probe so they agree about whether a retained root still contains
managed data without importing either FastAPI router.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .policy import path_chain_has_symlink

COMMUNITY_AUTH_FILENAME = "community_auth.json"
SOCIAL_SESSION_FILENAME = "social_session.json"
COMMUNITY_OAUTH_PENDING_FILENAME = "community_oauth_pending.json"
COMMUNITY_STEAM_PENDING_FILENAME = "community_steam_pending.json"
SOCIAL_SESSION_LOCK_FILENAME = f"{SOCIAL_SESSION_FILENAME}.lock"

COMMUNITY_PRIVATE_STATE_FILENAMES = (
    COMMUNITY_AUTH_FILENAME,
    SOCIAL_SESSION_FILENAME,
    COMMUNITY_OAUTH_PENDING_FILENAME,
    COMMUNITY_STEAM_PENDING_FILENAME,
)


@dataclass(frozen=True)
class RetainedCommunityStateInventory:
    present_names: tuple[str, ...] = ()
    unsafe_names: tuple[str, ...] = ()
    unreadable_names: tuple[str, ...] = ()
    active_social_lock: bool = False

    @property
    def has_managed_content(self) -> bool:
        return bool(self.present_names or self.unsafe_names or self.unreadable_names)

    @property
    def cleanup_blocked(self) -> bool:
        return bool(self.unsafe_names or self.unreadable_names or self.active_social_lock)

    def has_expected_content(self, expected_names: set[str]) -> bool:
        managed_names = set(self.present_names) | set(self.unsafe_names) | set(self.unreadable_names)
        if "<root>" in managed_names:
            return bool(expected_names)
        return not managed_names.isdisjoint(expected_names)

    @property
    def state(self) -> str:
        if self.unsafe_names:
            return "unsafe"
        if self.unreadable_names:
            return "unreadable"
        if self.active_social_lock:
            return "active_lock"
        if self.present_names:
            return "present"
        return "absent"


def probe_retained_community_state(
    retained_root: Path | str,
) -> RetainedCommunityStateInventory:
    """Inspect only the exact legacy community filenames without following links."""
    root = Path(retained_root).expanduser()
    if not root.is_absolute():
        return RetainedCommunityStateInventory(unsafe_names=("<root>",))
    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        return RetainedCommunityStateInventory()
    except OSError:
        return RetainedCommunityStateInventory(unreadable_names=("<root>",))
    if (
        stat.S_ISLNK(root_metadata.st_mode)
        or not stat.S_ISDIR(root_metadata.st_mode)
        or path_chain_has_symlink(root)
    ):
        return RetainedCommunityStateInventory(unsafe_names=("<root>",))

    present: list[str] = []
    unsafe: list[str] = []
    unreadable: list[str] = []
    active_social_lock = False
    for filename in (*COMMUNITY_PRIVATE_STATE_FILENAMES, SOCIAL_SESSION_LOCK_FILENAME):
        candidate = root / filename
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            unreadable.append(filename)
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or path_chain_has_symlink(candidate)
        ):
            unsafe.append(filename)
            continue
        present.append(filename)
        if filename == SOCIAL_SESSION_LOCK_FILENAME:
            # Age cannot prove that no process still owns an exclusive-create
            # lock (a suspended process or slow filesystem can outlive any
            # wall-clock threshold).  Every extant lock therefore blocks
            # migration and cleanup until an operator verifies and removes it.
            active_social_lock = True

    return RetainedCommunityStateInventory(
        present_names=tuple(present),
        unsafe_names=tuple(unsafe),
        unreadable_names=tuple(unreadable),
        active_social_lock=active_social_lock,
    )


def snapshot_retained_community_state(
    retained_root: Path | str,
    *,
    allow_active_social_lock: bool = False,
    dir_fd: int | None = None,
) -> dict[str, str]:
    """Hash exact managed private files for a durable cleanup intent."""
    root = Path(retained_root).expanduser()
    if dir_fd is None:
        inventory = probe_retained_community_state(root)
        if inventory.unsafe_names or inventory.unreadable_names or (
            inventory.active_social_lock and not allow_active_social_lock
        ):
            raise OSError(f"retained community state is {inventory.state}")

    snapshot: dict[str, str] = {}
    for filename in COMMUNITY_PRIVATE_STATE_FILENAMES:
        path = root / filename
        try:
            before = (
                path.lstat()
                if dir_fd is None
                else os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OSError(f"retained {filename} is unreadable") from exc
        if not stat.S_ISREG(before.st_mode) or (
            dir_fd is None and path_chain_has_symlink(path)
        ):
            raise OSError(f"retained {filename} is unsafe")
        try:
            if dir_fd is None:
                content = path.read_bytes()
                after = path.lstat()
            else:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(filename, flags, dir_fd=dir_fd)
                try:
                    after = os.fstat(fd)
                    chunks: list[bytes] = []
                    while chunk := os.read(fd, 1024 * 1024):
                        chunks.append(chunk)
                    content = b"".join(chunks)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise OSError(f"retained {filename} is unreadable") from exc
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or len(content) != after.st_size:
            raise OSError(f"retained {filename} changed while snapshotting")
        snapshot[filename] = hashlib.sha256(content).hexdigest()
    return snapshot


def retained_community_snapshot_matches(
    retained_root: Path | str,
    expected: dict[str, str],
    *,
    dir_fd: int | None = None,
) -> bool:
    """Allow a partially deleted intent, but reject changed or new private files."""
    if not isinstance(expected, dict) or any(
        filename not in COMMUNITY_PRIVATE_STATE_FILENAMES
        or not isinstance(digest, str)
        or len(digest) != 64
        for filename, digest in expected.items()
    ):
        return False
    try:
        current = snapshot_retained_community_state(
            retained_root,
            allow_active_social_lock=True,
            dir_fd=dir_fd,
        )
    except OSError:
        return False
    return all(
        filename in expected and expected[filename] == digest
        for filename, digest in current.items()
    )
