from __future__ import annotations

from .mode_manager import MODE_COMPANION, normalize_mode
from .models import STATUS_STOPPED, StudyState


def build_initial_state(*, mode: str = MODE_COMPANION) -> StudyState:
    return StudyState(status=STATUS_STOPPED, active_mode=normalize_mode(mode))
