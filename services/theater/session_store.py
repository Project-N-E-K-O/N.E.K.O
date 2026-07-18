from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async
from utils.logger_config import get_module_logger


_SESSION_ID_RE = re.compile(r"^theater_[a-f0-9-]{36}$")
# 轻量架构只接受当前协议存档；旧重型 Session 不做高风险字段迁移。
SESSION_SCHEMA_VERSION = 1
# Session 终止原因只接受框架固定枚举；休眠不属于终止，不能写入这组原因。
SESSION_END_REASONS = frozenset(
    {
        "story_complete",
        "branch_ending_domain",
        "user_exit",
        "replaced_by_new_session",
        "character_switch",
        "management_end",
        "start_publish_failed",
    }
)
_ACTIVE_BY_ROOT_AND_LANLAN: dict[tuple[str, str], str] = {}
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_ACTIVE_INDEX_LOCKS: dict[str, asyncio.Lock] = {}
_CHARACTER_LOCKS: dict[str, asyncio.Lock] = {}
logger = get_module_logger("services.theater.session_store")


@asynccontextmanager
async def session_guard(session_id: str):
    """串行保护同一 Session 的回合、离场和恢复写入。"""  # noqa: DOCSTRING_CJK
    lock = _SESSION_LOCKS.setdefault(str(session_id or ""), asyncio.Lock())
    async with lock:
        yield


@asynccontextmanager
async def active_index_guard(root: Path):
    """串行保护同一运行目录下共享活动索引的完整读改写过程。"""  # noqa: DOCSTRING_CJK
    # 活动索引由所有猫娘共享，因此锁必须按运行目录而不是按角色划分。
    root_key = str(Path(root).resolve())
    lock = _ACTIVE_INDEX_LOCKS.setdefault(root_key, asyncio.Lock())
    async with lock:
        yield


@asynccontextmanager
async def character_guard(root: Path, lanlan_name: str):
    """串行保护同一猫娘的开场与活动 Session 切换。"""  # noqa: DOCSTRING_CJK
    # 角色名与运行目录共同组成锁键，测试目录和不同用户数据根不会互相阻塞。
    lock_key = f"{Path(root).resolve()}::{str(lanlan_name or '').strip()}"
    lock = _CHARACTER_LOCKS.setdefault(lock_key, asyncio.Lock())
    async with lock:
        yield


def state_revision(session: dict[str, Any]) -> int:
    """读取非负 revision；旧存档缺失时从零开始。"""  # noqa: DOCSTRING_CJK
    value = session.get("state_revision")
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def lifecycle_fields_valid(session: dict[str, Any]) -> bool:
    """校验可选休眠时间和固定终止原因，不猜测修复任意外部文本。"""  # noqa: DOCSTRING_CJK
    dormant_at = session.get("dormant_at")
    if dormant_at is not None and (
        not isinstance(dormant_at, int)
        or isinstance(dormant_at, bool)
        or dormant_at <= 0
    ):
        return False
    ended_at = session.get("ended_at")
    if ended_at is not None and (
        not isinstance(ended_at, int) or isinstance(ended_at, bool) or ended_at <= 0
    ):
        return False
    end_reason = session.get("end_reason")
    if end_reason is not None and (
        not isinstance(end_reason, str)
        or end_reason not in SESSION_END_REASONS
        or ended_at is None
    ):
        # 终止原因必须伴随合法结束时间；旧结束存档可以缺原因，但活动 Session 不能携带孤儿原因。
        return False
    return True


async def set_active_session(root: Path, lanlan_name: str, session_id: str) -> None:
    """记录角色当前小剧场 session，并写入私有 active 索引用于重启恢复。"""  # noqa: DOCSTRING_CJK
    if lanlan_name and session_id:
        async with active_index_guard(root):
            active = await load_active_sessions(root)
            active[lanlan_name] = session_id
            await save_active_sessions(root, active)
            # 磁盘提交成功后再更新内存，避免写失败时两份索引分叉。
            _ACTIVE_BY_ROOT_AND_LANLAN[_active_cache_key(root, lanlan_name)] = (
                session_id
            )


async def clear_active_session(root: Path, lanlan_name: str, session_id: str) -> None:
    """仅当 session 仍是当前角色 active 时清除 active 索引。"""  # noqa: DOCSTRING_CJK
    async with active_index_guard(root):
        active = await load_active_sessions(root)
        if lanlan_name and active.get(lanlan_name) == session_id:
            active.pop(lanlan_name, None)
            await save_active_sessions(root, active)
        # 只有磁盘状态已经保留或成功清除后，才同步移除对应内存指针。
        cache_key = _active_cache_key(root, lanlan_name)
        if lanlan_name and _ACTIVE_BY_ROOT_AND_LANLAN.get(cache_key) == session_id:
            _ACTIVE_BY_ROOT_AND_LANLAN.pop(cache_key, None)


async def get_active_session_id(root: Path, lanlan_name: str) -> str:
    """读取角色当前 active session，优先用内存索引，缺失时从文件恢复。"""  # noqa: DOCSTRING_CJK
    if not lanlan_name:
        return ""
    async with active_index_guard(root):
        cache_key = _active_cache_key(root, lanlan_name)
        active_session_id = _ACTIVE_BY_ROOT_AND_LANLAN.get(cache_key)
        if active_session_id:
            return active_session_id
        active = await load_active_sessions(root)
        restored_session_id = str(active.get(lanlan_name) or "")
        if restored_session_id:
            _ACTIVE_BY_ROOT_AND_LANLAN[cache_key] = restored_session_id
        return restored_session_id


async def is_stale_session(root: Path, session: dict[str, Any]) -> bool:
    """判断 session 是否已被同角色更新的 active session 顶掉。"""  # noqa: DOCSTRING_CJK
    lanlan_name = str(session.get("lanlan_name") or "")
    session_id = str(session.get("session_id") or "")
    active_session_id = await get_active_session_id(root, lanlan_name)
    return bool(active_session_id and active_session_id != session_id)


async def load_session(root: Path, session_id: str) -> dict[str, Any] | None:
    """读取当前轻量协议 Session；不兼容版本由状态接口另行解释。"""  # noqa: DOCSTRING_CJK
    session, _reason = await load_session_with_status(root, session_id)
    return session


async def load_session_with_status(
    root: Path, session_id: str
) -> tuple[dict[str, Any] | None, str]:
    """读取 Session，并区分不存在、旧版本和不支持版本。"""  # noqa: DOCSTRING_CJK
    if not _SESSION_ID_RE.match(str(session_id or "")):
        return None, "session_not_found"
    path = session_path(root, session_id)
    try:
        data = await read_json_async(path)
    except FileNotFoundError:
        return None, "session_not_found"
    if not isinstance(data, dict):
        return None, "session_not_found"
    schema_version = data.get("schema_version")
    if schema_version is None:
        # 瘦身前 Session 结构无法安全映射到作者图节点，保留原文件并明确要求重新开场。
        return None, "session_upgrade_required"
    if schema_version != SESSION_SCHEMA_VERSION:
        # 未知版本可能来自更高版本，不能删除或按当前结构误读。
        return None, "session_version_unsupported"
    return data, ""


async def save_session(root: Path, session: dict[str, Any]) -> None:
    """把 theater session 原子写入私有 sessions 目录。"""  # noqa: DOCSTRING_CJK
    path = session_path(root, str(session["session_id"]))
    await atomic_write_json_async(path, session, ensure_ascii=False, indent=2)


async def load_active_sessions(root: Path) -> dict[str, str]:
    """读取 theater active 索引，只保留角色名到合法 session_id 的映射。"""  # noqa: DOCSTRING_CJK
    try:
        data = await read_json_async(active_sessions_path(root))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return await _recover_active_sessions(root, reason=type(exc).__name__)
    if not isinstance(data, dict):
        # 合法 JSON 但顶层结构错误同样属于索引损坏，不能让历史 Session 失去 stale 依据。
        return await _recover_active_sessions(root, reason="invalid_payload")
    active: dict[str, str] = {}
    for lanlan_name, session_id in data.items():
        lanlan = str(lanlan_name or "").strip()
        sid = str(session_id or "").strip()
        if lanlan and _SESSION_ID_RE.match(sid):
            active[lanlan] = sid
    if len(active) != len(data):
        # 任一非法映射都说明索引不完整，统一从 Session 真源重建，避免只恢复部分角色。
        return await _recover_active_sessions(root, reason="invalid_mapping")
    return active


async def _recover_active_sessions(root: Path, *, reason: str) -> dict[str, str]:
    """从 Session 真源重建损坏活动索引，并在可写时修复磁盘文件。"""  # noqa: DOCSTRING_CJK
    rebuilt = await _rebuild_active_sessions(root)
    logger.warning(
        "小剧场活动索引损坏，已从未结束 Session 重建: path=%s count=%d reason=%s",
        active_sessions_path(root),
        len(rebuilt),
        reason,
    )
    try:
        await save_active_sessions(root, rebuilt)
    except OSError as write_exc:
        # 只读或短暂 IO 故障时仍返回内存重建结果，不能让附属索引阻断普通角色切换。
        logger.warning(
            "小剧场活动索引重建写盘失败: path=%s err=%s",
            active_sessions_path(root),
            write_exc,
        )
    return rebuilt


async def _rebuild_active_sessions(root: Path) -> dict[str, str]:
    """按每个猫娘最新的未结束 Session 重建可恢复活动指针。"""  # noqa: DOCSTRING_CJK
    latest_by_lanlan: dict[str, tuple[int, str]] = {}
    for session_id in await list_session_ids(root):
        try:
            session = await load_session(root, session_id)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            # 单个 Session 损坏不应阻止其他角色恢复；损坏剧情文件本身不会被改写。
            continue
        if not isinstance(session, dict) or session.get("ended_at"):
            continue
        lanlan_name = str(session.get("lanlan_name") or "").strip()
        if not lanlan_name:
            continue
        timestamp = session.get("updated_at") or session.get("started_at") or 0
        normalized_timestamp = (
            timestamp
            if isinstance(timestamp, int) and not isinstance(timestamp, bool)
            else 0
        )
        candidate = (normalized_timestamp, session_id)
        if candidate > latest_by_lanlan.get(lanlan_name, (-1, "")):
            latest_by_lanlan[lanlan_name] = candidate
    return {
        lanlan_name: candidate[1] for lanlan_name, candidate in latest_by_lanlan.items()
    }


async def save_active_sessions(root: Path, active: dict[str, str]) -> None:
    """把 theater active 索引原子写入文件，供进程重启后恢复 stale 判断。"""  # noqa: DOCSTRING_CJK
    await atomic_write_json_async(
        active_sessions_path(root), active, ensure_ascii=False, indent=2
    )


async def list_session_ids(root: Path) -> list[str]:
    """列出 theater 私有 sessions 目录下形态合法的 session_id。"""  # noqa: DOCSTRING_CJK
    sessions_dir = root / "sessions"

    def _scan() -> list[str]:
        if not sessions_dir.exists():
            return []
        session_ids: list[str] = []
        for path in sessions_dir.glob("*.json"):
            session_id = path.stem
            if _SESSION_ID_RE.match(session_id):
                session_ids.append(session_id)
        return sorted(session_ids)

    return await asyncio.to_thread(_scan)


def session_path(root: Path, session_id: str) -> Path:
    """生成 theater 私有 session 文件路径。"""  # noqa: DOCSTRING_CJK
    return root / "sessions" / f"{session_id}.json"


def active_sessions_path(root: Path) -> Path:
    """生成 theater active session 索引文件路径。"""  # noqa: DOCSTRING_CJK
    return root / "active_sessions.json"


def _active_cache_key(root: Path, lanlan_name: str) -> tuple[str, str]:
    """生成与磁盘活动索引和锁相同作用域的内存缓存键。"""  # noqa: DOCSTRING_CJK
    return str(Path(root).resolve()), str(lanlan_name or "").strip()


def reset_active_sessions_for_tests() -> None:
    """清空进程内 active 索引，供单元测试模拟后端重启。"""  # noqa: DOCSTRING_CJK
    _ACTIVE_BY_ROOT_AND_LANLAN.clear()
    _SESSION_LOCKS.clear()
    _ACTIVE_INDEX_LOCKS.clear()
    _CHARACTER_LOCKS.clear()
