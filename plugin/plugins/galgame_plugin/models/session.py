from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import DATA_SOURCE_BRIDGE_SDK
from .sanitizers import _int, _string


@dataclass(slots=True)
class SessionCandidate:
    game_id: str
    session_path: Path
    events_path: Path
    session: dict[str, Any]
    data_source: str = DATA_SOURCE_BRIDGE_SDK

    @property
    def session_id(self) -> str:
        return _string(self.session.get("session_id"))

    @property
    def last_seq(self) -> int:
        return max(0, _int(self.session.get("last_seq"), 0))

    @property
    def sort_key(self) -> tuple[str, str, int]:
        state = self.session.get("state")
        state_ts = _string(state.get("ts")) if isinstance(state, dict) else ""
        return (state_ts, _string(self.session.get("started_at")), self.last_seq)
