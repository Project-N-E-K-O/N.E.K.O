"""
Agent Session Manager — manages independent sessions for CUA and Browser Use agents.

Each session tracks:
  - Which agent type owns it (cua / browser_use)
  - When it was created and last used
  - A compact history of instructions and results
  - An auto-expiry TTL so idle sessions are cleaned up

Sessions are completely independent from the conversation agent.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

_DEFAULT_TTL_SECONDS = 600  # 10 minutes


@dataclass
class TaskRecord:
    """One instruction + result within a session."""
    instruction: str
    result_summary: str = ""
    success: Optional[bool] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentSession:
    """Represents a single agent session."""
    session_id: str
    agent_type: str  # "cua" or "browser_use"
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    task_history: List[TaskRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        """Update last_active timestamp."""
        self.last_active = time.time()

    def add_task(self, instruction: str) -> None:
        """Record that a new task was started."""
        self.task_history.append(TaskRecord(instruction=instruction))
        self.touch()

    def complete_task(self, result_summary: str, success: bool) -> None:
        """Mark the most recent task as completed."""
        if self.task_history:
            last = self.task_history[-1]
            last.result_summary = result_summary
            last.success = success
        self.touch()

    def get_context_summary(self, max_items: int = 5) -> str:
        """Return a compact text summary of recent tasks for LLM context."""
        recent = self.task_history[-max_items:]
        if not recent:
            return ""
        lines = []
        for i, t in enumerate(recent, 1):
            status = "OK" if t.success else ("FAIL" if t.success is False else "PENDING")
            summary = t.result_summary[:120] if t.result_summary else "(no result yet)"
            lines.append(f"  {i}. [{status}] {t.instruction[:80]} → {summary}")
        return f"Session {self.session_id[:8]} ({self.agent_type}), {len(self.task_history)} tasks:\n" + "\n".join(lines)

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active


class AgentSessionManager:
    """Manages agent sessions with auto-expiry.

    Usage:
        mgr = AgentSessionManager()
        sid = mgr.create_session("browser_use")
        session = mgr.get_session(sid)
        session.add_task("搜索今天的新闻")
        # ... after task completes ...
        session.complete_task("Found 5 results", success=True)
        mgr.close_session(sid)
    """

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._sessions: Dict[str, AgentSession] = {}
        self._ttl = ttl_seconds

    def create_session(self, agent_type: str, session_id: Optional[str] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> str:
        """Create a new session and return its ID."""
        self._expire_idle()
        sid = session_id or str(uuid.uuid4())
        session = AgentSession(
            session_id=sid,
            agent_type=agent_type,
            metadata=metadata or {},
        )
        self._sessions[sid] = session
        logger.debug("[SessionMgr] Created session %s (%s)", sid[:8], agent_type)
        return sid

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        """Retrieve a session by ID, or None if expired/missing."""
        self._expire_idle()
        session = self._sessions.get(session_id)
        if session is not None:
            session.touch()
        return session

    def get_or_create(self, session_id: Optional[str], agent_type: str) -> AgentSession:
        """Get existing session or create a new one."""
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return existing
        new_sid = self.create_session(agent_type, session_id=session_id)
        return self._sessions[new_sid]

    def close_session(self, session_id: str) -> None:
        """Explicitly close and remove a session."""
        removed = self._sessions.pop(session_id, None)
        if removed:
            logger.debug("[SessionMgr] Closed session %s", session_id[:8])

    def list_sessions(self, agent_type: Optional[str] = None) -> List[AgentSession]:
        """List active sessions, optionally filtered by agent_type."""
        self._expire_idle()
        sessions = list(self._sessions.values())
        if agent_type:
            sessions = [s for s in sessions if s.agent_type == agent_type]
        return sessions

    def _expire_idle(self) -> None:
        """Remove sessions that have been idle beyond TTL."""
        expired = [sid for sid, s in self._sessions.items() if s.idle_seconds > self._ttl]
        for sid in expired:
            logger.debug("[SessionMgr] Expired idle session %s", sid[:8])
            del self._sessions[sid]


# Singleton instance
_session_manager: Optional[AgentSessionManager] = None


def get_session_manager() -> AgentSessionManager:
    """Get or create the global AgentSessionManager singleton."""
    global _session_manager
    if _session_manager is None:
        _session_manager = AgentSessionManager()
    return _session_manager
