"""Claude Code Adapter — 会话管理器。

负责：
1. 维护 cwd → SessionRecord 的映射
2. 判断是否可以恢复会话（cwd 匹配 + 提示包签名匹配）
3. 通过 PluginStore 持久化会话记录
4. 标记会话为损坏（poisoned）以便下次跳过

设计要点：
- 使用 PluginStore 而非独立 SQLite，符合 SDK 设计
- 提示包签名用于检测 system prompt / skills 变化
- 线程安全由 asyncio.Lock 保证（插件运行在单事件循环）
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Optional

from .models import SessionRecord


# ---------------------------------------------------------------------------
# 提示包签名
# ---------------------------------------------------------------------------


def compute_prompt_signature(
    *,
    system_prompt_file: str = "",
    skills_dir: str = "",
    extra: str = "",
) -> str:
    """计算提示包签名。

    签名基于 system prompt 文件路径 + skills 目录 + 额外标记。
    当任一变化时，签名变化，旧会话会被放弃。

    注意：签名不包含 prompt 本身（prompt 每次都不同），
    只包含影响 Claude CLI 上下文构建的"环境"因素。
    """
    parts = [
        f"sys:{system_prompt_file or ''}",
        f"skills:{skills_dir or ''}",
        f"extra:{extra or ''}",
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 会话管理器
# ---------------------------------------------------------------------------


class SessionManager:
    """会话管理器。

    会话记录通过 PluginStore 持久化，key 格式为
    ``session:{cwd}``，其中 cwd 是工作目录的绝对路径。

    用法::

        mgr = SessionManager(store)
        await mgr.load()

        # 尝试恢复
        record = await mgr.find_resumable(cwd, signature)
        if record:
            resume_id = record.session_id
        else:
            resume_id = ""

        # 执行后更新
        if new_session_id:
            await mgr.upsert(new_session_id, cwd, signature)
        elif record:
            await mgr.touch(record.session_id, turn_count=5)

        # 错误时标记
        await mgr.mark_error(session_id, "poisoned_session")
    """

    STORE_KEY_PREFIX = "session:"

    def __init__(self, store: Any, logger: Any = None) -> None:
        self.store = store
        self.logger = logger
        self._lock = asyncio.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        """key = cwd（绝对路径），value = SessionRecord。"""

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """从 PluginStore 加载所有会话记录。"""
        async with self._lock:
            self._sessions.clear()
            try:
                result = await self.store.keys(prefix=self.STORE_KEY_PREFIX)
                # PluginStore.keys 返回 Result[list[str], ...]
                keys = _unwrap_result(result, default=[])
                for key in keys:
                    if not isinstance(key, str):
                        continue
                    get_result = await self.store.get(key)
                    data = _unwrap_result(get_result, default=None)
                    if isinstance(data, dict):
                        try:
                            record = SessionRecord.from_dict(data)
                            self._sessions[record.cwd] = record
                        except Exception as e:
                            if self.logger is not None:
                                try:
                                    self.logger.warning(
                                        "Failed to load session {}: {}", key, e
                                    )
                                except Exception:
                                    pass
            except Exception as e:
                if self.logger is not None:
                    try:
                        self.logger.warning("SessionManager.load failed: {}", e)
                    except Exception:
                        pass

        if self.logger is not None:
            try:
                self.logger.info(
                    "SessionManager loaded {} sessions", len(self._sessions)
                )
            except Exception:
                pass

    async def _persist(self, cwd: str, record: SessionRecord) -> None:
        """持久化单条记录。调用方需持有 _lock。"""
        key = self._key(cwd)
        try:
            await self.store.set(key, record.to_dict())
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.warning("Failed to persist session {}: {}", key, e)
                except Exception:
                    pass

    async def _delete(self, cwd: str) -> None:
        """删除单条记录。调用方需持有 _lock。"""
        key = self._key(cwd)
        try:
            await self.store.delete(key)
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.warning("Failed to delete session {}: {}", key, e)
                except Exception:
                    pass

    def _key(self, cwd: str) -> str:
        return f"{self.STORE_KEY_PREFIX}{cwd}"

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def find_resumable(
        self,
        cwd: str,
        prompt_signature: str,
    ) -> Optional[SessionRecord]:
        """查找可恢复的会话。

        可恢复的条件：
        1. cwd 匹配
        2. prompt_signature 匹配（提示包未变化）
        3. last_error 不是不可恢复的错误（poisoned_session / unknown_session）
        4. session_id 非空
        """
        async with self._lock:
            record = self._sessions.get(cwd)
            if record is None:
                return None
            if not record.session_id:
                return None
            if record.prompt_signature != prompt_signature:
                return None
            if record.last_error in ("poisoned_session", "unknown_session"):
                return None
            return record

    async def list_all(self) -> list[SessionRecord]:
        """返回所有会话记录（按 last_used_at 降序）。"""
        async with self._lock:
            records = list(self._sessions.values())
        records.sort(key=lambda r: r.last_used_at, reverse=True)
        return records

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    async def upsert(
        self,
        session_id: str,
        cwd: str,
        prompt_signature: str,
    ) -> SessionRecord:
        """新建或更新会话记录。

        如果 cwd 已有记录且 session_id 不同，会覆盖旧记录。
        """
        now = time.time()
        async with self._lock:
            existing = self._sessions.get(cwd)
            if existing is not None and existing.session_id == session_id:
                # 同一会话 — 只更新时间戳
                existing.last_used_at = now
                await self._persist(cwd, existing)
                return existing

            record = SessionRecord(
                session_id=session_id,
                cwd=cwd,
                prompt_signature=prompt_signature,
                created_at=existing.created_at if existing is not None else now,
                last_used_at=now,
                turn_count=existing.turn_count if existing is not None else 0,
                last_error="",  # 新会话清除错误状态
            )
            self._sessions[cwd] = record
            await self._persist(cwd, record)
            return record

    async def touch(
        self,
        session_id: str,
        *,
        turn_count: int = 0,
    ) -> None:
        """更新会话的最近使用时间和轮次。"""
        async with self._lock:
            for record in self._sessions.values():
                if record.session_id == session_id:
                    record.last_used_at = time.time()
                    if turn_count > 0:
                        record.turn_count += turn_count
                    await self._persist(record.cwd, record)
                    return

    async def mark_error(
        self,
        session_id: str,
        error_kind: str,
    ) -> None:
        """标记会话的最近错误。"""
        async with self._lock:
            for record in self._sessions.values():
                if record.session_id == session_id:
                    record.last_error = error_kind
                    await self._persist(record.cwd, record)
                    return

    async def clear(self, cwd: Optional[str] = None) -> int:
        """清除会话记录。

        Parameters
        ----------
        cwd:
            指定工作目录则只清除该目录的会话；None 则清除所有。

        Returns
        -------
        清除的记录数。
        """
        async with self._lock:
            if cwd is not None:
                if cwd in self._sessions:
                    del self._sessions[cwd]
                    await self._delete(cwd)
                    return 1
                return 0

            count = len(self._sessions)
            keys_to_delete = list(self._sessions.keys())
            self._sessions.clear()
            for k in keys_to_delete:
                await self._delete(k)
            return count


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _unwrap_result(result: Any, default: Any = None) -> Any:
    """解包 Result 类型。

    SDK 的 PluginStore 方法返回 Result[T, E] 类型（Ok / Err）。
    本函数兼容两种形态：
    - Result 对象（有 .is_ok() / .value_or_none() 方法）
    - 直接值（非 Result 包装，向后兼容）
    """
    if result is None:
        return default
    # Result 类型 — 优先使用 value_or_none()（Ok 返回值，Err 返回 None）
    value_or_none = getattr(result, "value_or_none", None)
    if callable(value_or_none):
        try:
            value = value_or_none()
            return value if value is not None else default
        except Exception:
            return default
    # 兼容：检查 is_ok / value
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        try:
            if result.is_ok():
                value = getattr(result, "value", default)
                return value if value is not None else default
            return default
        except Exception:
            return default
    # 直接值
    return result


__all__ = [
    "compute_prompt_signature",
    "SessionManager",
]
