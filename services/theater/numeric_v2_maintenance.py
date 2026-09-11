"""Numeric v2 启动核查和可恢复剧本删除事务。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from .numeric_v2_archive import NumericV2ArchiveStore
from .numeric_v2_registry import NumericV2PackageRegistry, NumericV2PackageError, NumericV2PackageNotFoundError
from .numeric_v2_runtime import NumericV2RuntimeError
from .numeric_v2_store import (
    NumericV2SessionStore,
    NumericV2StoreError,
    _read_numeric_v2_session_summary,
    _read_story_session_slots,
    _write_story_session_slots,
    delete_numeric_v2_sessions,
    list_numeric_v2_public_archives,
    list_numeric_v2_sessions,
)


QUARANTINE_FILE_LIMIT = 6
DELETE_TRANSACTION_SCHEMA = "neko.script.delete_transaction.numeric.v2"

_MAINTENANCE_LOCK = threading.Lock()
_MAINTAINED_ROOTS: set[str] = set()
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
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _manifest_path(payload: Mapping[str, Any], key: str) -> Path | None:
    raw = str(payload.get(key) or "").strip()
    return Path(raw) if raw else None


def _restore_delete_transaction(transaction_dir: Path, payload: Mapping[str, Any]) -> None:
    package_backup = transaction_dir / "package.json"
    package_target = _manifest_path(payload, "package_target")
    if package_backup.is_file() and package_target is not None:
        package_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package_backup, package_target)

    session_root = _manifest_path(payload, "session_root")
    session_backup_root = transaction_dir / "sessions"
    if session_backup_root.is_dir() and session_root is not None:
        session_root.mkdir(parents=True, exist_ok=True)
        for backup in session_backup_root.glob("*.json"):
            shutil.copy2(backup, session_root / backup.name)

    public_archive_root = _manifest_path(payload, "public_archive_root")
    public_archive_backup_root = transaction_dir / "public_archives"
    if public_archive_backup_root.is_dir() and public_archive_root is not None:
        public_archive_root.mkdir(parents=True, exist_ok=True)
        for backup in public_archive_backup_root.glob("*.json"):
            shutil.copy2(backup, public_archive_root / backup.name)

    receipt_root = _manifest_path(payload, "receipt_root")
    receipt_backup_root = transaction_dir / "end_receipts"
    if receipt_backup_root.is_dir() and receipt_root is not None:
        receipt_root.mkdir(parents=True, exist_ok=True)
        for backup in receipt_backup_root.glob("*.json"):
            shutil.copy2(backup, receipt_root / backup.name)

    index_target = _manifest_path(payload, "index_target")
    story_id = str(payload.get("story_id") or "").strip()
    if index_target is not None and story_id:
        stories = _read_story_session_slots(index_target)
        raw_slots = payload.get("index_story_slots")
        if isinstance(raw_slots, dict) and raw_slots:
            stories[story_id] = {
                str(character_id): str(session_id)
                for character_id, session_id in raw_slots.items()
                if str(character_id).strip() and str(session_id).strip()
            }
        else:
            stories.pop(story_id, None)
        if stories or payload.get("index_existed") is True:
            _write_story_session_slots(index_target, stories)
        elif index_target.exists():
            index_target.unlink()


def recover_numeric_v2_delete_transactions(theater_root: Path) -> None:
    root = Path(theater_root) / "numeric_v2" / "delete_transactions"
    if not root.is_dir():
        return
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
        if payload.get("schema") != DELETE_TRANSACTION_SCHEMA:
            continue
        if payload.get("state") != "committed":
            _restore_delete_transaction(transaction_dir, payload)
        shutil.rmtree(transaction_dir, ignore_errors=True)


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
        for summary in list_numeric_v2_sessions(
            theater_root,
            story_id=story_id,
            raise_on_io_error=True,
        ):
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
        manifest = {
            "schema": DELETE_TRANSACTION_SCHEMA,
            "state": "prepared",
            "story_id": story_id,
            "package_target": str(package_target),
            "session_root": str(session_root),
            "public_archive_root": str(public_archive_root),
            "receipt_root": str(archive_store.root),
            "index_target": str(index_target),
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
) -> int:
    """删除剧本包、槽位和索引；任一步失败都恢复删除前快照。"""  # noqa: DOCSTRING_CJK

    try:
        transaction_dir, manifest_path, manifest = await asyncio.to_thread(
            _prepare_delete_transaction,
            theater_root,
            registry,
            story_id,
        )
    except OSError as exc:
        raise NumericV2StoreError("numeric_story_delete_backup_failed") from exc
    try:
        deleted = await delete_numeric_v2_sessions(theater_root, story_id=story_id)
        await asyncio.to_thread(
            NumericV2ArchiveStore(theater_root).delete_receipts,
            story_id=story_id,
        )
        # 完整公开演绎已经纳入事务快照；删除剧本时必须同步移除，失败则由下方统一回滚。
        await asyncio.to_thread(
            NumericV2ArchiveStore(theater_root).delete_public_archives,
            story_id=story_id,
            character_id="",
        )
        # Registry 删除包含文件替换，必须离开事件循环执行。
        await asyncio.to_thread(registry.delete_package, story_id)
        manifest["state"] = "committed"
        await asyncio.to_thread(_atomic_write_manifest, manifest_path, manifest)
    except BaseException:
        try:
            await asyncio.to_thread(
                _restore_delete_transaction,
                transaction_dir,
                manifest,
            )
        except OSError as rollback_exc:
            raise NumericV2StoreError(
                "numeric_story_delete_rollback_failed"
            ) from rollback_exc
        finally:
            await asyncio.to_thread(shutil.rmtree, transaction_dir, True)
        raise
    await asyncio.to_thread(shutil.rmtree, transaction_dir, True)
    return len(deleted)


def _quarantine_session(path: Path, quarantine_root: Path, reason: str) -> None:
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / (
        f"{reason}-{int(time.time() * 1000)}-{uuid.uuid4().hex}-{path.name}"
    )
    os.replace(path, target)


def _trim_quarantine(quarantine_root: Path) -> None:
    if not quarantine_root.is_dir():
        return
    files = sorted(
        (path for path in quarantine_root.iterdir() if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in files[QUARANTINE_FILE_LIMIT:]:
        stale.unlink()


def _trim_quarantine_safely(quarantine_root: Path) -> None:
    try:
        _trim_quarantine(quarantine_root)
    except OSError:
        logger.warning("Numeric v2 隔离区裁剪失败", exc_info=True)


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
) -> dict[str, int]:
    """启动/维护时全盘复验；日常恢复路径不扫描 Session 目录。"""  # noqa: DOCSTRING_CJK

    session_root = Path(theater_root) / "numeric_v2" / "sessions"
    quarantine_root = Path(theater_root) / "numeric_v2" / "quarantine"
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
        _trim_quarantine_safely(quarantine_root)
        return {"valid": 0, "quarantined": 0}

    valid: list[tuple[Path, dict[str, str], int, int, str]] = []
    quarantined = 0
    engine_cache: dict[str, Any] = {}
    unloadable_stories: set[str] = set()
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

    old_index = _read_story_session_slots(index_path)
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
    _trim_quarantine_safely(quarantine_root)
    return {"valid": sum(len(slots) for story_id, slots in rebuilt.items() if story_id not in unloadable_stories), "quarantined": quarantined}


def maintain_numeric_v2_storage_once(
    theater_root: Path,
    registry: NumericV2PackageRegistry,
    *,
    character_ids_by_name: Mapping[str, str],
    assert_writable: Callable[[], None] | None = None,
) -> dict[str, int] | None:
    """每个运行根仅在冷启动初始化时执行一次恢复和全盘核查。"""  # noqa: DOCSTRING_CJK

    key = str(Path(theater_root).resolve())
    with _MAINTENANCE_LOCK:
        if key in _MAINTAINED_ROOTS:
            return None
        # 冷启动恢复、默认包安装和索引重建都会写盘，必须服从与云存档相同的写栅栏。
        if assert_writable is not None:
            assert_writable()
        recover_numeric_v2_delete_transactions(theater_root)
        registry.ensure_default_packages()
        result = audit_numeric_v2_storage(
            theater_root,
            registry,
            character_ids_by_name=character_ids_by_name,
        )
        active_session_ids = {
            item["session_id"]
            for item in list_numeric_v2_sessions(theater_root)
        }
        result.update(
            NumericV2ArchiveStore(theater_root).cleanup_receipts(
                active_session_ids
            )
        )
        _MAINTAINED_ROOTS.add(key)
        return result


__all__ = [
    "QUARANTINE_FILE_LIMIT",
    "audit_numeric_v2_storage",
    "delete_numeric_v2_story_transactionally",
    "maintain_numeric_v2_storage_once",
    "recover_numeric_v2_delete_transactions",
]
