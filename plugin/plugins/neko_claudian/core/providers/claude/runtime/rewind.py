# Ported from claudian/src/providers/claude/runtime/ClaudeRewindService.ts
# Original author: Claudian contributors
# License: MIT

"""
ClaudeRewindService — File backup and restore for conversation rewind.

Provides safe file-level rewind with automatic backup/restore on failure.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class ChatRewindMode:
    """Rewind modes."""
    CONVERSATION = "conversation"
    FILES = "files"


@dataclass
class ChatRewindResult:
    """Result of a rewind operation."""
    can_rewind: bool
    files_changed: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0


@dataclass
class BackupEntryFile:
    """Backup entry for a regular file."""
    original_path: str
    existed_before: bool = True
    kind: str = "file"  # "file", "dir", "symlink"
    backup_path: str = ""


@dataclass
class BackupEntrySymlink:
    """Backup entry for a symlink."""
    original_path: str
    existed_before: bool = True
    kind: str = "symlink"
    symlink_target: str = ""


@dataclass
class BackupEntryMissing:
    """Backup entry for a missing file."""
    original_path: str
    existed_before: bool = False


# Union type for backup entries
BackupEntry = BackupEntryFile | BackupEntrySymlink | BackupEntryMissing


@dataclass
class ClaudeRewindBackup:
    """Backup context with restore and cleanup capabilities."""
    _entries: list[BackupEntry] = field(default_factory=list)
    _backup_root: str = ""

    async def restore(self) -> None:
        """Restore all backed up files to their original state."""
        errors: list[Exception] = []

        for entry in self._entries:
            try:
                if not entry.existed_before:
                    # File didn't exist before - remove it
                    if os.path.exists(entry.original_path):
                        if os.path.isdir(entry.original_path):
                            shutil.rmtree(entry.original_path)
                        else:
                            os.remove(entry.original_path)
                    continue

                # Remove current state
                if os.path.exists(entry.original_path):
                    if os.path.isdir(entry.original_path):
                        shutil.rmtree(entry.original_path)
                    else:
                        os.remove(entry.original_path)

                # Ensure parent directory exists
                os.makedirs(os.path.dirname(entry.original_path), exist_ok=True)

                if isinstance(entry, BackupEntrySymlink):
                    os.symlink(entry.symlink_target, entry.original_path)
                elif isinstance(entry, BackupEntryFile):
                    if entry.kind == "dir":
                        shutil.copytree(entry.backup_path, entry.original_path)
                    else:
                        shutil.copy2(entry.backup_path, entry.original_path)
            except Exception as e:
                errors.append(e)

        if errors:
            raise RuntimeError(f"Failed to restore {errors.len(errors)} file(s) after rewind failure.")

    async def cleanup(self) -> None:
        """Remove the backup directory."""
        if self._backup_root and os.path.exists(self._backup_root):
            shutil.rmtree(self._backup_root, ignore_errors=True)


def resolve_rewind_file_path(file_path: str, workspace_path: Optional[str]) -> str:
    """Resolve a file path relative to workspace if not absolute."""
    if os.path.isabs(file_path):
        return file_path
    if workspace_path:
        return os.path.join(workspace_path, file_path)
    return file_path


async def copy_dir(from_path: str, to_path: str) -> None:
    """Recursively copy a directory."""
    os.makedirs(to_path, exist_ok=True)

    for item in os.listdir(from_path):
        src = os.path.join(from_path, item)
        dst = os.path.join(to_path, item)

        if os.path.islink(src):
            target = os.readlink(src)
            os.symlink(target, dst)
        elif os.path.isdir(src):
            await copy_dir(src, dst)
        else:
            shutil.copy2(src, dst)


async def create_claude_rewind_backup(
    files_changed: Optional[list[str]],
    workspace_path: Optional[str]
) -> Optional[ClaudeRewindBackup]:
    """Create a backup of files before rewind.

    Returns None if no files to backup.
    """
    if not files_changed:
        return None

    backup_root = tempfile.mkdtemp(prefix="neko-rewind-")
    entries: list[BackupEntry] = []

    for i, file_path in enumerate(files_changed):
        original_path = resolve_rewind_file_path(file_path, workspace_path)

        try:
            if os.path.islink(original_path):
                target = os.readlink(original_path)
                entries.append(BackupEntrySymlink(
                    original_path=original_path,
                    existed_before=True,
                    symlink_target=target
                ))
                continue

            backup_path = os.path.join(backup_root, str(i))

            if os.path.isdir(original_path):
                await copy_dir(original_path, backup_path)
                entries.append(BackupEntryFile(
                    original_path=original_path,
                    existed_before=True,
                    kind="dir",
                    backup_path=backup_path
                ))
                continue

            if os.path.isfile(original_path):
                shutil.copy2(original_path, backup_path)
                entries.append(BackupEntryFile(
                    original_path=original_path,
                    existed_before=True,
                    kind="file",
                    backup_path=backup_path
                ))
                continue

            entries.append(BackupEntryMissing(original_path=original_path))

        except FileNotFoundError:
            entries.append(BackupEntryMissing(original_path=original_path))
        except Exception:
            # Clean up on error
            shutil.rmtree(backup_root, ignore_errors=True)
            raise

    backup = ClaudeRewindBackup(_entries=entries, _backup_root=backup_root)
    return backup


@dataclass
class ExecuteClaudeRewindDeps:
    """Dependencies for executeClaudeRewind."""
    assistant_message_id: str
    mode: str  # ChatRewindMode
    rewind_files: Callable  # async (user_message_id, dry_run?) -> RewindFilesResult
    close_persistent_query: Callable[[str], None]
    set_pending_resume_at: Callable[[str], None]
    workspace_path: Optional[str] = None


async def execute_claude_rewind(
    user_message_id: str,
    deps: ExecuteClaudeRewindDeps
) -> ChatRewindResult:
    """Execute a rewind operation.

    For conversation mode: just marks resume point and closes query.
    For files mode: previews changes, backs up, applies, and handles rollback on failure.

    Ported from ClaudeRewindService.ts executeClaudeRewind.
    """
    if deps.mode == ChatRewindMode.CONVERSATION:
        deps.set_pending_resume_at(deps.assistant_message_id)
        deps.close_persistent_query("conversation rewind")
        return ChatRewindResult(can_rewind=True, files_changed=[])

    # Preview changes first
    preview = await deps.rewind_files(user_message_id, True)
    if not preview.can_rewind:
        return preview

    # Create backup
    backup = await create_claude_rewind_backup(preview.files_changed, deps.workspace_path)

    try:
        # Apply rewind
        result = await deps.rewind_files(user_message_id)
        if not result.can_rewind:
            if backup:
                await backup.restore()
            deps.close_persistent_query("rewind failed")
            return result

        deps.set_pending_resume_at(deps.assistant_message_id)
        deps.close_persistent_query("rewind")
        return ChatRewindResult(
            can_rewind=True,
            files_changed=preview.files_changed,
            insertions=preview.insertions,
            deletions=preview.deletions
        )
    except Exception as error:
        # Rollback on failure
        try:
            if backup:
                await backup.restore()
        except Exception as rollback_error:
            deps.close_persistent_query("rewind failed")
            raise RuntimeError(
                f"Rewind failed and files could not be fully restored: {rollback_error}"
            ) from rollback_error

        deps.close_persistent_query("rewind failed")
        raise RuntimeError(
            f"Rewind failed but files were restored: {error}"
        ) from error
    finally:
        if backup:
            await backup.cleanup()
