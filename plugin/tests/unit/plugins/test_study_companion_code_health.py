from __future__ import annotations

from pathlib import Path

import pytest

from plugin.tests.unit.plugins import test_pomodoro_timer as _pomodoro_tests
from plugin.tests.unit.plugins import test_study_habit_store as _habit_tests
from plugin.plugins.study_companion.constants import (
    LLM_OPERATION_EXPAND_NOTE,
    LLM_OPERATION_SUMMARIZE_TO_NOTE,
    SUPPORTED_LLM_OPERATIONS,
)

pytestmark = pytest.mark.unit

_STUDY_COMPANION_ROOT = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"


def test_code_health_modules_are_flat_for_dependency_direction() -> None:
    for child_dir in ("plugin_entries", "store", "tutor_llm_agent"):
        assert not (_STUDY_COMPANION_ROOT / child_dir).exists()

    flat_split_modules = ("entry_", "store_", "tutor_llm_agent_")
    flat_main_modules = {"store.py", "tutor_llm_agent.py"}
    for path in _STUDY_COMPANION_ROOT.glob("*.py"):
        if not (
            path.name.startswith(flat_split_modules) or path.name in flat_main_modules
        ):
            continue
        source = path.read_text(encoding="utf-8")
        assert "from .." not in source
        assert "import .." not in source


def test_entry_exceptions_use_traceback_logging_helper() -> None:
    for path in _STUDY_COMPANION_ROOT.glob("entry_*.py"):
        if path.name == "entry_common.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "return Err(SdkError(str(exc)))" not in source, path.name
        assert "return Err(SdkError(f\"" not in source, path.name
        assert "return Err(SdkError(f'" not in source, path.name


def test_tutor_learning_support_is_not_split_by_mro_dependency() -> None:
    assert not (_STUDY_COMPANION_ROOT / "entry_tutor_learning_support.py").exists()
    plugin_source = (_STUDY_COMPANION_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "_TutorLearningSupportMixin" not in plugin_source


def test_awareness_uses_activity_public_surface_only() -> None:
    forbidden_imports = (
        "main_logic.activity.state_machine",
        "main_logic.activity.system_signals",
        "get_snapshot_sync",
    )
    for path in _STUDY_COMPANION_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in forbidden_imports:
            assert forbidden not in source, path.name


def test_screen_hash_dependency_and_fields_are_removed() -> None:
    repo_root = _STUDY_COMPANION_ROOT.parents[2]
    forbidden_dependencies = ("image" + "hash",)
    for path in (repo_root / "requirements.txt", repo_root / "pyproject.toml"):
        source = path.read_text(encoding="utf-8")
        for forbidden in forbidden_dependencies:
            assert forbidden.lower() not in source.lower(), path.name

    forbidden_fields = (
        "thumbnail_" + "phash",
        "has_content" + "_change",
        "_thumbnail" + "_hash",
    )
    for path in _STUDY_COMPANION_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in forbidden_fields:
            assert forbidden not in source, path.name


def test_notebook_llm_operations_are_registered() -> None:
    assert LLM_OPERATION_EXPAND_NOTE in SUPPORTED_LLM_OPERATIONS
    assert LLM_OPERATION_SUMMARIZE_TO_NOTE in SUPPORTED_LLM_OPERATIONS


test_store_transaction_rolls_back_and_json_loads_is_public = (
    _habit_tests.test_store_transaction_rolls_back_and_json_loads_is_public
)
test_store_purge_all_clears_user_data_tables = (
    _habit_tests.test_store_purge_all_clears_user_data_tables
)
test_habit_store_creates_goals_and_cascades_focus_sessions = (
    _habit_tests.test_habit_store_creates_goals_and_cascades_focus_sessions
)
test_checkin_manager_tracks_streaks_makeups_and_session_derived_progress = _habit_tests.test_checkin_manager_tracks_streaks_makeups_and_session_derived_progress
test_habit_data_stays_out_of_public_knowledge_export = (
    _habit_tests.test_habit_data_stays_out_of_public_knowledge_export
)
test_checkin_streak_is_not_truncated_at_default_checked_dates_limit = (
    _habit_tests.test_checkin_streak_is_not_truncated_at_default_checked_dates_limit
)
test_memory_habit_bridge_updates_deck_goals_idempotently = (
    _habit_tests.test_memory_habit_bridge_updates_deck_goals_idempotently
)
test_memory_habit_bridge_summarizes_recitation_and_deck_focus = (
    _habit_tests.test_memory_habit_bridge_summarizes_recitation_and_deck_focus
)
test_memory_habit_bridge_reuses_existing_focus_goal_without_shrinking = (
    _habit_tests.test_memory_habit_bridge_reuses_existing_focus_goal_without_shrinking
)
test_memory_habit_bridge_summary_uses_configured_local_day = (
    _habit_tests.test_memory_habit_bridge_summary_uses_configured_local_day
)
test_memory_habit_bridge_summary_includes_due_only_decks = (
    _habit_tests.test_memory_habit_bridge_summary_includes_due_only_decks
)

test_pomodoro_timer_completes_focus_then_short_break_without_counting_break_minutes = _pomodoro_tests.test_pomodoro_timer_completes_focus_then_short_break_without_counting_break_minutes
test_pomodoro_timer_uses_long_break_interval_and_supports_cancel = (
    _pomodoro_tests.test_pomodoro_timer_uses_long_break_interval_and_supports_cancel
)
test_pomodoro_stop_completes_expired_focus_before_cancelling = (
    _pomodoro_tests.test_pomodoro_stop_completes_expired_focus_before_cancelling
)
test_pomodoro_stop_is_noop_when_timer_is_not_active = (
    _pomodoro_tests.test_pomodoro_stop_is_noop_when_timer_is_not_active
)
test_pomodoro_timer_respects_disabled_session_derived_checkins = (
    _pomodoro_tests.test_pomodoro_timer_respects_disabled_session_derived_checkins
)
test_pomodoro_timer_uses_configured_timezone_for_session_derived_checkins = _pomodoro_tests.test_pomodoro_timer_uses_configured_timezone_for_session_derived_checkins
test_pomodoro_start_does_not_mutate_state_when_initial_persistence_fails = _pomodoro_tests.test_pomodoro_start_does_not_mutate_state_when_initial_persistence_fails
test_pomodoro_completion_does_not_duplicate_progress_when_checkin_retries = _pomodoro_tests.test_pomodoro_completion_does_not_duplicate_progress_when_checkin_retries
test_pomodoro_completion_stays_retryable_when_persistence_fails = (
    _pomodoro_tests.test_pomodoro_completion_stays_retryable_when_persistence_fails
)
