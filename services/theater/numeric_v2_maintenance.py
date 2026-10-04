"""Numeric v2 启动核查和可恢复剧本删除事务。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from .numeric_v2_archive import (
    FORGET_MARKER_DIRNAME,
    PUBLIC_ARCHIVE_QUARANTINE_DIRNAME,
    RETRACT_INTENT_DIRNAME,
    SESSION_QUARANTINE_DIRNAME,
    NumericV2ArchiveError,
    NumericV2ArchiveStore,
)
from .numeric_v2_registry import NumericV2PackageRegistry, NumericV2PackageError, NumericV2PackageNotFoundError
from .numeric_v2_runtime import NumericV2RuntimeError
from .numeric_v2_store import (
    NumericV2SessionStore,
    NumericV2StoreError,
    _read_numeric_v2_session_summary,
    _read_story_session_slots,
    _write_story_session_slots,
    _delete_numeric_v2_sessions_unlocked,
    _is_story_session_index_content_error,
    numeric_v2_session_files_guard,
    list_numeric_v2_public_archives,
    list_numeric_v2_sessions,
)

from .numeric_v2_storage_transaction import discard_temporary_file, run_storage_mutation


# No quarantine directory is trimmed automatically. Invalid, orphaned and
# duplicate sessions are distinct ledgers, not copies of a surviving session, so
# only an explicit delete or forget (in its rollback-safe transaction) erases them.
DELETE_TRANSACTION_SCHEMA = "neko.script.delete_transaction.numeric.v2"
# 损坏的 story_sessions.json 是可重建的派生缓存；移入独立目录保存，不参与裁剪删除。
INDEX_QUARANTINE_DIRNAME = "quarantine_indexes"
# 这些状态只需清理事务目录，绝不重放备份。
_SETTLED_DELETE_TRANSACTION_STATES = frozenset({"committed", "rolled_back", "superseded"})
# Backup subdirectory for quarantined session files inside a delete transaction.
_QUARANTINED_SESSION_BACKUP_DIRNAME = "quarantined_sessions"
_MANIFEST_PATH_KEYS = (
    "package_target",
    "session_root",
    "public_archive_root",
    "receipt_root",
    "public_archive_quarantine_root",
    "session_quarantine_root",
    "index_target",
)

_MAINTENANCE_LOCK = threading.Lock()
_MAINTAINED_ROOTS: set[str] = set()
# Stories whose interrupted delete could not be rolled back at startup, per root.
# Their transaction directory is kept for manual recovery (and retried at the
# next process start); until then only these stories fail closed.
_RECOVERY_BLOCKED_STORIES: dict[str, frozenset[str]] = {}
logger = logging.getLogger(__name__)


def _atomic_write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".manifest-",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        discard_temporary_file(temporary_path)


class _UnresolvableManifestPathError(ValueError):
    """A delete manifest names a path that cannot be mapped into the current theater root."""


def _manifest_relative(theater_root: Path, target: Path) -> str:
    """Store a manifest target relative to the theater root so a root migration keeps it valid."""

    return Path(target).relative_to(Path(theater_root)).as_posix()


def _expected_manifest_relative(payload: Mapping[str, Any], key: str) -> PurePosixPath | None:
    """Return the fixed layout location of a manifest key, used to map legacy absolute paths."""

    story_id = str(payload.get("story_id") or "").strip()
    fixed = {
        "package_target": f"numeric_v2/packages/{story_id}.json" if story_id else "",
        "session_root": "numeric_v2/sessions",
        "public_archive_root": "numeric_v2/public_archives",
        "receipt_root": "numeric_v2/end_receipts",
        "public_archive_quarantine_root": f"numeric_v2/{PUBLIC_ARCHIVE_QUARANTINE_DIRNAME}",
        "session_quarantine_root": f"numeric_v2/{SESSION_QUARANTINE_DIRNAME}",
        "index_target": "numeric_v2/story_sessions.json",
    }.get(key, "")
    return PurePosixPath(fixed) if fixed else None


def _path_under(root: Path, candidate: Path) -> PurePosixPath | None:
    for base, target in ((root, candidate), (root.resolve(), candidate.resolve())):
        try:
            return PurePosixPath(target.relative_to(base).as_posix())
        except ValueError:
            continue
    return None


def _manifest_path(payload: Mapping[str, Any], key: str, theater_root: Path) -> Path | None:
    """Resolve a manifest target inside the current theater root.

    New manifests store paths relative to the theater root. Legacy manifests
    stored absolute paths, which go stale after a storage-root migration: a path
    already under the current root is used as is, and a path under another root
    is mapped onto the current root only when its tail is exactly the fixed
    layout location of that key (i.e. relative to the old theater root). Anything
    else raises, so recovery never writes outside the current root.
    """

    raw = str(payload.get(key) or "").strip()
    if not raw:
        return None
    root = Path(theater_root)
    candidate = Path(raw)
    if not candidate.is_absolute() and not PureWindowsPath(raw).is_absolute():
        relative = PurePosixPath(candidate.as_posix())
        if ".." in relative.parts:
            raise _UnresolvableManifestPathError(key)
        return root.joinpath(*relative.parts)
    current_relative = _path_under(root, candidate)
    if current_relative is not None and ".." not in current_relative.parts:
        return root.joinpath(*current_relative.parts)
    expected = _expected_manifest_relative(payload, key)
    legacy_parts = PurePosixPath(raw.replace("\\", "/")).parts
    if expected is not None and tuple(legacy_parts[-len(expected.parts):]) == expected.parts:
        return root.joinpath(*expected.parts)
    raise _UnresolvableManifestPathError(key)


def _restore_missing_file(backup: Path, target: Path) -> None:
    # Only undo this transaction's unlink: a file present again at the target is
    # either untouched or newer (re-import, new round) and must not be overwritten.
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)


def _manifest_paths_resolvable(payload: Mapping[str, Any], theater_root: Path) -> bool:
    for key in _MANIFEST_PATH_KEYS:
        try:
            _manifest_path(payload, key, theater_root)
        except _UnresolvableManifestPathError:
            return False
    return True


def _restore_delete_transaction(
    transaction_dir: Path, payload: Mapping[str, Any], theater_root: Path,
) -> None:
    """Best-effort undo of a story delete; every step runs, the first failure is raised last."""

    if not _manifest_paths_resolvable(payload, theater_root):
        # Never restore into a location outside the current root.
        raise _UnresolvableManifestPathError(str(transaction_dir))
    failures: list[BaseException] = []

    def attempt(step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - collected and re-raised below
            failures.append(exc)

    package_backup = transaction_dir / "package.json"
    package_target = _manifest_path(payload, "package_target", theater_root)
    if package_backup.is_file() and package_target is not None:
        attempt(lambda: _restore_missing_file(package_backup, package_target))

    for root_key, backup_dirname in (
        ("session_root", "sessions"),
        ("public_archive_root", "public_archives"),
        ("receipt_root", "end_receipts"),
        ("public_archive_quarantine_root", PUBLIC_ARCHIVE_QUARANTINE_DIRNAME),
        ("session_quarantine_root", _QUARANTINED_SESSION_BACKUP_DIRNAME),
    ):
        target_root = _manifest_path(payload, root_key, theater_root)
        backup_root = transaction_dir / backup_dirname
        if not backup_root.is_dir() or target_root is None:
            continue
        for backup in sorted(backup_root.glob("*.json")):
            attempt(
                lambda backup=backup, target_root=target_root: _restore_missing_file(
                    backup, target_root / backup.name,
                )
            )

    index_target = _manifest_path(payload, "index_target", theater_root)
    story_id = str(payload.get("story_id") or "").strip()
    raw_slots = payload.get("index_story_slots")
    if index_target is not None and story_id and isinstance(raw_slots, dict) and raw_slots:
        def restore_index_slots() -> None:
            stories = _read_story_session_slots_or_quarantine(index_target)
            story_slots = stories.setdefault(story_id, {})
            for character_id, session_id in raw_slots.items():
                if str(character_id).strip() and str(session_id).strip():
                    # A slot written after the delete belongs to a newer session.
                    story_slots.setdefault(str(character_id), str(session_id))
            _write_story_session_slots(index_target, stories)

        attempt(restore_index_slots)

    if failures:
        raise failures[0]


def _read_story_session_slots_or_quarantine(index_path: Path) -> dict[str, dict[str, str]]:
    """Read the derived story-session index, moving a corrupt one aside for a rebuild."""

    try:
        return _read_story_session_slots(index_path)
    except NumericV2StoreError as exc:
        if not _is_story_session_index_content_error(exc):
            # Temporarily unreadable is not corrupt: fail closed, never move it.
            raise
        logger.warning(
            "Numeric v2 story-session index %s is corrupt (%s); quarantining and rebuilding it",
            index_path,
            exc,
        )
        _quarantine_session(
            index_path,
            index_path.parent / INDEX_QUARANTINE_DIRNAME,
            "corrupt",
        )
        return {}


def numeric_v2_story_recovery_pending(theater_root: Path, story_id: str) -> bool:
    """True while this process could not roll back an interrupted delete of ``story_id``."""

    key = str(Path(theater_root).resolve())
    return str(story_id or "").strip() in _RECOVERY_BLOCKED_STORIES.get(key, frozenset())


def recover_numeric_v2_delete_transactions(theater_root: Path) -> set[str]:
    """Roll back interrupted story deletes; return the stories whose rollback failed.

    A failed rollback is isolated: its transaction directory (the only backup)
    stays in place for manual recovery and the next startup, and the other
    transactions are still processed.
    """

    root = Path(theater_root) / "numeric_v2" / "delete_transactions"
    blocked: set[str] = set()
    if not root.is_dir():
        return blocked
    for transaction_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_path = transaction_dir / "manifest.json"
        if not manifest_path.is_file():
            # destructive 阶段只会在 prepared manifest 落盘后开始；这里仅是
            # 备份阶段中断留下的临时目录，可以直接清理。
            shutil.rmtree(transaction_dir, ignore_errors=True)
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != DELETE_TRANSACTION_SCHEMA:
            continue
        state = payload.get("state")
        if state == "prepared":
            if not _manifest_paths_resolvable(payload, theater_root):
                # A legacy absolute manifest from an unrecognised layout: restoring
                # could write outside the current root, and deleting would lose the
                # only backup. Keep it for manual recovery.
                logger.warning(
                    "Numeric v2 delete transaction %s names paths outside the current "
                    "theater root; leaving it in place",
                    transaction_dir,
                )
                continue
            try:
                _restore_delete_transaction(transaction_dir, payload, theater_root)
            except Exception:
                story_id = str(payload.get("story_id") or "").strip()
                logger.error(
                    "Numeric v2 could not roll back the interrupted delete %s of story %r; "
                    "keeping it for manual recovery and blocking that story",
                    transaction_dir,
                    story_id,
                    exc_info=True,
                )
                if story_id:
                    blocked.add(story_id)
                continue
        elif state not in _SETTLED_DELETE_TRANSACTION_STATES:
            # Unknown state: keep the backup rather than guess.
            logger.warning(
                "Numeric v2 delete transaction %s has unknown state %r; leaving it in place",
                transaction_dir,
                state,
            )
            continue
        shutil.rmtree(transaction_dir, ignore_errors=True)
    return blocked


CHARACTER_PURGE_INTENT_SCHEMA = "neko.theater.character-purge.v1"
CHARACTER_PURGE_INTENT_DIRNAME = "purge_intents"
# Only files in these numeric_v2 directories can belong to a deleted character;
# a purge intent naming anything else is never acted on.
_CHARACTER_PURGE_TARGET_DIRS = frozenset({
    "sessions",
    "public_archives",
    "end_receipts",
    "forget_transactions",
    RETRACT_INTENT_DIRNAME,
    FORGET_MARKER_DIRNAME,
    PUBLIC_ARCHIVE_QUARANTINE_DIRNAME,
    SESSION_QUARANTINE_DIRNAME,
})


class NumericV2PurgeIntentError(ValueError):
    """A character purge intent is malformed or names a path it may not delete."""


def character_purge_intent_path(
    theater_root: Path, character_id: str, legacy_catgirl_name: str,
) -> Path:
    """Return the intent file for one deleted character identity."""

    key = json.dumps(
        [str(character_id or "").strip(), str(legacy_catgirl_name or "").strip()],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
    return (
        Path(theater_root) / "numeric_v2" / CHARACTER_PURGE_INTENT_DIRNAME
        / f"purge_{digest}.json"
    )


def _character_purge_target(theater_root: Path, relative: Any) -> Path:
    """Map one intent target onto the theater root, refusing anything outside the purge dirs."""

    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise NumericV2PurgeIntentError("numeric_purge_intent_target_invalid")
    parts = PurePosixPath(relative).parts
    if (
        PurePosixPath(relative).is_absolute()
        or PureWindowsPath(relative).is_absolute()
        or len(parts) != 3
        or parts[0] != "numeric_v2"
        or parts[1] not in _CHARACTER_PURGE_TARGET_DIRS
        or parts[2] in {".", ".."}
        or not parts[2].endswith(".json")
    ):
        raise NumericV2PurgeIntentError("numeric_purge_intent_target_invalid")
    root = Path(theater_root)
    target = root.joinpath(*parts)
    allowed_dir = root.resolve().joinpath(*parts[:2])
    if target.resolve().parent != allowed_dir:
        # A symlinked file or directory must not redirect the delete elsewhere.
        raise NumericV2PurgeIntentError("numeric_purge_intent_target_invalid")
    return target


def write_character_purge_intent(
    theater_root: Path,
    *,
    character_id: str,
    legacy_catgirl_name: str,
    targets: list[Path] | tuple[Path, ...],
) -> Path:
    """Durably record the theater files a character delete is about to erase.

    Written before the character leaves characters.json. Every target must lie in
    a purge directory of this theater root, or this raises before anything is
    committed (the caller then aborts the delete).
    """

    root = Path(theater_root)
    relative_targets = []
    for target in targets:
        try:
            relative = Path(target).relative_to(root).as_posix()
        except ValueError as exc:
            raise NumericV2PurgeIntentError("numeric_purge_intent_target_invalid") from exc
        _character_purge_target(root, relative)
        if relative not in relative_targets:
            relative_targets.append(relative)
    path = character_purge_intent_path(root, character_id, legacy_catgirl_name)
    _atomic_write_manifest(path, {
        "schema": CHARACTER_PURGE_INTENT_SCHEMA,
        "character_id": str(character_id or "").strip(),
        "legacy_catgirl_name": str(legacy_catgirl_name or "").strip(),
        "targets": relative_targets,
        "created_at": time.time(),
    })
    return path


def discard_character_purge_intent(path: Path) -> None:
    """Remove an intent once its targets are gone (or its delete never committed)."""

    Path(path).unlink(missing_ok=True)


def _load_character_purge_intent(theater_root: Path, path: Path) -> dict[str, Any]:
    """Read and validate an intent the same way forget intents are scoped."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NumericV2PurgeIntentError("numeric_purge_intent_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != CHARACTER_PURGE_INTENT_SCHEMA
        or not isinstance(payload.get("character_id"), str)
        or not isinstance(payload.get("legacy_catgirl_name"), str)
        or not isinstance(payload.get("targets"), list)
        or character_purge_intent_path(
            theater_root, payload["character_id"], payload["legacy_catgirl_name"],
        ).name != Path(path).name
    ):
        raise NumericV2PurgeIntentError("numeric_purge_intent_invalid")
    return payload


def apply_character_purge_intent(theater_root: Path, path: Path) -> int:
    """Delete exactly the files an intent lists (missing ones are fine), then drop it."""

    payload = _load_character_purge_intent(theater_root, path)
    targets = [_character_purge_target(theater_root, item) for item in payload["targets"]]
    removed = 0
    for target in targets:
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    discard_character_purge_intent(path)
    return removed


def recover_character_purge_intents(
    theater_root: Path,
    character_ids_by_name: Mapping[str, str],
) -> dict[str, int]:
    """Retry character purges a previous process committed but could not finish.

    An intent whose character is still configured belongs to a delete that never
    committed (a crash between the intent write and characters.json), so it is
    discarded without deleting anything. Malformed intents are kept for manual
    inspection and never acted on.
    """

    root = Path(theater_root) / "numeric_v2" / CHARACTER_PURGE_INTENT_DIRNAME
    result = {"purge_intents_applied": 0, "purge_intents_discarded": 0}
    if not root.is_dir():
        return result
    live_ids = {str(value or "").strip() for value in character_ids_by_name.values()}
    live_names = {str(name or "").strip() for name in character_ids_by_name}
    for path in sorted(root.glob("purge_*.json")):
        try:
            payload = _load_character_purge_intent(theater_root, path)
            character_id = payload["character_id"].strip()
            legacy_name = payload["legacy_catgirl_name"].strip()
            still_configured = (
                character_id in live_ids if character_id else legacy_name in live_names
            )
            if still_configured:
                discard_character_purge_intent(path)
                result["purge_intents_discarded"] += 1
                continue
            apply_character_purge_intent(theater_root, path)
            result["purge_intents_applied"] += 1
        except NumericV2PurgeIntentError:
            logger.warning("Numeric v2 purge intent %s is invalid; leaving it in place", path)
        except OSError:
            logger.warning("Numeric v2 purge intent %s could not be completed; will retry", path, exc_info=True)
    return result


def _supersede_pending_delete_transactions(
    theater_root: Path, story_id: str, current_dir: Path,
) -> None:
    # A later committed delete of the same story supersedes an earlier one whose
    # rollback failed; replaying that stale backup would resurrect the story.
    root = Path(theater_root) / "numeric_v2" / "delete_transactions"
    try:
        transaction_dirs = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        logger.warning("Numeric v2 cannot scan delete transactions", exc_info=True)
        return
    for transaction_dir in transaction_dirs:
        if transaction_dir == current_dir:
            continue
        manifest_path = transaction_dir / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != DELETE_TRANSACTION_SCHEMA
                or payload.get("state") != "prepared"
                or payload.get("story_id") != story_id
            ):
                continue
            payload["state"] = "superseded"
            _atomic_write_manifest(manifest_path, payload)
        except (OSError, UnicodeError, json.JSONDecodeError):
            logger.warning(
                "Numeric v2 cannot supersede delete transaction %s", transaction_dir, exc_info=True,
            )


def _prepare_delete_transaction(
    theater_root: Path,
    registry: NumericV2PackageRegistry,
    story_id: str,
) -> tuple[Path, Path, dict[str, Any]]:
    package_target = registry.package_path(story_id)
    session_root = Path(theater_root) / "numeric_v2" / "sessions"
    public_archive_root = Path(theater_root) / "numeric_v2" / "public_archives"
    archive_store = NumericV2ArchiveStore(theater_root)
    index_target = Path(theater_root) / "numeric_v2" / "story_sessions.json"
    transaction_dir = (
        Path(theater_root)
        / "numeric_v2"
        / "delete_transactions"
        / f"{story_id}-{uuid.uuid4().hex}"
    )
    try:
        transaction_dir.mkdir(parents=True)
        shutil.copy2(package_target, transaction_dir / "package.json")
        session_backup_root = transaction_dir / "sessions"
        story_session_ids: set[str] = set()
        for summary in list_numeric_v2_sessions(
            theater_root,
            story_id=story_id,
            raise_on_io_error=True,
        ):
            story_session_ids.add(str(summary.get("session_id") or ""))
            session_backup_root.mkdir(parents=True, exist_ok=True)
            source = Path(summary["path"])
            shutil.copy2(source, session_backup_root / source.name)
        public_archive_backup_root = transaction_dir / "public_archives"
        for summary in list_numeric_v2_public_archives(
            theater_root,
            story_id=story_id,
            raise_on_io_error=True,
        ):
            public_archive_backup_root.mkdir(parents=True, exist_ok=True)
            source = Path(summary["path"])
            shutil.copy2(source, public_archive_backup_root / source.name)
        receipt_backup_root = transaction_dir / "end_receipts"
        for source in archive_store.receipt_paths_for_scope(story_id=story_id):
            if not source.is_file():
                continue
            receipt_backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, receipt_backup_root / source.name)
        index_stories = _read_story_session_slots(index_target)
        story_session_ids.update(str(value) for value in index_stories.get(story_id, {}).values())
        # Only quarantined archives attributable to this story are erased here: a
        # package delete is not a request to erase data whose owner is unknown.
        quarantined_archives = archive_store.quarantined_public_archive_paths(
            story_id=story_id,
            session_ids=story_session_ids,
        )
        quarantine_backup_root = transaction_dir / PUBLIC_ARCHIVE_QUARANTINE_DIRNAME
        for source in quarantined_archives:
            quarantine_backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, quarantine_backup_root / source.name)
        # Quarantined session files (full ledger) of this story follow the same
        # rule: attributable ones are erased with the package, unknown ones kept.
        quarantined_sessions = archive_store.quarantined_session_paths(
            story_id=story_id,
            session_ids=story_session_ids,
        )
        session_quarantine_backup_root = transaction_dir / _QUARANTINED_SESSION_BACKUP_DIRNAME
        for source in quarantined_sessions:
            session_quarantine_backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, session_quarantine_backup_root / source.name)
        manifest = {
            "schema": DELETE_TRANSACTION_SCHEMA,
            "state": "prepared",
            "story_id": story_id,
            # Relative to the theater root: a storage-root migration or cloud
            # restore moves the whole tree, and recovery must follow it.
            "package_target": _manifest_relative(theater_root, package_target),
            "session_root": _manifest_relative(theater_root, session_root),
            "public_archive_root": _manifest_relative(theater_root, public_archive_root),
            "receipt_root": _manifest_relative(theater_root, archive_store.root),
            "public_archive_quarantine_root": _manifest_relative(
                theater_root, archive_store.public_archive_quarantine_root,
            ),
            "quarantined_archive_files": [path.name for path in quarantined_archives],
            "session_quarantine_root": _manifest_relative(
                theater_root, archive_store.session_quarantine_root,
            ),
            "quarantined_session_files": [path.name for path in quarantined_sessions],
            "index_target": _manifest_relative(theater_root, index_target),
            "index_existed": index_target.is_file(),
            "index_story_slots": index_stories.get(story_id, {}),
        }
        manifest_path = transaction_dir / "manifest.json"
        _atomic_write_manifest(manifest_path, manifest)
        return transaction_dir, manifest_path, manifest
    except BaseException:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


async def delete_numeric_v2_story_transactionally(
    theater_root: Path,
    registry: NumericV2PackageRegistry,
    story_id: str,
    *,
    write_transaction=nullcontext,
) -> int:
    async with numeric_v2_session_files_guard(theater_root):
        return await run_storage_mutation(
            write_transaction, _delete_story_files, theater_root, registry, story_id,
        )


def _delete_story_files(theater_root: Path, registry: NumericV2PackageRegistry, story_id: str) -> int:
    # Backup, deletion and rollback share one cloud fence and one worker thread.
    try:
        transaction_dir, manifest_path, manifest = _prepare_delete_transaction(
            theater_root, registry, story_id,
        )
    except (OSError, NumericV2ArchiveError) as exc:
        raise NumericV2StoreError("numeric_story_delete_backup_failed") from exc
    try:
        deleted =_delete_numeric_v2_sessions_unlocked(theater_root, story_id=story_id)
        NumericV2ArchiveStore(theater_root).delete_receipts(story_id=story_id)
        archive_store = NumericV2ArchiveStore(theater_root)
        archive_store.delete_public_archives(story_id=story_id, character_id="")
        for name in manifest["quarantined_archive_files"]:
            # Backed up in the prepared transaction above, so a rollback restores it.
            (archive_store.public_archive_quarantine_root / name).unlink(missing_ok=True)
        for name in manifest["quarantined_session_files"]:
            (archive_store.session_quarantine_root / name).unlink(missing_ok=True)
        registry.delete_package(story_id)
        manifest["state"] = "committed"
        _atomic_write_manifest(manifest_path, manifest)
    except BaseException:
        try:
            _restore_delete_transaction(transaction_dir, manifest, theater_root)
        except Exception as rollback_exc:
            raise NumericV2StoreError("numeric_story_delete_rollback_failed") from rollback_exc
        # rmtree may silently leave the manifest behind (e.g. a Windows share
        # violation); settle it first so startup recovery never replays it.
        try:
            manifest["state"] = "rolled_back"
            _atomic_write_manifest(manifest_path, manifest)
        except OSError:
            logger.warning("Numeric v2 cannot mark delete rollback settled", exc_info=True)
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise
    _supersede_pending_delete_transactions(theater_root, story_id, transaction_dir)
    shutil.rmtree(transaction_dir, ignore_errors=True)
    return len(deleted)


def _quarantine_session(path: Path, quarantine_root: Path, reason: str) -> None:
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / (
        f"{reason}-{int(time.time() * 1000)}-{uuid.uuid4().hex}-{path.name}"
    )
    os.replace(path, target)
    try:
        # os.replace keeps the ledger's last write time; record when it was quarantined.
        os.utime(target)
    except OSError:
        logger.warning("Numeric v2 cannot refresh quarantine time of %s", target, exc_info=True)


def _caused_by_os_error(exc: BaseException) -> bool:
    """识别被业务异常包装的暂时性文件系统错误。"""  # noqa: DOCSTRING_CJK

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError):
            return True
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return False


def audit_numeric_v2_storage(
    theater_root: Path,
    registry: NumericV2PackageRegistry,
    *,
    character_ids_by_name: Mapping[str, str] | None = None,
    skip_story_ids: frozenset[str] | set[str] = frozenset(),
) -> dict[str, int]:
    """启动/维护时全盘复验；日常恢复路径不扫描 Session 目录。"""  # noqa: DOCSTRING_CJK

    session_root = Path(theater_root) / "numeric_v2" / "sessions"
    quarantine_root = Path(theater_root) / "numeric_v2" / SESSION_QUARANTINE_DIRNAME
    index_path = Path(theater_root) / "numeric_v2" / "story_sessions.json"
    known_characters = {
        str(name).strip(): str(character_id).strip()
        for name, character_id in (character_ids_by_name or {}).items()
        if str(name).strip() and str(character_id).strip()
    }
    known_character_ids = set(known_characters.values())
    if not session_root.is_dir():
        if index_path.is_file():
            _write_story_session_slots(index_path, {})
        return {"valid": 0, "quarantined": 0}

    valid: list[tuple[Path, dict[str, str], int, int, str]] = []
    quarantined = 0
    engine_cache: dict[str, Any] = {}
    # Stories with an unfinished delete rollback are left exactly as they are.
    unloadable_stories: set[str] = set(skip_story_ids)
    for path in sorted(session_root.glob("*.json")):
        try:
            summary = _read_numeric_v2_session_summary(
                path,
                raise_on_io_error=True,
            )
            if summary is None or summary["session_id"] != path.stem:
                raise NumericV2StoreError("numeric_session_summary_invalid")
            story_id = summary["story_id"]
            if story_id in unloadable_stories:
                continue
            if story_id not in engine_cache:
                package_path = registry.package_path(story_id)
                try:
                    package_path.stat()
                except FileNotFoundError:
                    # 已删除剧本留下的孤儿 Session 属于可确定的数据失效，不应当作暂时性 I/O 故障。
                    raise NumericV2StoreError(
                        "numeric_session_story_missing"
                    ) from None
                try:
                    engine_cache[story_id] = registry.load_engine(story_id)
                except NumericV2PackageNotFoundError:
                    raise
                except NumericV2PackageError as exc:
                    if _caused_by_os_error(exc):
                        raise
                    # An unusable package says nothing about the validity of its saves.
                    unloadable_stories.add(story_id)
                    continue
            store = NumericV2SessionStore(theater_root, engine_cache[story_id])
            stored = store._read(path)
            try:
                store._validate_chain(stored)
            except NumericV2RuntimeError as exc:
                if str(exc) not in {
                    "story_package_revision_mismatch",
                    "story_package_hash_mismatch",
                }:
                    raise
                # 合法旧 Session 不能因剧本升级被隔离；保留它供用户结束或删除，
                # 日常恢复仍会走严格重放并拒绝继续旧版本剧情。
                store._validate_lifecycle_chain(stored)
            effective_character_id = summary["character_id"] or known_characters.get(
                summary["catgirl_name"],
                "",
            )
            if known_characters and effective_character_id not in known_character_ids:
                raise NumericV2StoreError("numeric_session_character_missing")
            if not effective_character_id:
                raise NumericV2StoreError("numeric_session_character_unresolved")
            valid.append(
                (
                    path,
                    summary,
                    stored.session.revision,
                    path.stat().st_mtime_ns,
                    effective_character_id,
                )
            )
        except (NumericV2StoreError, NumericV2RuntimeError, NumericV2PackageError, NumericV2PackageNotFoundError, OSError) as exc:
            if _caused_by_os_error(exc):
                # 权限、挂载或设备故障可能只是暂时状态；本轮中止，绝不移动仍可能有效的数据。
                raise NumericV2StoreError(
                    "numeric_session_audit_read_failed"
                ) from exc
            try:
                _quarantine_session(path, quarantine_root, "invalid")
                quarantined += 1
            except OSError:
                logger.warning(
                    "Numeric v2 无法隔离异常 Session %s: %s",
                    path,
                    exc,
                    exc_info=True,
                )

    # 索引是派生缓存：内容损坏时隔离后按 Session 文件重建；暂时性 I/O 故障仍中止本轮。
    old_index = _read_story_session_slots_or_quarantine(index_path)
    slots: dict[
        tuple[str, str],
        list[tuple[Path, dict[str, str], int, int, str]],
    ] = {}
    for item in valid:
        slots.setdefault((item[1]["story_id"], item[4]), []).append(item)

    rebuilt: dict[str, dict[str, str]] = {
        story_id: dict(old_index[story_id])
        for story_id in unloadable_stories if story_id in old_index
    }
    for (story_id, character_id), candidates in slots.items():
        indexed_id = old_index.get(story_id, {}).get(character_id, "")
        selected = next(
            (item for item in candidates if item[1]["session_id"] == indexed_id),
            None,
        ) or max(
            candidates,
            key=lambda item: (
                item[1]["status"] != "ended",
                item[2],
                item[3],
                item[1]["session_id"],
            ),
        )
        rebuilt.setdefault(story_id, {})[character_id] = selected[1]["session_id"]
        for duplicate in candidates:
            if duplicate[0] == selected[0]:
                continue
            try:
                _quarantine_session(duplicate[0], quarantine_root, "duplicate")
                quarantined += 1
            except OSError:
                logger.warning(
                    "Numeric v2 无法隔离重复 Session: %s",
                    duplicate[0],
                    exc_info=True,
                )

    _write_story_session_slots(index_path, rebuilt)
    return {"valid": sum(len(slots) for story_id, slots in rebuilt.items() if story_id not in unloadable_stories), "quarantined": quarantined}


def maintain_numeric_v2_storage_once(
    theater_root: Path,
    registry: NumericV2PackageRegistry,
    *,
    character_ids_by_name: Mapping[str, str] | Callable[[], Mapping[str, str]],
    assert_writable: Callable[[], None] | None = None,
    write_transaction=nullcontext,
) -> dict[str, int] | None:
    """每个运行根仅在冷启动初始化时执行一次恢复和全盘核查。"""  # noqa: DOCSTRING_CJK

    key = str(Path(theater_root).resolve())
    with _MAINTENANCE_LOCK:
        if key in _MAINTAINED_ROOTS:
            return None
        if callable(character_ids_by_name):
            character_ids_by_name = character_ids_by_name()
        with write_transaction():
            # 冷启动恢复、默认包安装和索引重建都会写盘，必须服从与云存档相同的写栅栏。
            if assert_writable is not None:
                assert_writable()
            blocked_stories = frozenset(recover_numeric_v2_delete_transactions(theater_root))
            _RECOVERY_BLOCKED_STORIES[key] = blocked_stories
            # Finish character purges a previous process committed but could not
            # complete, before the audit rebuilds the session index without them.
            purge_result = recover_character_purge_intents(
                theater_root, character_ids_by_name,
            )
            registry.ensure_default_packages()
            result = audit_numeric_v2_storage(
                theater_root,
                registry,
                character_ids_by_name=character_ids_by_name,
                skip_story_ids=blocked_stories,
            )
            if blocked_stories:
                result["recovery_blocked_stories"] = sorted(blocked_stories)
            result.update({key: value for key, value in purge_result.items() if value})
            active_session_ids = {
                item["session_id"]
                for item in list_numeric_v2_sessions(theater_root)
            }
            archive_store = NumericV2ArchiveStore(theater_root)
            # A blocked story's sessions may be missing (they sit in its transaction
            # backup), so ownership judged from sessions on disk would delete its
            # receipts and queue retractions of its memory; leave it untouched.
            result.update(archive_store.cleanup_receipts(
                active_session_ids, skip_story_ids=blocked_stories,
            ))
            # 损坏的公开冷档案会让角色改名/删除的严格快照对所有角色失败；
            # 与坏档 Session 一样移入隔离区，但使用独立目录，不参与数量裁剪删除。
            archives_quarantined = archive_store.quarantine_invalid_public_archives(
                Path(theater_root) / "numeric_v2" / PUBLIC_ARCHIVE_QUARANTINE_DIRNAME,
                skip_story_ids=blocked_stories,
            )
            if archives_quarantined:
                logger.warning("Numeric v2 已隔离 %d 份无法解析的公开冷档案", archives_quarantined)
                result["archives_quarantined"] = archives_quarantined
            _MAINTAINED_ROOTS.add(key)
            return result


__all__ = [
    "CHARACTER_PURGE_INTENT_SCHEMA",
    "INDEX_QUARANTINE_DIRNAME",
    "NumericV2PurgeIntentError",
    "PUBLIC_ARCHIVE_QUARANTINE_DIRNAME",
    "apply_character_purge_intent",
    "audit_numeric_v2_storage",
    "character_purge_intent_path",
    "discard_character_purge_intent",
    "delete_numeric_v2_story_transactionally",
    "maintain_numeric_v2_storage_once",
    "numeric_v2_story_recovery_pending",
    "recover_character_purge_intents",
    "recover_numeric_v2_delete_transactions",
    "write_character_purge_intent",
]
