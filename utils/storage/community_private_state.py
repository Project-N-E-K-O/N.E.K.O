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
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .policy import path_chain_has_symlink

COMMUNITY_AUTH_FILENAME = "community_auth.json"
SOCIAL_SESSION_FILENAME = "social_session.json"
COMMUNITY_OAUTH_PENDING_FILENAME = "community_oauth_pending.json"
COMMUNITY_STEAM_PENDING_FILENAME = "community_steam_pending.json"
SOCIAL_SESSION_LOCK_FILENAME = f"{SOCIAL_SESSION_FILENAME}.lock"
SOCIAL_LOCK_SCHEMA_VERSION = 2
SOCIAL_LOCK_OWNER_ACTIVE = "active"
SOCIAL_LOCK_OWNER_ORPHANED = "orphaned"
SOCIAL_LOCK_OWNER_UNKNOWN = "unknown"

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
    orphaned_social_lock: bool = False

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
        if self.orphaned_social_lock:
            return "orphaned_lock"
        if self.present_names:
            return "present"
        return "absent"


def parse_social_lock_owner(raw: bytes | str) -> dict | None:
    """Parse the compatible lock owner without accepting partial records."""
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = str(payload.get("token") or "").strip()
    raw_token_pid = token.split(":", 1)[0]
    if not raw_token_pid.isdigit():
        return None
    token_pid = int(raw_token_pid)
    try:
        explicit_pid = int(payload.get("pid", token_pid))
    except (TypeError, ValueError):
        return None
    if token_pid <= 0 or explicit_pid != token_pid:
        return None
    owner_kind = str(payload.get("owner_kind") or "").strip()
    if owner_kind not in {"", "neko", "pc"}:
        return None
    return {
        "pid": token_pid,
        "token": token,
        "owner_kind": owner_kind,
        "start_token": str(payload.get("start_token") or "").strip(),
        "start_token_scheme": str(payload.get("start_token_scheme") or "").strip(),
    }


def probe_social_lock_process(pid: int) -> tuple[str, str, str]:
    """Return (active/orphaned/unknown, start token, token scheme)."""
    if pid <= 0:
        return SOCIAL_LOCK_OWNER_UNKNOWN, "", ""
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return SOCIAL_LOCK_OWNER_ORPHANED, "", "linux-proc-start-v1"
        except OSError:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "linux-proc-start-v1"
        closing = raw.rfind(") ")
        if closing < 0:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "linux-proc-start-v1"
        fields = raw[closing + 2 :].split()
        if len(fields) <= 19:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "linux-proc-start-v1"
        # Zombies no longer own descriptors even though kill(pid, 0) reports
        # them. Their social lock is therefore conclusively orphaned.
        if fields[0] == "Z":
            return SOCIAL_LOCK_OWNER_ORPHANED, "", "linux-proc-start-v1"
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="ascii"
            ).strip()
        except OSError:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "linux-proc-start-v1"
        if not boot_id:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "linux-proc-start-v1"
        return (
            SOCIAL_LOCK_OWNER_ACTIVE,
            f"{boot_id}:{fields[19]}",
            "linux-proc-start-v1",
        )

    if sys.platform == "win32":
        script = (
            f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue;"
            "if($null -ne $p){$p.StartTime.ToUniversalTime().ToString('o')}"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0),
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "windows-powershell-start-v1"
        output = str(result.stdout or "").strip()
        if result.returncode != 0 or str(result.stderr or "").strip():
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "windows-powershell-start-v1"
        if not output:
            return SOCIAL_LOCK_OWNER_ORPHANED, "", "windows-powershell-start-v1"
        return SOCIAL_LOCK_OWNER_ACTIVE, output, "windows-powershell-start-v1"

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "darwin-ps-lstart-v1"
        output = str(result.stdout or "").strip()
        if result.returncode == 1 and not output and not str(result.stderr or "").strip():
            return SOCIAL_LOCK_OWNER_ORPHANED, "", "darwin-ps-lstart-v1"
        if result.returncode != 0 or str(result.stderr or "").strip() or not output:
            return SOCIAL_LOCK_OWNER_UNKNOWN, "", "darwin-ps-lstart-v1"
        return SOCIAL_LOCK_OWNER_ACTIVE, output, "darwin-ps-lstart-v1"

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return SOCIAL_LOCK_OWNER_ORPHANED, "", ""
    except OSError:
        return SOCIAL_LOCK_OWNER_UNKNOWN, "", ""
    return SOCIAL_LOCK_OWNER_ACTIVE, "", ""


def classify_social_lock_owner(
    owner: dict | None,
    *,
    process_probe=None,
) -> str:
    """Classify one complete owner record; ambiguous evidence stays blocked."""
    if not owner:
        return SOCIAL_LOCK_OWNER_UNKNOWN
    if process_probe is None:
        process_probe = probe_social_lock_process
    observed_state, observed_token, observed_scheme = process_probe(int(owner["pid"]))
    if observed_state != SOCIAL_LOCK_OWNER_ACTIVE:
        return observed_state
    expected_token = str(owner.get("start_token") or "")
    expected_scheme = str(owner.get("start_token_scheme") or "")
    if expected_token and expected_scheme:
        if not observed_token or observed_scheme != expected_scheme:
            return SOCIAL_LOCK_OWNER_UNKNOWN
        if observed_token != expected_token:
            return SOCIAL_LOCK_OWNER_ORPHANED
    return SOCIAL_LOCK_OWNER_ACTIVE


def backend_can_recover_social_lock_owner(owner: dict | None) -> bool:
    """Partition stale-lock recovery so PC and backend can never reap one lock."""
    return bool(owner) and str(owner.get("owner_kind") or "") in {"", "neko"}


class _PrivateStateSnapshotChanged(OSError):
    """The opened private-state name no longer identifies the inspected file."""


def _read_stable_regular_file(
    path: Path,
    before: os.stat_result,
    *,
    dir_fd: int | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, os.stat_result]:
    """Open and read the same regular file without blocking on a raced FIFO."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)

    fd = -1
    try:
        if dir_fd is None:
            fd = os.open(path, flags)
        else:
            fd = os.open(path.name, flags, dir_fd=dir_fd)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(before, opened)
            or (max_bytes is not None and int(opened.st_size) > max_bytes)
        ):
            raise _PrivateStateSnapshotChanged

        if max_bytes is None:
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
            raw = b"".join(chunks)
        else:
            raw = os.read(fd, max_bytes + 1)

        after = os.fstat(fd)
        named = (
            path.lstat()
            if dir_fd is None
            else os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
        )
        if (
            (max_bytes is not None and len(raw) > max_bytes)
            or not os.path.samestat(opened, after)
            or not os.path.samestat(after, named)
        ):
            raise _PrivateStateSnapshotChanged
        return raw, after
    finally:
        if fd >= 0:
            os.close(fd)


def read_social_lock_owner_snapshot(path: Path) -> tuple[str, dict | None]:
    """Read and classify one stable regular lock snapshot without following it."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return SOCIAL_LOCK_OWNER_UNKNOWN, None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        return SOCIAL_LOCK_OWNER_UNKNOWN, None
    try:
        raw, _after = _read_stable_regular_file(path, before, max_bytes=4096)
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return SOCIAL_LOCK_OWNER_UNKNOWN, None
    owner = parse_social_lock_owner(raw)
    return classify_social_lock_owner(owner), owner


def read_social_lock_owner_state(path: Path) -> str:
    """Read and classify a regular lock without exposing its owner record."""
    return read_social_lock_owner_snapshot(path)[0]


def probe_retained_community_state(
    retained_root: Path | str,
    *,
    classify_social_lock_process: bool = True,
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
    orphaned_social_lock = False
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
            if not classify_social_lock_process:
                active_social_lock = True
                continue
            owner_state, owner = read_social_lock_owner_snapshot(candidate)
            orphaned_social_lock = (
                owner_state == SOCIAL_LOCK_OWNER_ORPHANED
                and backend_can_recover_social_lock_owner(owner)
            )
            active_social_lock = not orphaned_social_lock

    return RetainedCommunityStateInventory(
        present_names=tuple(present),
        unsafe_names=tuple(unsafe),
        unreadable_names=tuple(unreadable),
        active_social_lock=active_social_lock,
        orphaned_social_lock=orphaned_social_lock,
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
            content, after = _read_stable_regular_file(
                path,
                before,
                dir_fd=dir_fd,
            )
        except _PrivateStateSnapshotChanged as exc:
            raise OSError(f"retained {filename} changed while snapshotting") from exc
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
