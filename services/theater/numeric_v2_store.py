"""Numeric v2 Session 与 Ledger 的原子文件存储。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, TYPE_CHECKING
from weakref import WeakValueDictionary

from .numeric_v2_performance import (
    transition_source_dialogue_policy,
    valid_mixed_performance_policy,
    valid_ordered_content,
    valid_scene_narration,
)

if TYPE_CHECKING:
    from .numeric_v2_runtime import NumericV2Engine, ScriptSessionV2


STORE_SCHEMA = "neko.script.store.numeric.v2"
STORY_SESSION_INDEX_SCHEMA = "neko.script.story_session_index.numeric.v2.character-slots"


class NumericV2StoreError(ValueError):
    """Numeric v2 存档无法安全读取或提交。"""  # noqa: DOCSTRING_CJK


class NumericV2SessionExistsError(NumericV2StoreError):
    pass


class NumericV2SessionNotFoundError(NumericV2StoreError):
    pass


class NumericV2StoreRevisionConflictError(NumericV2StoreError):
    pass


@dataclass(frozen=True, slots=True)
class NumericV2StoredSession:
    session: "ScriptSessionV2"
    ledger_events: tuple[dict[str, Any], ...]


# 协程持有或等待锁时会保留强引用；空闲后由弱引用表自动回收，避免 Session/剧本 ID 无限积累。
_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_STORY_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _lock(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    lock = _LOCKS.get(key)
    if lock is None:
        # 新锁必须先由局部变量强持有，再登记弱引用；否则创建与返回之间就可能被立即回收。
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _story_lock(path: Path, story_id: str) -> asyncio.Lock:
    key = f"{path.resolve()}::{story_id}"
    lock = _STORY_LOCKS.get(key)
    if lock is None:
        # 与 Session 文件锁保持相同生命周期，调用方进入 async with 前始终持有强引用。
        lock = asyncio.Lock()
        _STORY_LOCKS[key] = lock
    return lock


@asynccontextmanager
async def numeric_v2_story_session_guard(
    theater_storage_root: Path,
    story_id: str,
):
    """在不加载剧本包的情况下复用指定剧本的 Session 生命周期锁。"""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    if not normalized_story_id:
        raise NumericV2StoreError("numeric_story_id_required")
    index_path = Path(theater_storage_root) / "numeric_v2" / "story_sessions.json"
    async with _story_lock(index_path, normalized_story_id):
        yield


def _read_story_session_slots(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # 已存在但不可读的索引不能等同于全新空索引，否则任一写请求都会覆盖全部恢复槽位。
        raise NumericV2StoreError("numeric_story_session_index_read_failed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STORY_SESSION_INDEX_SCHEMA:
        raise NumericV2StoreError("numeric_story_session_index_invalid")
    stories = payload.get("stories")
    if not isinstance(stories, dict):
        raise NumericV2StoreError("numeric_story_session_index_invalid")
    normalized: dict[str, dict[str, str]] = {}
    for story_id, slots in stories.items():
        normalized_story_id = str(story_id or "").strip()
        if not normalized_story_id or not isinstance(slots, dict):
            continue
        normalized_slots = {
            str(catgirl_name).strip(): str(session_id).strip()
            for catgirl_name, session_id in slots.items()
            if str(catgirl_name).strip() and str(session_id).strip()
        }
        if normalized_slots:
            normalized[normalized_story_id] = normalized_slots
    return normalized


def _write_story_session_slots(
    path: Path,
    stories: Mapping[str, Mapping[str, str]],
) -> None:
    encoded = json.dumps(
        {
            "schema": STORY_SESSION_INDEX_SCHEMA,
            "stories": {
                str(story_id): dict(slots)
                for story_id, slots in stories.items()
                if slots
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json_payload(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _numeric_v2_session_root(theater_storage_root: Path) -> Path:
    return Path(theater_storage_root) / "numeric_v2" / "sessions"


def _numeric_v2_public_archive_root(theater_storage_root: Path) -> Path:
    return Path(theater_storage_root) / "numeric_v2" / "public_archives"


def _session_matches_character(
    binding: Mapping[str, Any],
    character_id: str,
    legacy_catgirl_name: str = "",
) -> bool:
    stored_character_id = str(binding.get("character_id") or "").strip()
    if stored_character_id:
        return stored_character_id == character_id
    return bool(
        legacy_catgirl_name
        and str(binding.get("catgirl_name") or "").strip() == legacy_catgirl_name
    )


def _read_numeric_v2_session_summary(
    path: Path,
    *,
    raise_on_io_error: bool = False,
) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # 日常列表保持旧有容错；启动审计则必须区分暂时不可读与内容损坏。
        if raise_on_io_error:
            raise
        return None
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != STORE_SCHEMA:
        return None
    raw_session = payload.get("session")
    binding = raw_session.get("catgirl_binding") if isinstance(raw_session, dict) else None
    if not isinstance(raw_session, dict) or not isinstance(binding, dict):
        return None
    return {
        "session_id": str(raw_session.get("session_id") or path.stem),
        "story_id": str(raw_session.get("story_package_id") or "").strip(),
        "catgirl_name": str(binding.get("catgirl_name") or "").strip(),
        "character_id": str(binding.get("character_id") or "").strip(),
        "status": str(raw_session.get("status") or "active"),
        "path": str(path),
    }


def list_numeric_v2_sessions(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
    raise_on_io_error: bool = False,
) -> list[dict[str, str]]:
    """按剧本或角色列出可识别的 Numeric v2 Session。"""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    root = _numeric_v2_session_root(theater_storage_root)
    if not root.is_dir():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        summary = _read_numeric_v2_session_summary(
            path,
            raise_on_io_error=raise_on_io_error,
        )
        if summary is None:
            continue
        if normalized_story_id and summary["story_id"] != normalized_story_id:
            continue
        if normalized_character_id:
            binding_matches = summary["character_id"] == normalized_character_id or (
                not summary["character_id"]
                and normalized_legacy_name
                and summary["catgirl_name"] == normalized_legacy_name
            )
        elif normalized_legacy_name:
            # 旧角色卡没有 character_id 时，只能按角色名收窄；绝不能把空 ID 解释为“全部角色”。
            binding_matches = summary["catgirl_name"] == normalized_legacy_name
        else:
            binding_matches = True
        if not binding_matches:
            continue
        result.append(summary)
    return result


def list_numeric_v2_public_archives(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
    raise_on_io_error: bool = False,
) -> list[dict[str, str]]:
    """列出与剧本或角色匹配的公开演绎冷档案。"""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    root = _numeric_v2_public_archive_root(theater_storage_root)
    if not root.is_dir():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            # 展示列表保持容错；删除事务必须把暂时不可读视为失败，不能遗漏用户档案。
            if raise_on_io_error:
                raise
            continue
        except (UnicodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "neko.theater.numeric.v2.public-archive"
        ):
            continue
        summary = {
            "session_id": str(payload.get("session_id") or "").strip(),
            "story_id": str(payload.get("story_id") or "").strip(),
            "catgirl_name": str(payload.get("catgirl_name") or "").strip(),
            "character_id": str(payload.get("character_id") or "").strip(),
            "path": str(path),
        }
        if normalized_story_id and summary["story_id"] != normalized_story_id:
            continue
        if normalized_character_id and not (
            summary["character_id"] == normalized_character_id
            or (
                not summary["character_id"]
                and normalized_legacy_name
                and summary["catgirl_name"] == normalized_legacy_name
            )
        ):
            continue
        if (
            not normalized_character_id
            and normalized_legacy_name
            and summary["catgirl_name"] != normalized_legacy_name
        ):
            continue
        result.append(summary)
    return result


async def delete_numeric_v2_sessions(
    theater_storage_root: Path,
    *,
    story_id: str = "",
    character_id: str = "",
    legacy_catgirl_name: str = "",
) -> list[dict[str, str]]:
    """删除指定剧本或角色的 Session，并同步清理恢复索引。"""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_character_id = str(character_id or "").strip()
    normalized_legacy_name = str(legacy_catgirl_name or "").strip()
    if (
        not normalized_story_id
        and not normalized_character_id
        and not normalized_legacy_name
    ):
        raise NumericV2StoreError("numeric_session_delete_scope_required")
    session_root = _numeric_v2_session_root(theater_storage_root)
    index_path = session_root.parent / "story_sessions.json"
    async with _lock(index_path):
        # 索引不可读时必须在删除任何 Session 或冷档案之前失败。
        stories = _read_story_session_slots(index_path)
        try:
            candidates = list_numeric_v2_sessions(
                theater_storage_root,
                story_id=normalized_story_id,
                character_id=normalized_character_id,
                legacy_catgirl_name=normalized_legacy_name,
                raise_on_io_error=True,
            )
        except OSError as exc:
            raise NumericV2StoreError("numeric_session_read_failed") from exc
        try:
            archive_candidates = list_numeric_v2_public_archives(
                theater_storage_root,
                story_id=normalized_story_id,
                character_id=normalized_character_id,
                legacy_catgirl_name=normalized_legacy_name,
                raise_on_io_error=True,
            )
        except OSError as exc:
            raise NumericV2StoreError("numeric_public_archive_read_failed") from exc
        deleted: list[dict[str, str]] = []
        for candidate in candidates:
            path = Path(candidate["path"])
            async with _lock(path):
                try:
                    current = _read_numeric_v2_session_summary(
                        path,
                        raise_on_io_error=True,
                    )
                except OSError as exc:
                    raise NumericV2StoreError("numeric_session_read_failed") from exc
                if current is None:
                    continue
                if normalized_story_id and current["story_id"] != normalized_story_id:
                    continue
                if normalized_character_id:
                    binding_matches = current["character_id"] == normalized_character_id or (
                        not current["character_id"]
                        and normalized_legacy_name
                        and current["catgirl_name"] == normalized_legacy_name
                    )
                elif normalized_legacy_name:
                    binding_matches = current["catgirl_name"] == normalized_legacy_name
                else:
                    binding_matches = True
                if not binding_matches:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                deleted.append(candidate)

        # 公开冷档案与对应剧本/角色同生命周期；删除恢复槽位时不能留下孤儿文件。
        for archive in archive_candidates:
            path = Path(archive["path"])
            async with _lock(path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

        if (
            normalized_story_id
            and not normalized_character_id
            and not normalized_legacy_name
        ):
            stories.pop(normalized_story_id, None)
        if normalized_character_id:
            story_ids = (
                [normalized_story_id]
                if normalized_story_id
                else list(stories)
            )
            for current_story_id in story_ids:
                if current_story_id not in stories:
                    continue
                stories[current_story_id].pop(normalized_character_id, None)
                if not stories[current_story_id]:
                    stories.pop(current_story_id)
        if normalized_legacy_name:
            deleted_session_ids = {item["session_id"] for item in deleted}
            for current_story_id in list(stories):
                stories[current_story_id] = {
                    current_character_id: current_session_id
                    for current_character_id, current_session_id in stories[
                        current_story_id
                    ].items()
                    if current_session_id not in deleted_session_ids
                }
                if not stories[current_story_id]:
                    stories.pop(current_story_id)
        if index_path.is_file() or stories:
            _write_story_session_slots(index_path, stories)
        return deleted


async def update_numeric_v2_character_bindings(
    theater_storage_root: Path,
    *,
    character_id: str,
    legacy_catgirl_name: str,
    catgirl_binding: Mapping[str, Any],
) -> int:
    """角色卡改名时保留所有剧本进度，并更新持久化身份投影。"""  # noqa: DOCSTRING_CJK

    normalized_character_id = str(character_id or "").strip()
    if not normalized_character_id:
        raise NumericV2StoreError("numeric_character_id_required")
    session_root = _numeric_v2_session_root(theater_storage_root)
    index_path = session_root.parent / "story_sessions.json"
    candidates = list_numeric_v2_sessions(
        theater_storage_root,
        character_id=normalized_character_id,
        legacy_catgirl_name=legacy_catgirl_name,
    )
    async with _lock(index_path):
        stories = _read_story_session_slots(index_path)
        updated = 0
        for candidate in candidates:
            path = Path(candidate["path"])
            async with _lock(path):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise NumericV2StoreError("numeric_session_read_failed") from exc
                raw_session = payload.get("session") if isinstance(payload, dict) else None
                if not isinstance(raw_session, dict):
                    raise NumericV2StoreError("numeric_session_payload_invalid")
                existing_binding = raw_session.get("catgirl_binding")
                refreshed_binding = {
                    str(key): str(value)
                    for key, value in catgirl_binding.items()
                }
                if isinstance(existing_binding, Mapping):
                    # 历史 Ledger 按该 Session 当时的称呼事实重放；角色改名只能刷新猫娘展示字段。
                    refreshed_binding["player_address"] = str(
                        existing_binding.get("player_address") or ""
                    )
                raw_session["catgirl_binding"] = refreshed_binding
                _atomic_write_json_payload(path, payload)
                story_id = str(raw_session.get("story_package_id") or "").strip()
                session_id = str(raw_session.get("session_id") or path.stem).strip()
                if story_id and session_id:
                    stories.setdefault(story_id, {})[
                        normalized_character_id
                    ] = session_id
                updated += 1
        _write_story_session_slots(index_path, stories)
        return updated


class NumericV2SessionStore:
    """每个 Session 一个文件，所有提交都先复验 revision 再原子替换。"""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path, engine: "NumericV2Engine"):
        self.root = Path(root) / "numeric_v2" / "sessions"
        self.engine = engine

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in session_id):
            raise NumericV2StoreError("numeric_session_id_invalid")
        return self.root / f"{session_id}.json"

    @property
    def _story_session_index_path(self) -> Path:
        return self.root.parent / "story_sessions.json"

    @asynccontextmanager
    async def story_session_guard(self, story_id: str):
        async with numeric_v2_story_session_guard(self.root.parent.parent, story_id):
            yield

    def _read_story_session_index(self) -> dict[str, dict[str, str]]:
        return _read_story_session_slots(self._story_session_index_path)

    def _write_story_session_index(
        self,
        stories: Mapping[str, Mapping[str, str]],
    ) -> None:
        _write_story_session_slots(self._story_session_index_path, stories)

    async def get_story_session_id(self, story_id: str, character_id: str) -> str:
        async with _lock(self._story_session_index_path):
            stories = self._read_story_session_index()
            return stories.get(str(story_id or "").strip(), {}).get(
                str(character_id or "").strip(),
                "",
            )

    async def set_story_session_id(
        self,
        story_id: str,
        character_id: str,
        session_id: str,
    ) -> None:
        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if (
            not normalized_story_id
            or not normalized_character_id
            or not normalized_session_id
        ):
            raise NumericV2StoreError("numeric_story_session_index_invalid")
        async with _lock(self._story_session_index_path):
            stories = self._read_story_session_index()
            stories.setdefault(normalized_story_id, {})[
                normalized_character_id
            ] = normalized_session_id
            self._write_story_session_index(stories)

    async def restore_story_session(
        self,
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> NumericV2StoredSession | None:
        """按剧本和猫娘从恢复索引读取唯一 Session。"""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        if not normalized_story_id or not normalized_character_id:
            return None
        async with self.story_session_guard(normalized_story_id):
            return await self._restore_story_session_unlocked(
                normalized_story_id,
                normalized_character_id,
                normalized_legacy_name,
            )

    async def _restore_story_session_unlocked(
        self,
        normalized_story_id: str,
        normalized_character_id: str,
        normalized_legacy_name: str = "",
    ) -> NumericV2StoredSession | None:
        session_id = await self.get_story_session_id(
            normalized_story_id,
            normalized_character_id,
        )
        if session_id:
            try:
                indexed = await self.load(session_id)
            except NumericV2StoreError as exc:
                # JSON、合同或账本损坏可由冷启动审计修复；暂时性 I/O 故障必须交给调用方重试。
                if isinstance(exc.__cause__, OSError):
                    raise
                indexed = None
            if (
                indexed is not None
                and indexed.session.story_package_id == normalized_story_id
                and _session_matches_character(
                    indexed.session.catgirl_binding,
                    normalized_character_id,
                    normalized_legacy_name,
                )
            ):
                return indexed
        return None

    async def create(self, session: "ScriptSessionV2") -> NumericV2StoredSession:
        path = self._path(session.session_id)
        async with _lock(path):
            if path.exists():
                raise NumericV2SessionExistsError("numeric_session_exists")
            self.engine.validate_session(session)
            stored = NumericV2StoredSession(session, ())
            self._write(path, stored, exclusive=True)
            return stored

    async def create_isolated_snapshot(
        self,
        stored: NumericV2StoredSession,
    ) -> NumericV2StoredSession:
        """原子写入已重放快照，但不发布到剧本恢复槽位，供压测分叉使用。"""  # noqa: DOCSTRING_CJK

        path = self._path(stored.session.session_id)
        async with _lock(path):
            if path.exists():
                raise NumericV2SessionExistsError("numeric_session_exists")
            # 先在内存中验证完整账本，再一次落盘；失败时不会留下半条分叉链。
            self._validate_chain(stored)
            self._write(path, stored, exclusive=True)
            return stored

    async def create_story_session(
        self,
        session: "ScriptSessionV2",
    ) -> NumericV2StoredSession:
        """原子创建 Session 文件并发布对应剧本恢复槽位。"""  # noqa: DOCSTRING_CJK

        path = self._path(session.session_id)
        index_path = self._story_session_index_path
        character_id = str(session.catgirl_binding.get("character_id") or "").strip()
        if not character_id:
            raise NumericV2StoreError("numeric_story_session_index_invalid")
        async with _lock(index_path):
            async with _lock(path):
                if path.exists():
                    raise NumericV2SessionExistsError("numeric_session_exists")
                self.engine.validate_session(session)
                stored = NumericV2StoredSession(session, ())
                stories = self._read_story_session_index()
                stories.setdefault(session.story_package_id, {})[
                    character_id
                ] = session.session_id
                self._write(path, stored, exclusive=True)
                try:
                    self._write_story_session_index(stories)
                except Exception:
                    # 索引发布失败时撤销刚创建的不可达 Session，保持文件与恢复槽位原子一致。
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError as rollback_exc:
                        raise NumericV2StoreError(
                            "numeric_session_create_rollback_failed"
                        ) from rollback_exc
                    raise
                return stored

    async def replace_active(
        self,
        previous_session_id: str,
        session: "ScriptSessionV2",
    ) -> NumericV2StoredSession:
        """用新 ID 替换槽位 Session，阻止旧页面继续提交且不累积历史。"""  # noqa: DOCSTRING_CJK

        previous_path = self._path(previous_session_id)
        next_path = self._path(session.session_id)
        if previous_path == next_path:
            raise NumericV2StoreError("numeric_replacement_session_id_reused")
        index_path = self._story_session_index_path
        async with _lock(index_path):
            async with _lock(previous_path):
                if not previous_path.is_file():
                    raise NumericV2SessionNotFoundError("numeric_session_not_found")
                previous = self._read(previous_path)
                if previous.session.story_package_id != session.story_package_id:
                    raise NumericV2StoreError("numeric_replacement_story_mismatch")
                async with _lock(next_path):
                    if next_path.exists():
                        raise NumericV2SessionExistsError("numeric_session_exists")
                    self.engine.validate_session(session)
                    stored = NumericV2StoredSession(session, ())
                    stories = self._read_story_session_index()
                    previous_stories = deepcopy(stories)
                    stories.setdefault(session.story_package_id, {})[
                        str(session.catgirl_binding.get("character_id") or "")
                    ] = session.session_id
                    self._write(next_path, stored, exclusive=True)
                    try:
                        self._write_story_session_index(stories)
                        previous_path.unlink()
                    except OSError as exc:
                        try:
                            self._write_story_session_index(previous_stories)
                        except OSError:
                            pass
                        try:
                            next_path.unlink()
                        except OSError:
                            pass
                        raise NumericV2StoreError("numeric_session_replace_failed") from exc
                    return stored

    async def load(self, session_id: str) -> NumericV2StoredSession | None:
        path = self._path(session_id)
        async with _lock(path):
            try:
                stored = self._read(path)
            except NumericV2StoreError as exc:
                if isinstance(exc.__cause__, FileNotFoundError):
                    return None
                raise
            # 先按持久化身份拒绝跨剧本 Session，再用当前剧本引擎重放 Ledger。
            if stored.session.story_package_id != self.engine.story_id:
                return None
            self._validate_chain(stored)
            return stored

    async def load_for_lifecycle(
        self,
        session_id: str,
    ) -> NumericV2StoredSession | None:
        """读取只用于结束/删除的快照，不把旧剧本状态恢复到当前 Runtime。"""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            try:
                stored = self._read(path)
            except NumericV2StoreError as exc:
                if isinstance(exc.__cause__, FileNotFoundError):
                    return None
                raise
            if stored.session.story_package_id != self.engine.story_id:
                return None
            self._validate_lifecycle_chain(stored)
            return stored

    async def update_catgirl_binding(
        self,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
    ) -> NumericV2StoredSession:
        """只迁移角色卡身份投影，不改变剧情、revision 或 Ledger。"""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            if not path.is_file():
                raise NumericV2SessionNotFoundError("numeric_session_not_found")
            current = self._read(path)
            self._validate_chain(current)
            migrated = NumericV2StoredSession(
                replace(
                    current.session,
                    catgirl_binding={
                        str(key): str(value)
                        for key, value in catgirl_binding.items()
                    },
                ),
                current.ledger_events,
            )
            self._validate_chain(migrated)
            self._write(path, migrated)
            return migrated

    async def commit(
        self,
        session: "ScriptSessionV2",
        ledger_event: Mapping[str, Any],
    ) -> NumericV2StoredSession:
        path = self._path(session.session_id)
        async with _lock(path):
            if not path.is_file():
                raise NumericV2SessionNotFoundError("numeric_session_not_found")
            current = self._read(path)
            if current.session.revision != int(ledger_event.get("base_revision", -1)):
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.status == "ended":
                raise NumericV2StoreRevisionConflictError("session_already_ended")
            if any(event.get("client_turn_id") == ledger_event.get("client_turn_id") for event in current.ledger_events):
                raise NumericV2StoreRevisionConflictError("numeric_duplicate_client_turn_id")
            if session.revision != current.session.revision + 1:
                raise NumericV2StoreError("numeric_revision_not_monotonic")
            stored = NumericV2StoredSession(
                session,
                (*current.ledger_events, deepcopy(dict(ledger_event))),
            )
            self._validate_chain(stored)
            self._write(path, stored)
            return stored

    async def end_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
        reason: str,
    ) -> NumericV2StoredSession:
        path = self._path(session_id)
        async with _lock(path):
            if not path.is_file():
                raise NumericV2SessionNotFoundError("numeric_session_not_found")
            current = self._read(path)
            # 剧本包可能已升级，结束动作只改生命周期；仍需复验持久化账本自身连续，
            # 但不能拿新剧本规则重放旧剧情，否则用户会永久卡在演绎状态。
            self._validate_lifecycle_chain(current)
            if current.session.revision != base_revision:
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.status == "ended":
                # 仅接受紧邻本次请求的成功重放；更早生命周期的延迟请求必须冲突。
                if (
                    current.session.ended_reason == str(reason or "user_exit")
                    and current.session.lifecycle_revision == base_lifecycle_revision + 1
                ):
                    return current
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.lifecycle_revision != base_lifecycle_revision:
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            ended = replace(
                current.session,
                status="ended",
                ended_reason=str(reason or "user_exit"),
                lifecycle_revision=current.session.lifecycle_revision + 1,
            )
            stored = NumericV2StoredSession(ended, current.ledger_events)
            self._write(path, stored)
            return stored

    async def resume_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
    ) -> NumericV2StoredSession:
        """恢复玩家主动退出的 Session；剧情自然结局仍保持不可继续。"""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            if not path.is_file():
                raise NumericV2SessionNotFoundError("numeric_session_not_found")
            current = self._read(path)
            if current.session.revision != base_revision:
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.status == "active":
                # 已成功继续后的同一请求可以安全重试，旧请求不能借当前 active 状态蒙混通过。
                if current.session.lifecycle_revision == base_lifecycle_revision + 1:
                    return current
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.lifecycle_revision != base_lifecycle_revision:
                raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
            if current.session.status != "ended" or current.session.ended_reason != "user_exit":
                raise NumericV2StoreError("numeric_session_not_resumable")
            resumed = NumericV2StoredSession(
                replace(
                    current.session,
                    status="active",
                    ended_reason=None,
                    lifecycle_revision=current.session.lifecycle_revision + 1,
                ),
                current.ledger_events,
            )
            self._validate_chain(resumed)
            self._write(path, resumed)
            return resumed

    async def forget_history_through_current_revision(
        self,
        session_id: str,
    ) -> NumericV2StoredSession:
        """记录显式遗忘边界，防止继续旧 Session 后重新投影已忘内容。"""  # noqa: DOCSTRING_CJK

        path = self._path(session_id)
        async with _lock(path):
            if not path.is_file():
                raise NumericV2SessionNotFoundError("numeric_session_not_found")
            current = self._read(path)
            if current.session.forgotten_through_revision >= current.session.revision:
                return current
            forgotten = NumericV2StoredSession(
                replace(
                    current.session,
                    forgotten_through_revision=current.session.revision,
                ),
                current.ledger_events,
            )
            self._validate_chain(forgotten)
            self._write(path, forgotten)
            return forgotten

    def _read(self, path: Path) -> NumericV2StoredSession:
        from .numeric_v2_runtime import ScriptSessionV2, _player_address_disclosed

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NumericV2StoreError("numeric_session_read_failed") from exc
        if not isinstance(payload, dict) or payload.get("schema") != STORE_SCHEMA:
            raise NumericV2StoreError("numeric_store_schema_invalid")
        raw_session = dict(payload.get("session") or {})
        if "player_address_known" not in raw_session:
            initial_state = self.engine.story.get("initial_state")
            initial_known = bool(
                initial_state.get("player_address_known")
                if isinstance(initial_state, Mapping)
                else False
            )
            if not initial_known:
                binding = raw_session.get("catgirl_binding")
                configured_address = (
                    str(binding.get("player_address") or "").strip()
                    if isinstance(binding, Mapping)
                    else ""
                )
                events = payload.get("ledger_events")
                initial_known = bool(
                    configured_address
                    and configured_address not in {"你", "男主"}
                    and isinstance(events, list)
                    and any(
                        _player_address_disclosed(
                            str(event.get("input_text") or ""),
                            configured_address,
                        )
                        for event in events
                        if isinstance(event, Mapping)
                    )
                )
            raw_session["player_address_known"] = initial_known
        session = ScriptSessionV2.from_mapping(raw_session)
        events = payload.get("ledger_events")
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise NumericV2StoreError("numeric_ledger_invalid")
        return NumericV2StoredSession(session, tuple(deepcopy(events)))

    def _validate_lifecycle_chain(self, stored: NumericV2StoredSession) -> None:
        """校验与剧本内容无关的存档链，仅供生命周期收尾和启动审计。"""  # noqa: DOCSTRING_CJK

        session = stored.session
        if session.story_package_id != self.engine.story_id:
            raise NumericV2StoreError("story_package_id_mismatch")
        if session.status not in {"active", "ended"}:
            raise NumericV2StoreError("session_status_invalid")
        if (
            session.node_turn_count < 0
            or session.revision < 0
            or session.lifecycle_revision < 0
            or not -1 <= session.forgotten_through_revision <= session.revision
        ):
            raise NumericV2StoreError("session_counter_invalid")

        events = stored.ledger_events
        if len(events) != session.revision:
            raise NumericV2StoreError("numeric_ledger_revision_mismatch")
        if len(session.performance_history) != len(events):
            raise NumericV2StoreError("numeric_performance_history_mismatch")

        expected_node: str | None = None
        expected_metrics: dict[str, int] | None = None
        seen_turns: set[str] = set()
        for event_index, event in enumerate(events):
            expected_revision = event_index + 1
            if (
                not isinstance(event, Mapping)
                or event.get("session_id") != session.session_id
                or event.get("base_revision") != expected_revision - 1
                or event.get("result_revision") != expected_revision
            ):
                raise NumericV2StoreError("numeric_ledger_revision_chain_invalid")

            from_node_id = str(event.get("from_node_id") or "")
            to_node_id = str(event.get("to_node_id") or "")
            if not from_node_id or not to_node_id:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")
            if expected_node is not None and from_node_id != expected_node:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")

            before_metrics = event.get("before_metrics")
            after_metrics = event.get("after_metrics")
            if (
                not isinstance(before_metrics, Mapping)
                or not isinstance(after_metrics, Mapping)
                or any(isinstance(value, bool) or not isinstance(value, int) for value in before_metrics.values())
                or any(isinstance(value, bool) or not isinstance(value, int) for value in after_metrics.values())
            ):
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")
            if expected_metrics is not None and dict(before_metrics) != expected_metrics:
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")

            turn_id = str(event.get("client_turn_id") or "")
            if not turn_id or turn_id in seen_turns:
                raise NumericV2StoreError("numeric_ledger_turn_id_invalid")
            performance = session.performance_history[event_index]
            if (
                not isinstance(performance, Mapping)
                or performance.get("client_turn_id") != turn_id
                or performance.get("revision") != expected_revision
                or performance.get("from_node_id") != from_node_id
                or performance.get("to_node_id") != to_node_id
            ):
                raise NumericV2StoreError("numeric_performance_record_mismatch")

            expected_node = to_node_id
            expected_metrics = dict(after_metrics)
            seen_turns.add(turn_id)

        if events and (
            expected_node != session.current_node_id
            or expected_metrics != session.metrics
        ):
            raise NumericV2StoreError("numeric_session_not_at_ledger_tail")
        if (
            seen_turns != set(session.processed_client_turn_ids)
            or len(seen_turns) != len(session.processed_client_turn_ids)
        ):
            raise NumericV2StoreError("numeric_processed_turn_ids_mismatch")

    def _validate_chain(self, stored: NumericV2StoredSession) -> None:
        self.engine.validate_session(stored.session)
        events = stored.ledger_events
        if len(events) != stored.session.revision:
            raise NumericV2StoreError("numeric_ledger_revision_mismatch")
        expected_revision = 0
        expected_node = str(self.engine.story["start_node_id"])
        expected_metrics = {str(key): int(value) for key, value in self.engine.story["initial_state"]["metrics"].items()}
        seen_turns: set[str] = set()
        from .numeric_v2_runtime import MetricChangeV2, TurnRequestV2

        replay_session = self.engine.create_session(
            session_id=stored.session.session_id,
            catgirl_binding=stored.session.catgirl_binding,
            opening_performance=stored.session.opening_performance,
            actor_budget_profile=stored.session.actor_budget_profile,
        )
        if len(stored.session.performance_history) != len(events):
            raise NumericV2StoreError("numeric_performance_history_mismatch")
        for event_index, event in enumerate(events):
            expected_revision += 1
            if not isinstance(event, Mapping):
                raise NumericV2StoreError("numeric_ledger_event_invalid")
            if event.get("base_revision") != expected_revision - 1 or event.get("result_revision") != expected_revision:
                raise NumericV2StoreError("numeric_ledger_revision_chain_invalid")
            if event.get("from_node_id") != expected_node:
                raise NumericV2StoreError("numeric_ledger_node_chain_invalid")
            if event.get("before_metrics") != expected_metrics:
                raise NumericV2StoreError("numeric_ledger_metric_chain_invalid")
            turn_id = str(event.get("client_turn_id") or "")
            if not turn_id or turn_id in seen_turns:
                raise NumericV2StoreError("numeric_ledger_turn_id_invalid")
            raw_changes = event.get("metric_changes")
            if not isinstance(raw_changes, list):
                raise NumericV2StoreError("numeric_ledger_metric_changes_invalid")
            try:
                changes = tuple(
                    MetricChangeV2.from_mapping(
                        {
                            key: change.get(key)
                            for key in ("metric_id", "delta", "criterion", "evidence")
                        },
                        self.engine.metric_schema,
                    )
                    for change in raw_changes
                    if isinstance(change, Mapping)
                )
                if len(changes) != len(raw_changes):
                    raise ValueError("metric_change_shape")
                scene_complete = event.get("scene_complete")
                if not isinstance(scene_complete, bool):
                    raise ValueError("scene_complete_shape")
                # 重放必须使用当时记录的结局授权；旧 Ledger 缺省关闭，不能由完成信号推导。
                natural_ending_ready = event.get("natural_ending_ready", False)
                if not isinstance(natural_ending_ready, bool):
                    raise ValueError("natural_ending_ready_shape")
                request = TurnRequestV2.from_mapping(
                    {
                        "client_turn_id": turn_id,
                        "base_revision": event.get("base_revision"),
                        "message": event.get("input_text"),
                    }
                )
                replayed = self.engine.resolve_turn(
                    replay_session,
                    request,
                    changes,
                    scene_complete=scene_complete,
                    transition_intent=str(event.get("transition_intent") or "unclear"),
                    natural_ending_ready=natural_ending_ready,
                )
                performance = stored.session.performance_history[event_index]
                if not isinstance(performance, Mapping):
                    raise ValueError("performance_record_shape")
            except Exception as exc:
                raise NumericV2StoreError("numeric_ledger_replay_invalid") from exc
            expected_event = replayed.ledger_event
            for field in (
                "schema",
                "session_id",
                "client_turn_id",
                "base_revision",
                "result_revision",
                "from_node_id",
                "to_node_id",
                "route_id",
                "route_status",
                "scene_complete",
                "node_turn_count",
                "status",
                "before_metrics",
                "after_metrics",
                "metric_changes",
            ):
                if event.get(field) != expected_event.get(field):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            replayed_session = replayed.session
            if "transition_offered" in event:
                # 新 Ledger 的提议状态来自同 revision 的 Actor 正文；重放时只复用已提交值。
                committed_transition_offered = event.get("transition_offered")
                if not isinstance(committed_transition_offered, bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                if performance.get("transition_offered", False) != committed_transition_offered:
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                replayed_session = replace(
                    replayed_session,
                    transition_offered=committed_transition_offered,
                )
            disclosure_version = event.get("player_address_disclosure_version")
            if disclosure_version not in {None, 2}:
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if disclosure_version == 2 and "player_address_known" not in event:
                raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            if "player_address_known" in event:
                committed_address_known = event.get("player_address_known")
                if not isinstance(committed_address_known, bool):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                if committed_address_known != expected_event.get("player_address_known"):
                    if disclosure_version == 2 or committed_address_known is False:
                        raise NumericV2StoreError("numeric_ledger_replay_mismatch")
                    # 版本字段加入前，任意昵称出现都会被提交为知情；既成 Session 不能因规则收紧而损坏。
                    replayed_session = replace(
                        replayed_session,
                        player_address_known=True,
                    )
            for field in (
                "transition_intent",
                "before_dialogue_policy",
                "performance_dialogue_policy",
                "dialogue_policy",
            ):
                if field in event and event.get(field) != expected_event.get(field):
                    raise NumericV2StoreError("numeric_ledger_replay_mismatch")
            performance_contract_version = performance.get("performance_contract_version")
            if performance_contract_version not in {None, 1, 2, 3}:
                raise NumericV2StoreError("numeric_performance_record_invalid")
            if (
                performance.get("client_turn_id") != turn_id
                or performance.get("revision") != expected_revision
                or performance.get("from_node_id") != expected_event["from_node_id"]
                or performance.get("to_node_id") != expected_event["to_node_id"]
            ):
                raise NumericV2StoreError("numeric_performance_record_mismatch")
            if performance_contract_version == 2:
                if expected_event["from_node_id"] == expected_event["to_node_id"]:
                    if not valid_ordered_content(
                        performance,
                        require_narration=True,
                        require_dialogue=True,
                    ):
                        raise NumericV2StoreError("numeric_performance_record_invalid")
                else:
                    segments = performance.get("segments")
                    if (
                        not isinstance(segments, list)
                        or len(segments) != 3
                        or not all(isinstance(segment, Mapping) for segment in segments)
                        or [segment.get("phase") for segment in segments]
                        != ["source_response", "transition_bridge", "target_opening"]
                        or not valid_ordered_content(segments[0], require_dialogue=True)
                        or not valid_ordered_content(segments[1], require_narration=True)
                        or not valid_ordered_content(segments[2], require_narration=True)
                    ):
                        raise NumericV2StoreError("numeric_transition_performance_invalid")
            if performance_contract_version == 3:
                if expected_event["from_node_id"] == expected_event["to_node_id"]:
                    if not valid_mixed_performance_policy(
                        performance,
                        str(
                            event.get("performance_dialogue_policy")
                            or replayed_session.dialogue_policy
                        ),
                    ):
                        raise NumericV2StoreError("numeric_performance_record_invalid")
                else:
                    segments = performance.get("segments")
                    if (
                        not isinstance(segments, list)
                        or len(segments) != 3
                        or not all(isinstance(segment, Mapping) for segment in segments)
                        or [segment.get("phase") for segment in segments]
                        != ["source_response", "transition_bridge", "target_opening"]
                        or set(segments[0]) != {"phase", "performance"}
                        or set(segments[1]) != {"phase", "scene_narration"}
                        or set(segments[2]) != {"phase", "scene_narration", "performance"}
                        or not valid_mixed_performance_policy(
                            segments[0],
                            transition_source_dialogue_policy(
                                replay_session.dialogue_policy
                            ),
                        )
                        # 合同版本 3 允许去重后的空桥段；目标开场仍必须非空并由 Runtime 注入。
                        or not valid_scene_narration(segments[1], allow_empty=True)
                        or not valid_scene_narration(segments[2])
                        or not valid_mixed_performance_policy(
                            segments[2], replayed_session.dialogue_policy
                        )
                    ):
                        raise NumericV2StoreError("numeric_transition_performance_invalid")
            if (
                expected_event["from_node_id"] != expected_event["to_node_id"]
                and performance_contract_version in {1, 2, 3}
            ):
                segments = performance.get("segments")
                if (
                    performance.get("transition_delivered") is not True
                    or performance.get("visible_node_id") != expected_event["to_node_id"]
                    or not isinstance(segments, list)
                    or [item.get("phase") for item in segments if isinstance(item, Mapping)]
                    != ["source_response", "transition_bridge", "target_opening"]
                ):
                    raise NumericV2StoreError("numeric_transition_performance_invalid")
            # 重放时同步补回正式正文历史，供后续上下文继续使用。
            replay_session = replace(
                replayed_session,
                performance_history=(*replayed_session.performance_history, deepcopy(dict(performance))),
            )
            expected_node = str(expected_event["to_node_id"])
            expected_metrics = dict(expected_event["after_metrics"])
            seen_turns.add(turn_id)
        if expected_node != stored.session.current_node_id or expected_metrics != stored.session.metrics:
            raise NumericV2StoreError("numeric_session_not_at_ledger_tail")
        if seen_turns != set(stored.session.processed_client_turn_ids):
            raise NumericV2StoreError("numeric_processed_turn_ids_mismatch")
        if not (
            replay_session.status == stored.session.status
            or (
                replay_session.status == "active"
                and stored.session.status == "ended"
                and stored.session.ended_reason
            )
        ):
            raise NumericV2StoreError("numeric_session_status_mismatch")
        if replay_session.revision != stored.session.revision:
            raise NumericV2StoreError("numeric_session_revision_mismatch")
        if (
            replay_session.player_address_known != stored.session.player_address_known
            or replay_session.dialogue_policy != stored.session.dialogue_policy
            or replay_session.transition_offered != stored.session.transition_offered
        ):
            raise NumericV2StoreError("numeric_session_scene_progress_mismatch")

    def _write(self, path: Path, stored: NumericV2StoredSession, *, exclusive: bool = False) -> None:
        payload = {
            "schema": STORE_SCHEMA,
            "session": stored.session.to_dict(),
            "ledger_events": deepcopy(list(stored.ledger_events)),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False) as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            if exclusive:
                try:
                    os.link(temporary_path, path)
                except FileExistsError as exc:
                    raise NumericV2SessionExistsError("numeric_session_exists") from exc
                except OSError:
                    # Some user-selected filesystems do not support hard links.
                    # Preserve exclusive creation semantics with a direct write.
                    try:
                        target_fd = os.open(
                            path,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                    except FileExistsError as exc:
                        raise NumericV2SessionExistsError("numeric_session_exists") from exc
                    try:
                        with os.fdopen(target_fd, "wb") as target_file:
                            target_file.write(encoded)
                            target_file.flush()
                            os.fsync(target_file.fileno())
                    except Exception:
                        try:
                            path.unlink()
                        except OSError:
                            pass
                        raise
            else:
                os.replace(temporary_path, path)
                temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


__all__ = [
    "delete_numeric_v2_sessions",
    "list_numeric_v2_public_archives",
    "list_numeric_v2_sessions",
    "numeric_v2_story_session_guard",
    "update_numeric_v2_character_bindings",
    "NumericV2SessionExistsError",
    "NumericV2SessionNotFoundError",
    "NumericV2SessionStore",
    "NumericV2StoreError",
    "NumericV2StoreRevisionConflictError",
    "NumericV2StoredSession",
]
