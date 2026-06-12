"""
1:1 ported from claudian/src/providers/claude/runtime/ClaudeSessionManager.ts

Session 管理（sessionId 跟踪 / resume / fork）。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional


class SessionManager:
    """
    维护运行时 session 状态。
    - session_id: 当前 session id（来自 SDK init 消息）
    - parent_session_id: 上一个 session（用于 fork / resume）
    - pending_resume: 启动时希望 resume 的 session
    - pending_fork: 启动时希望 fork 的 session
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._session_id: Optional[str] = None
        self._parent_session_id: Optional[str] = None
        self._pending_resume: Optional[str] = None
        self._pending_fork: bool = False
        self._history: List[str] = []  # 历史 session_id（用于切换）

    # ------------------------------------------------------------------
    # session_id
    # ------------------------------------------------------------------

    def set_session_id(self, sid: Optional[str]):
        with self._lock:
            if sid and sid != self._session_id:
                if self._session_id:
                    self._history.append(self._session_id)
                self._session_id = sid
            elif sid is None:
                self._session_id = None

    def get_session_id(self) -> Optional[str]:
        with self._lock:
            return self._session_id

    # ------------------------------------------------------------------
    # parent (fork 用)
    # ------------------------------------------------------------------

    def set_parent_session_id(self, parent: Optional[str]):
        with self._lock:
            self._parent_session_id = parent

    def get_parent_session_id(self) -> Optional[str]:
        with self._lock:
            return self._parent_session_id

    # ------------------------------------------------------------------
    # pending resume / fork
    # ------------------------------------------------------------------

    def set_pending_resume(self, sid: Optional[str]):
        with self._lock:
            self._pending_resume = sid

    def take_pending_resume(self) -> Optional[str]:
        with self._lock:
            v = self._pending_resume
            self._pending_resume = None
            return v

    def set_pending_fork(self, fork: bool):
        with self._lock:
            self._pending_fork = fork

    def take_pending_fork(self) -> bool:
        with self._lock:
            v = self._pending_fork
            self._pending_fork = False
            return v

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self._session_id,
                "parent_session_id": self._parent_session_id,
                "pending_resume": self._pending_resume,
                "pending_fork": self._pending_fork,
                "history_size": len(self._history),
            }

    def reset(self):
        with self._lock:
            self._session_id = None
            self._parent_session_id = None
            self._pending_resume = None
            self._pending_fork = False
            self._history = []
