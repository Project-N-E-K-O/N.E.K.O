from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.models import TutorReply
from plugin.sdk.plugin import Err, Ok


pytestmark = pytest.mark.unit


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None

    def exception(self, *_args, **_kwargs) -> None:
        return None


class _Ctx:
    plugin_id = "study_companion"
    metadata: dict[str, object] = {}
    bus = None
    run_id = ""

    def __init__(self, plugin_dir: Path) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text(
            "[plugin]\nid='study_companion'\n", encoding="utf-8"
        )
        self._config = {
            "study": {"auto_open_ui": False},
            "study_companion": {"communication": {"enabled": False}},
        }
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(
        self, profile_name: str | None = None, timeout: float = 5.0
    ):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        self._config = {**self._config, **dict(updates or {})}
        return {"config": self._config}

    async def query_plugins(self, filters, timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **_kwargs):
        return {}

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **_kwargs):
        return {"ok": True}

    async def run_update_async(self, **_kwargs):
        return {"ok": True}

    async def export_push(self, **_kwargs):
        return {"ok": True}

    async def finish(self, **_kwargs):
        return {"ok": True}

    def push_message(self, **_kwargs):
        return {"ok": True}

    def update_status(self, _status) -> None:
        return None


def _scope_request(topic: Mapping[str, Any], *, topic_only: bool = False) -> dict[str, Any]:
    request = {
        "schema_version": 1,
        "mode": "explicit_topic" if topic_only else "explicit_scope",
        "stage": str(topic.get("stage") or ""),
        "subject": str(topic.get("subject") or ""),
        "course_family": str(topic.get("course_family") or ""),
        "chapter": str(topic.get("chapter") or ""),
        "unit": str(topic.get("unit") or ""),
    }
    if topic_only:
        request["topic_id"] = str(topic.get("id") or "")
    return request


def _two_distinct_topics(plugin: StudyCompanionPlugin) -> tuple[dict[str, Any], dict[str, Any]]:
    topics = plugin._store.list_topics(5000)
    assert topics, "knowledge graph seeds must be available for scope API tests"
    first = dict(topics[0])
    first_scope = _scope_request(first)
    second = next(
        dict(topic)
        for topic in topics[1:]
        if any(
            str(topic.get(key) or "") != str(first_scope.get(key) or "")
            for key in ("stage", "subject", "course_family", "chapter", "unit")
        )
    )
    return first, second


async def _start_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> StudyCompanionPlugin:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    plugin = StudyCompanionPlugin(_Ctx(tmp_path))
    started = await plugin.startup()
    assert isinstance(started, Ok)
    return plugin


@pytest.mark.asyncio
async def test_scope_set_get_clear_revisions_survive_plugin_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_plugin = await _start_plugin(tmp_path, monkeypatch)
    first_topic, _ = _two_distinct_topics(first_plugin)
    request = _scope_request(first_topic)
    try:
        first_set = await first_plugin.study_set_practice_scope(scope=request)
        second_set = await first_plugin.study_set_practice_scope(scope=request)

        assert isinstance(first_set, Ok)
        assert isinstance(second_set, Ok)
        assert first_set.value["scope_revision"] == 1
        assert second_set.value["scope_revision"] == 2
        assert first_set.value["scope"]["scope_key"] == second_set.value["scope"]["scope_key"]
        assert "eligible_topic_ids" not in second_set.value["scope"]
        persisted_key = second_set.value["scope"]["scope_key"]
    finally:
        await first_plugin.shutdown()

    restarted = await _start_plugin(tmp_path, monkeypatch)
    try:
        restored = await restarted.study_get_practice_scope()
        assert isinstance(restored, Ok)
        assert restored.value["active"] is True
        assert restored.value["scope_revision"] == 2
        assert restored.value["scope"]["scope_key"] == persisted_key

        cleared = await restarted.study_clear_practice_scope()
        assert isinstance(cleared, Ok)
        assert cleared.value == {"active": False, "scope": {}, "scope_revision": 3}
    finally:
        await restarted.shutdown()

    restarted_after_clear = await _start_plugin(tmp_path, monkeypatch)
    try:
        empty = await restarted_after_clear.study_get_practice_scope()
        assert isinstance(empty, Ok)
        assert empty.value == {"active": False, "scope": {}, "scope_revision": 3}
    finally:
        await restarted_after_clear.shutdown()


@pytest.mark.asyncio
async def test_scope_with_no_matching_topics_is_rejected_without_revision_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin.study_set_practice_scope(
            scope={
                "schema_version": 1,
                "mode": "explicit_scope",
                "stage": "junior_high",
                "subject": "math",
                "chapter": "__missing_chapter__",
                "unit": "__missing_unit__",
            }
        )

        assert isinstance(result, Err)
        assert result.error.code == "NO_TOPICS_IN_SCOPE"
        current = await plugin.study_get_practice_scope()
        assert isinstance(current, Ok)
        assert current.value == {"active": False, "scope": {}, "scope_revision": 0}
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_oversized_scope_is_rejected_instead_of_silently_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    requested_limits: list[int] = []

    def oversized_topics(limit=100, *_args, **_kwargs):
        requested_limits.append(int(limit))
        return [{"id": f"topic-{index}"} for index in range(limit)]

    monkeypatch.setattr(plugin._store, "list_topics", oversized_topics)
    try:
        result = await plugin.study_set_practice_scope(
            scope={
                "schema_version": 1,
                "mode": "explicit_scope",
                "stage": "junior_high",
                "subject": "math",
            }
        )

        assert isinstance(result, Err)
        assert result.error.code == "PRACTICE_SCOPE_TOO_LARGE"
        assert requested_limits == [5001]
        assert plugin._state.practice_scope_revision == 0
        assert plugin._state.active_practice_scope == {}
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_scope_query_matches_noncanonical_machine_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    try:
        plugin._store.upsert_topic(
            {
                "id": "normalized-scope-topic",
                "name": "Normalized scope topic",
                "stage": "junior-high",
                "subject": "computer science",
                "chapter": "Normalization",
                "unit": "Aliases",
                "source": "runtime",
            }
        )

        selected = await plugin.study_set_practice_scope(
            scope={
                "schema_version": 1,
                "mode": "explicit_topic",
                "stage": "junior-high",
                "subject": "computer science",
                "chapter": "Normalization",
                "unit": "Aliases",
                "topic_id": "normalized-scope-topic",
            }
        )

        assert isinstance(selected, Ok)
        assert selected.value["scope"]["stage"] == "junior_high"
        assert selected.value["scope"]["subject"] == "computer_science"
        assert selected.value["scope"]["topic_id"] == "normalized-scope-topic"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_explicit_topic_is_loaded_by_id_before_the_scope_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    topic, _ = _two_distinct_topics(plugin)
    monkeypatch.setattr(plugin._store, "list_topics", lambda *args, **kwargs: [])
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(topic, topic_only=True)
        )

        assert isinstance(selected, Ok)
        assert selected.value["scope"]["topic_id"] == str(topic["id"])
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_scope_mutation_rolls_back_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    topic, _ = _two_distinct_topics(plugin)
    request = _scope_request(topic, topic_only=True)
    real_save_state = plugin._store.save_state
    try:
        def fail_persist(_state) -> None:
            raise OSError("simulated persistence failure")

        monkeypatch.setattr(plugin._store, "save_state", fail_persist)
        failed_set = await plugin.study_set_practice_scope(scope=request)
        assert isinstance(failed_set, Err)
        assert plugin._state.active_practice_scope == {}
        assert plugin._state.practice_scope_revision == 0

        monkeypatch.setattr(plugin._store, "save_state", real_save_state)
        selected = await plugin.study_set_practice_scope(scope=request)
        assert isinstance(selected, Ok)
        assert selected.value["scope_revision"] == 1

        monkeypatch.setattr(plugin._store, "save_state", fail_persist)
        failed_clear = await plugin.study_clear_practice_scope()
        assert isinstance(failed_clear, Err)
        assert plugin._state.active_practice_scope["scope_key"] == (
            selected.value["scope"]["scope_key"]
        )
        assert plugin._state.practice_scope_revision == 1
    finally:
        monkeypatch.setattr(plugin._store, "save_state", real_save_state)
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_new_user_scope_context_cold_starts_from_a_seed_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    first_topic, _ = _two_distinct_topics(plugin)
    request = _scope_request(first_topic)
    try:
        matching = plugin._store.list_topics(
            5000,
            request["subject"],
            request["stage"],
            chapter=request["chapter"],
            unit=request["unit"],
            course_family=request["course_family"] or None,
        )
        eligible_ids = {str(topic["id"]) for topic in matching}
        assert eligible_ids
        assert plugin._store.count_tracked_mastery_topics() == 0

        selected = await plugin.study_set_practice_scope(scope=request)
        context = await plugin.study_question_context()

        assert isinstance(selected, Ok)
        assert isinstance(context, Ok)
        assert context.value["no_data"] is False
        assert context.value["selection_context_id"]
        assert context.value["selected_topic_id"] in eligible_ids
        assert context.value["scope_key"] == selected.value["scope"]["scope_key"]
        assert context.value["scope_revision"] == 1
        assert context.value["practice_scope"] == selected.value["scope"]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_scope_context_queries_topics_before_applying_the_global_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    inside, _ = _two_distinct_topics(plugin)
    original_list_topics = plugin._store.list_topics
    request = _scope_request(inside)
    eligible_ids = {
        str(topic["id"])
        for topic in original_list_topics(
            5000,
            request["subject"],
            request["stage"],
            chapter=request["chapter"],
            unit=request["unit"],
            course_family=request["course_family"] or None,
        )
    }

    def list_topics(
        limit=100,
        subject=None,
        stage=None,
        *,
        chapter=None,
        unit=None,
        course_family=None,
    ):
        if not any((subject, stage, chapter, unit, course_family)):
            return [{"id": "globally-earlier-topic"}]
        return original_list_topics(
            limit,
            subject,
            stage,
            chapter=chapter,
            unit=unit,
            course_family=course_family,
        )

    monkeypatch.setattr(plugin._store, "list_topics", list_topics)
    try:
        selected = await plugin.study_set_practice_scope(scope=request)
        context = await plugin.study_question_context()

        assert isinstance(selected, Ok)
        assert isinstance(context, Ok)
        assert context.value["selected_topic_id"] in eligible_ids
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_scope_context_queries_mastery_for_eligible_topics_before_global_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    topics = plugin._store.list_topics(5000)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for topic in topics:
        key = tuple(
            str(topic.get(field) or "")
            for field in ("stage", "subject", "course_family", "chapter", "unit")
        )
        groups.setdefault(key, []).append(topic)
    scope_topics = next(items for items in groups.values() if len(items) >= 2)
    tracked_topic = scope_topics[0]
    plugin._store.append_mastery_snapshot(
        {
            "topic_id": tracked_topic["id"],
            "mastery": 0.75,
            "accuracy": 0.8,
            "recency": 0.7,
            "consistency": 0.6,
            "confidence": 0.9,
            "level": "learning",
            "attempts": 3,
            "flags": [],
        }
    )
    monkeypatch.setattr(
        plugin._store,
        "list_mastery_overview",
        lambda limit=20: [{"topic_id": "globally-earlier-topic", "mastery": 0.1}],
    )
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(tracked_topic)
        )
        scope = plugin._resolve_active_practice_scope()
        assert scope is not None
        params = plugin._scoped_question_params(scope)

        assert isinstance(selected, Ok)
        mastery = params["mastery_overview"]
        assert {str(item["topic_id"]) for item in mastery} == {str(tracked_topic["id"])}
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_all_broad_course_modules_cold_start_without_history_or_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    try:
        topics = plugin._store.list_topics(5000)
        broad_requests: dict[tuple[str, ...], dict[str, Any]] = {}
        for topic in topics:
            stage = str(topic.get("stage") or "")
            subject = str(topic.get("subject") or "")
            course_family = str(topic.get("course_family") or "")
            if stage == "college":
                key = (stage, course_family)
                request = {
                    "mode": "explicit_scope",
                    "stage": stage,
                    "subject": subject,
                    "course_family": course_family,
                }
            else:
                key = (stage, subject)
                request = {
                    "mode": "explicit_scope",
                    "stage": stage,
                    "subject": subject,
                }
            broad_requests.setdefault(key, request)

        assert len(broad_requests) == 30
        assert plugin._store.count_tracked_mastery_topics() == 0
        for request in broad_requests.values():
            selected = await plugin.study_set_practice_scope(scope=request)
            context = await plugin.study_question_context()

            assert isinstance(selected, Ok), request
            assert isinstance(context, Ok), request
            assert context.value["no_data"] is False
            selected_topic = plugin._store.get_topic(
                context.value["selected_topic_id"]
            )
            assert selected_topic is not None
            for field in ("stage", "subject", "course_family"):
                expected = str(selected.value["scope"].get(field) or "")
                if expected:
                    assert str(selected_topic.get(field) or "") == expected
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_restarted_plugin_recreates_ephemeral_context_from_saved_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    topic, _ = _two_distinct_topics(plugin)
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(topic, topic_only=True)
        )
        before_restart = await plugin.study_question_context()
        assert isinstance(selected, Ok)
        assert isinstance(before_restart, Ok)
        old_token = before_restart.value["selection_context_id"]
        scope_key = selected.value["scope"]["scope_key"]
    finally:
        await plugin.shutdown()

    restarted = await _start_plugin(tmp_path, monkeypatch)
    try:
        restored = await restarted.study_question_context()

        assert isinstance(restored, Ok)
        assert restored.value["scope_key"] == scope_key
        assert restored.value["scope_revision"] == 1
        assert restored.value["selection_context_id"]
        assert restored.value["selection_context_id"] != old_token
        assert restored.value["selected_topic_id"] == str(topic["id"])
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_changing_scope_rejects_an_unconsumed_old_selection_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    first_topic, second_topic = _two_distinct_topics(plugin)
    try:
        first_set = await plugin.study_set_practice_scope(
            scope=_scope_request(first_topic, topic_only=True)
        )
        context = await plugin.study_question_context()
        second_set = await plugin.study_set_practice_scope(
            scope=_scope_request(second_topic, topic_only=True)
        )

        assert isinstance(first_set, Ok)
        assert isinstance(context, Ok)
        assert isinstance(second_set, Ok)
        assert context.value["scope_revision"] == 1
        assert second_set.value["scope_revision"] == 2

        stale = await plugin.study_generate_targeted_question(
            selection_context_id=context.value["selection_context_id"]
        )
        assert isinstance(stale, Err)
        assert stale.error.code == "SELECTION_SCOPE_CHANGED"
    finally:
        await plugin.shutdown()


@pytest.mark.parametrize(
    ("candidate_key", "candidate_value"),
    [
        ("retry_wrong_question", {"topic_id": "{outside}"}),
        ("due_reviews", [{"topic_id": "{outside}"}]),
        ("weak_topics", [{"topic_id": "{outside}", "name": "outside"}]),
    ],
)
@pytest.mark.asyncio
async def test_out_of_scope_adaptive_candidates_never_override_explicit_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_key: str,
    candidate_value: object,
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    inside, outside = _two_distinct_topics(plugin)
    inside_id = str(inside["id"])
    outside_id = str(outside["id"])
    rendered_candidate = candidate_value
    if isinstance(candidate_value, dict):
        rendered_candidate = {
            key: (outside_id if value == "{outside}" else value)
            for key, value in candidate_value.items()
        }
    elif isinstance(candidate_value, list):
        rendered_candidate = [
            {
                key: (outside_id if value == "{outside}" else value)
                for key, value in item.items()
            }
            for item in candidate_value
        ]
    params = {
        "target_topic_id": outside_id,
        "target_topic": outside,
        "retry_wrong_question": {},
        "retry_wrong_questions": [],
        "due_reviews": [],
        "weak_topics": [],
        "candidate_evidence": [],
        "suggested_difficulty": 2,
    }
    params[candidate_key] = rendered_candidate
    monkeypatch.setattr(
        plugin._knowledge_tracker,
        "preview_next_question_params",
        lambda _topic_id="", **_kwargs: dict(params),
    )
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(inside, topic_only=True)
        )
        context = await plugin.study_question_context()

        assert isinstance(selected, Ok)
        assert isinstance(context, Ok)
        assert context.value["selected_topic_id"] == inside_id
        assert context.value["selected_topic_id"] != outside_id
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_explicit_topic_uses_diagnostic_after_retry_and_due_not_weak_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    inside, _ = _two_distinct_topics(plugin)
    inside_id = str(inside["id"])
    monkeypatch.setattr(
        plugin._knowledge_tracker,
        "get_weak_topics",
        lambda limit=5, **_kwargs: [
            {"topic_id": inside_id, "name": inside["name"]}
        ],
    )
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(inside, topic_only=True)
        )
        context = await plugin.study_question_context()

        assert isinstance(selected, Ok)
        assert isinstance(context, Ok)
        assert context.value["selected_topic_id"] == inside_id
        assert context.value["selection_reason"] == "recommended"
    finally:
        await plugin.shutdown()


class _AgentMustNotEvaluate:
    async def answer_evaluate(self, **_kwargs):
        raise AssertionError("forged topic must be rejected before invoking the LLM")


class _ScopedEvaluationAgent:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    async def answer_evaluate(self, **kwargs):
        correct = self.verdict == "correct"
        return TutorReply(
            operation="answer_evaluate",
            input_text=str(kwargs.get("answer") or ""),
            reply=self.verdict,
            payload={
                "verdict": self.verdict,
                "score": 100 if correct else 0,
                "error_type": "none" if correct else "concept_gap",
                "feedback": self.verdict,
                "topic": "forged_outside_topic",
            },
            created_at="2026-08-13T00:00:00Z",
        )


@pytest.mark.parametrize("verdict", ["wrong", "correct"])
@pytest.mark.asyncio
async def test_answer_cycle_keeps_retry_and_progression_inside_active_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    topics = plugin._store.list_topics(5000)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for topic in topics:
        key = tuple(
            str(topic.get(field) or "")
            for field in ("stage", "subject", "course_family", "chapter", "unit")
        )
        groups.setdefault(key, []).append(topic)
    scope_topics = next(items for items in groups.values() if len(items) >= 2)
    first_topic = scope_topics[0]
    request = _scope_request(first_topic)
    real_agent = plugin._agent
    plugin._agent = _ScopedEvaluationAgent(verdict)
    try:
        selected = await plugin.study_set_practice_scope(scope=request)
        assert isinstance(selected, Ok)
        scope = selected.value["scope"]
        async with plugin._lock:
            plugin._state.current_question = {
                "question": "Scoped diagnostic",
                "answer": "expected",
                "question_id": f"scoped-{verdict}-q",
                "attempt_id": f"scoped-{verdict}-a",
                "selected_topic_id": first_topic["id"],
                "topic": first_topic["id"],
                "scope_key": scope["scope_key"],
                "scope_revision": scope["scope_revision"],
                "scope_topic_count": len(scope_topics),
            }

        evaluated = await plugin.study_evaluate_answer(
            answer="expected" if verdict == "correct" else "wrong answer",
            question_id=f"scoped-{verdict}-q",
            attempt_id=f"scoped-{verdict}-a",
            selected_topic_id=first_topic["id"],
        )
        next_context = await plugin.study_question_context()

        assert isinstance(evaluated, Ok)
        assert evaluated.value["topic"] == first_topic["id"]
        assert isinstance(next_context, Ok)
        eligible_ids = {str(topic["id"]) for topic in scope_topics}
        assert next_context.value["selected_topic_id"] in eligible_ids
        if verdict == "wrong":
            assert next_context.value["selected_topic_id"] == first_topic["id"]
            assert next_context.value["selection_reason"] == "retry"
        else:
            assert next_context.value["selected_topic_id"] != first_topic["id"]
    finally:
        plugin._agent = real_agent
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_evaluate_rejects_client_selected_topic_that_differs_from_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = await _start_plugin(tmp_path, monkeypatch)
    inside, outside = _two_distinct_topics(plugin)
    real_agent = plugin._agent
    try:
        selected = await plugin.study_set_practice_scope(
            scope=_scope_request(inside, topic_only=True)
        )
        assert isinstance(selected, Ok)
        scope = selected.value["scope"]
        plugin._agent = _AgentMustNotEvaluate()
        async with plugin._lock:
            plugin._state.current_question = {
                "question": "What is the answer?",
                "answer": "42",
                "question_id": "question-scope-1",
                "attempt_id": "attempt-scope-1",
                "selected_topic_id": str(inside["id"]),
                "topic": str(inside["id"]),
                "scope_key": scope["scope_key"],
                "scope_revision": scope["scope_revision"],
            }

        result = await plugin.study_evaluate_answer(
            answer="42",
            question_id="question-scope-1",
            attempt_id="attempt-scope-1",
            selected_topic_id=str(outside["id"]),
        )

        assert isinstance(result, Err)
        assert result.error.code == "QUESTION_MISMATCH"
    finally:
        plugin._agent = real_agent
        await plugin.shutdown()
