from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from plugin.plugins.study_companion.checkin_manager import CheckinManager
from plugin.plugins.study_companion.memory_deck_store import MemoryDeckStore
from plugin.plugins.study_companion.memory_habit_bridge import MemoryHabitBridge
from plugin.plugins.study_companion.study_habit_store import StudyHabitStore
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.store_notebook import NotebookStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _study_store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_store_transaction_rolls_back_and_json_loads_is_public(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                    ("rollback-key", '{"private": true}', 0.0),
                )
                raise RuntimeError("rollback")

        with store.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM kv WHERE key = ?", ("rollback-key",)
            ).fetchone()

        assert row is None
        assert store.json_loads("{bad json", {"fallback": True}) == {"fallback": True}
    finally:
        store.close()


def test_store_purge_all_clears_user_data_tables(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        notebooks = NotebookStore(store)
        bridge = MemoryHabitBridge(store=store, memory=memory, habits=habits)

        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                ("custom-private", '{"value": 1}', 0.0),
            )
        goal = habits.create_goal(
            date="2026-05-24",
            target_type="custom",
            subject="private",
            target_amount=1,
            unit="task",
        )
        focus = habits.create_focus_session(
            goal_id=goal["id"],
            mode="focus",
            planned_minutes=25,
            started_at="2026-05-24T09:00:00Z",
        )
        habits.finish_focus_session(
            focus["id"],
            ended_at="2026-05-24T09:25:00Z",
            actual_minutes=25,
            status="completed",
        )
        habits.record_checkin(date="2026-05-24")
        deck = memory.create_deck(name="Private Deck", deck_type="word")
        word = memory.add_word(deck_id=deck["id"], word="secret", meaning="private")[
            "item"
        ]
        bridge.create_deck_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            target_amount=1,
            unit="cards",
        )
        notebook = notebooks.create_notebook(name="Private Notes")
        notebooks.create_note(
            notebook_id=notebook.id,
            title="Private note",
            content="reset should remove this note",
        )
        reviewed = memory.review_item(item_id=word["id"], rating="good")
        bridge.apply_review_progress(reviewed, date="2026-05-24")

        deleted = store.purge_all()

        assert deleted["kv"] >= 1
        assert deleted["daily_goals"] >= 1
        assert deleted["memory_habit_progress"] >= 1
        assert deleted["notes"] >= 1
        assert deleted["notebooks"] >= 1
        with store.transaction() as conn:
            table_queries = {
                "kv": "SELECT COUNT(*) FROM kv",
                "daily_goals": "SELECT COUNT(*) FROM daily_goals",
                "checkins": "SELECT COUNT(*) FROM checkins",
                "focus_sessions": "SELECT COUNT(*) FROM focus_sessions",
                "decks": "SELECT COUNT(*) FROM decks",
                "memory_items": "SELECT COUNT(*) FROM memory_items",
                "memory_fsrs_cards": "SELECT COUNT(*) FROM memory_fsrs_cards",
                "review_records": "SELECT COUNT(*) FROM review_records",
                "memory_habit_progress": "SELECT COUNT(*) FROM memory_habit_progress",
                "notes": "SELECT COUNT(*) FROM notes",
                "notebooks": "SELECT COUNT(*) FROM notebooks",
            }
            for table, query in table_queries.items():
                count = conn.execute(query).fetchone()[0]
                assert count == 0, table
    finally:
        store.close()


def test_habit_store_creates_goals_and_cascades_focus_sessions(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        goal = habits.create_goal(
            date="2026-05-22",
            target_type="subject",
            subject="math",
            target_amount=2,
            unit="pomodoro",
        )
        focus = habits.create_focus_session(
            goal_id=goal["id"],
            mode="focus",
            planned_minutes=25,
            started_at="2026-05-22T23:50:00+08:00",
        )
        habits.finish_focus_session(
            focus["id"],
            ended_at="2026-05-23T00:15:00+08:00",
            actual_minutes=25,
            status="completed",
        )

        assert habits.list_goals(date="2026-05-22")[0]["progress_amount"] == 0
        assert habits.list_focus_sessions(date="2026-05-22")[0]["actual_minutes"] == 25
        assert habits.list_focus_sessions(date="2026-05-23") == []

        habits.delete_goal(goal["id"])

        assert habits.list_goals(date="2026-05-22") == []
        assert habits.list_focus_sessions(date="2026-05-22") == []
        assert habits.delete_goal("missing-goal") is False
    finally:
        store.close()


def test_checkin_manager_tracks_streaks_makeups_and_session_derived_progress(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        manager = CheckinManager(habits, makeup_window_days=3)
        goal = manager.create_goal(
            date="2026-05-20",
            target_type="subject",
            subject="math",
            target_amount=30,
            unit="minute",
        )

        manager.apply_session_progress(
            date="2026-05-20",
            duration_minutes=20,
            question_count=3,
            subject="math",
        )
        manager.manual_checkin(date="2026-05-21", today="2026-05-22", note="复习错题")
        manager.manual_checkin(date="2026-05-22", today="2026-05-22")
        manager.apply_session_progress(
            date="2026-05-20",
            duration_minutes=10,
            question_count=0,
            subject="math",
        )

        updated = habits.get_goal(goal["id"])
        status = manager.checkin_status(date="2026-05-22", today="2026-05-22")
        summary = manager.daily_summary(date="2026-05-20")

        assert updated is not None
        assert updated["progress_amount"] == 30
        assert updated["status"] == "completed"
        assert status["checked_in"] is True
        assert status["streak_days"] == 3
        assert status["makeup_window_days"] == 3
        assert summary["total_focus_minutes"] == 30
        assert summary["completed_goal_count"] == 1
        assert summary["weak_points"] == []
    finally:
        store.close()


def test_habit_data_stays_out_of_public_knowledge_export(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        habits.create_goal(
            date="2026-05-22",
            target_type="custom",
            subject="private",
            target_amount=1,
            unit="task",
            target_id="personal-plan",
        )

        exported = store.export_json()

        assert "daily_goals" not in exported
        assert "checkins" not in exported
        assert "focus_sessions" not in exported
        assert "personal-plan" not in str(exported)
    finally:
        store.close()


def test_checkin_streak_is_not_truncated_at_default_checked_dates_limit(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        manager = CheckinManager(habits, makeup_window_days=3)
        start = date(2025, 1, 1)
        for offset in range(405):
            habits.record_checkin(
                date=(start + timedelta(days=offset)).isoformat(),
                status="checked_in",
                source="manual",
            )

        status = manager.checkin_status(date="2026-02-09", today="2026-02-09")

        assert status["streak_days"] == 405
    finally:
        store.close()


def test_memory_habit_bridge_updates_deck_goals_idempotently(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        bridge = MemoryHabitBridge(store=store, memory=memory, habits=habits)
        deck = memory.create_deck(name="Exam Words", deck_type="word")
        word = memory.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
        )["item"]
        goal_payload = bridge.create_deck_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            target_amount=2,
            unit="cards",
        )

        reviewed = memory.review_item(item_id=word["id"], rating="good")
        progress = bridge.apply_review_progress(reviewed, date="2026-05-24")
        duplicate = bridge.apply_review_progress(reviewed, date="2026-05-24")
        goal = habits.get_goal(goal_payload["goal"]["id"])

        assert progress["applied"] == 1
        assert duplicate["applied"] == 0
        assert goal is not None
        assert goal["target_type"] == "deck"
        assert goal["target_id"] == deck["id"]
        assert goal["progress_amount"] == 1
        assert habits.list_checkins(date="2026-05-24")[0]["source"] == "session_derived"
    finally:
        store.close()


def test_memory_habit_bridge_summarizes_recitation_and_deck_focus(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        bridge = MemoryHabitBridge(store=store, memory=memory, habits=habits)
        deck = memory.create_deck(name="Textbook", deck_type="passage")
        imported = memory.import_passage(
            deck_id=deck["id"],
            title="Short Text",
            text="First sentence. Second sentence.",
        )
        attempt_goal = bridge.create_deck_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            target_amount=1,
            unit="attempts",
        )
        focus_goal = bridge.resolve_focus_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            focus_minutes=25,
        )
        focus = habits.create_focus_session(
            goal_id=focus_goal["goal"]["id"],
            mode="focus",
            planned_minutes=25,
            started_at="2026-05-24T09:00:00+08:00",
        )
        habits.finish_focus_session(
            focus["id"],
            ended_at="2026-05-24T09:25:00+08:00",
            actual_minutes=25,
            status="completed",
        )

        recited = memory.add_recitation_attempt(
            item_id=imported["items"][0]["id"],
            user_input_text="First sentence.",
        )
        with store.transaction() as conn:
            conn.execute(
                "UPDATE review_records SET reviewed_at = ? WHERE id = ?",
                ("2026-05-24 01:00:00", recited["review"]["review_record"]["id"]),
            )
            conn.execute(
                "UPDATE recitation_attempts SET reviewed_at = ? WHERE id = ?",
                ("2026-05-24 01:00:00", recited["attempt"]["id"]),
            )
        progress = bridge.apply_recitation_progress(recited, date="2026-05-24")
        summary = bridge.memory_summary(date="2026-05-24")
        attempt_goal_updated = habits.get_goal(attempt_goal["goal"]["id"])

        assert progress["applied"] == 1
        assert attempt_goal_updated is not None
        assert attempt_goal_updated["progress_amount"] == 1
        assert summary["reviewed_items"] == 1
        assert summary["recitation_attempts"] == 1
        assert summary["focus_minutes"] == 25
        assert summary["deck_count"] == 1
        assert summary["decks"][0]["deck_id"] == deck["id"]
    finally:
        store.close()


def test_memory_habit_bridge_reuses_existing_focus_goal_without_shrinking(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        bridge = MemoryHabitBridge(store=store, memory=memory, habits=habits)
        deck = memory.create_deck(name="Long Review", deck_type="word")
        existing = bridge.create_deck_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            target_amount=120,
            unit="minutes",
        )

        resolved = bridge.resolve_focus_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            focus_minutes=25,
        )
        goal = habits.get_goal(existing["goal"]["id"])

        assert resolved["created"] is False
        assert resolved["goal"]["id"] == existing["goal"]["id"]
        assert goal is not None
        assert goal["target_amount"] == 120
    finally:
        store.close()


def test_memory_habit_bridge_summary_uses_configured_local_day(
    tmp_path: Path,
) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        bridge = MemoryHabitBridge(
            store=store,
            memory=memory,
            habits=habits,
            checkin_timezone="Asia/Shanghai",
        )
        deck = memory.create_deck(name="Boundary Words", deck_type="word")
        word = memory.add_word(
            deck_id=deck["id"],
            word="boundary",
            meaning="edge",
        )["item"]
        bridge.create_deck_goal(
            date="2026-05-23",
            deck_id=deck["id"],
            target_amount=1,
            unit="cards",
        )
        bridge.create_deck_goal(
            date="2026-05-24",
            deck_id=deck["id"],
            target_amount=1,
            unit="cards",
        )
        reviewed = memory.review_item(item_id=word["id"], rating="good")
        review_id = int(reviewed["review_record"]["id"])
        with store.transaction() as conn:
            card_row = conn.execute(
                "SELECT card_data FROM memory_fsrs_cards WHERE item_id = ?",
                (word["id"],),
            ).fetchone()
            card = store.json_loads(card_row["card_data"], {})
            card["due"] = "2026-05-24T08:00:00Z"
            card["created_at"] = "2026-05-24T08:00:00Z"
            conn.execute(
                "UPDATE review_records SET reviewed_at = ? WHERE id = ?",
                ("2026-05-23 16:30:00", review_id),
            )
            conn.execute(
                """
                UPDATE memory_fsrs_cards
                SET card_data = ?, next_due = ?
                WHERE item_id = ?
                """,
                (
                    json.dumps(card, ensure_ascii=False, sort_keys=True),
                    "2026-05-24T08:00:00Z",
                    word["id"],
                ),
            )

        summary = bridge.memory_summary(date="2026-05-24")
        previous = bridge.memory_summary(date="2026-05-23")

        assert summary["reviewed_items"] == 1
        assert summary["due_remaining"] == 1
        assert summary["decks"][0]["deck_id"] == deck["id"]
        assert previous["reviewed_items"] == 0
        assert previous["due_remaining"] == 0
    finally:
        store.close()


def test_memory_habit_bridge_summary_includes_due_only_decks(tmp_path: Path) -> None:
    store = _study_store(tmp_path)
    try:
        habits = StudyHabitStore(store)
        memory = MemoryDeckStore(store)
        bridge = MemoryHabitBridge(
            store=store,
            memory=memory,
            habits=habits,
            checkin_timezone="Asia/Shanghai",
        )
        due_deck = memory.create_deck(name="Due Only", deck_type="word")
        future_deck = memory.create_deck(name="Future", deck_type="word")
        due_word = memory.add_word(
            deck_id=due_deck["id"],
            word="due",
            meaning="ready",
        )["item"]
        future_word = memory.add_word(
            deck_id=future_deck["id"],
            word="later",
            meaning="not ready",
        )["item"]

        with store.transaction() as conn:
            for item_id, due_at in (
                (due_word["id"], "2026-05-24T08:00:00Z"),
                (future_word["id"], "2026-05-25T08:00:00Z"),
            ):
                card_row = conn.execute(
                    "SELECT card_data FROM memory_fsrs_cards WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                card = store.json_loads(card_row["card_data"], {})
                card["due"] = due_at
                card["created_at"] = due_at
                conn.execute(
                    """
                    UPDATE memory_fsrs_cards
                    SET card_data = ?, next_due = ?
                    WHERE item_id = ?
                    """,
                    (
                        json.dumps(card, ensure_ascii=False, sort_keys=True),
                        due_at,
                        item_id,
                    ),
                )

        summary = bridge.memory_summary(date="2026-05-24")

        assert summary["deck_count"] == 1
        assert summary["due_remaining"] == 1
        assert summary["reviewed_items"] == 0
        assert summary["decks"][0]["deck_id"] == due_deck["id"]
        assert summary["decks"][0]["due_remaining"] == 1
    finally:
        store.close()
