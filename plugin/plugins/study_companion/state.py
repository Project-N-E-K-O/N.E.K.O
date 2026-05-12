from __future__ import annotations

from .models import MODE_CONCEPT_EXPLAIN, STATUS_STOPPED, StudyState


def build_initial_state(*, mode: str = MODE_CONCEPT_EXPLAIN) -> StudyState:
    return StudyState(status=STATUS_STOPPED, active_mode=mode)
