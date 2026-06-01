from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.plugins import study_companion as study_companion_module
from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.llm_prompts import (
    _compact_prompt_value,
    build_concept_explain_messages,
    build_operation_messages,
)
from plugin.plugins.study_companion.mode_manager import (
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    ModeManager,
    build_transition_phrase,
    handle_user_intent,
    mode_label,
    normalize_mode,
)
from plugin.plugins.study_companion.knowledge_quality import (
    KnowledgeCandidateStatus,
    KnowledgeCandidateType,
    KnowledgeEvidenceType,
    KnowledgeQualityStore,
)
from plugin.plugins.study_companion.knowledge_contribution import (
    PublicGraphContributionBuilder,
)
from plugin.plugins.study_companion.knowledge_tracker import KnowledgeTracker
from plugin.plugins.study_companion.models import (
    OcrSnapshot,
    StudyConfig,
    TutorReply,
    build_config,
)
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion import (
    study_capture_backends as study_capture_backends_module,
)
from plugin.plugins.study_companion.entry_common import _entry_exception_error
from plugin.plugins.study_companion.study_capture_backends import (
    PrintWindowCaptureBackend,
    PyAutoGuiCaptureBackend,
)
from plugin.plugins.study_companion.study_ocr_pipeline import (
    StudyCaptureProfile,
    StudyOcrPipeline,
)
from plugin.plugins.study_companion import service as study_service
from plugin.plugins.study_companion import tesseract_support as study_tesseract_support
from plugin.plugins.study_companion.screen_classifier import (
    ScreenClassification,
    classify_screen_from_ocr,
)
from plugin.plugins.study_companion.service import _available_tesseract_languages
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent, _JSONCorrector
from plugin.plugins.study_companion.ui_api import (
    build_knowledge_map_payload,
    build_open_ui_payload,
)
from plugin.server.application.plugins.ui_query_service import _build_surfaces_sync
from plugin.sdk.plugin import Err, Ok


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "study_companion"
    metadata = {}
    bus = None
    run_id = ""

    def __init__(self, plugin_dir: Path, config: dict[str, object]) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text(
            "[plugin]\nid='study_companion'\n", encoding="utf-8"
        )
        self._config = config
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self.status_updates: list[dict[str, object]] = []
        self.run_updates: list[dict[str, object]] = []
        self.pushed_messages: list[dict[str, object]] = []

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

    async def trigger_plugin_event(self, **kwargs):
        return {}

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        self.run_updates.append(dict(kwargs))
        return {"ok": True}

    async def run_update_async(self, **kwargs):
        self.run_updates.append(dict(kwargs))
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}

    def update_status(self, status):
        self.status_updates.append(dict(status))


def test_entry_exception_error_logs_traceback_and_preserves_sdk_error() -> None:
    plugin = SimpleNamespace(logger=_Logger())
    exc = RuntimeError("boom")

    result = _entry_exception_error(plugin, exc, operation="unit-test")

    assert isinstance(result, Err)
    assert "boom" in str(result.error)
    assert len(plugin.logger.warnings) == 1
    args, kwargs = plugin.logger.warnings[0]
    assert args[0] == "study entry failed: {}"
    assert args[1] == "unit-test"
    assert kwargs["exc_info"] == (RuntimeError, exc, exc.__traceback__)

    prefixed = _entry_exception_error(
        plugin,
        exc,
        operation="unit-test-prefixed",
        message="install failed: boom",
    )

    assert isinstance(prefixed, Err)
    assert str(prefixed.error) == "install failed: boom"


def _study_push_texts(ctx: _Ctx) -> list[str]:
    texts: list[str] = []
    for message in ctx.pushed_messages:
        if message.get("source") != "study_companion":
            continue
        parts = message.get("parts") or []
        if not parts:
            continue
        texts.append(str(parts[0].get("text") or ""))
    return texts


async def _drain_scheduled_events() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class _FakeOcrBackend:
    def __init__(self, result):
        self.result = result

    def extract_text(self, image):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeCaptureBackend:
    def __init__(self, image):
        self.image = image
        self.calls: list[tuple[object, object]] = []

    def capture_frame(self, target, profile):
        self.calls.append((target, profile))
        return self.image


class _FakeStudyOcrPipeline:
    def __init__(self, snapshot: OcrSnapshot) -> None:
        self.snapshot = snapshot

    def capture_snapshot(self) -> OcrSnapshot:
        return self.snapshot


class _FakeTutorAgent:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, dict[str, object], str]] = []
        self.evaluations: list[tuple[str, str, str, dict[str, object], str]] = []
        self.summaries: list[
            tuple[list[dict[str, object]], dict[str, object], str]
        ] = []

    def update_config(self, config: StudyConfig) -> None:
        self._config = config

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.inputs.append((text, dict(context or {}), mode))
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=f"explained[{mode}]: {text}",
            created_at="2026-05-11T00:00:00Z",
        )

    async def answer_evaluate(
        self,
        *,
        question: str = "",
        answer: str = "",
        expected_answer: str = "",
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.evaluations.append(
            (question, answer, expected_answer, dict(context or {}), mode)
        )
        return TutorReply(
            operation="answer_evaluate",
            input_text=answer,
            reply="evaluated",
            payload={
                "verdict": "partial",
                "score": 50,
                "error_type": "unknown",
                "feedback": "evaluated",
                "next_action": "review",
            },
            created_at="2026-05-11T00:00:00Z",
        )

    async def shutdown(self) -> None:
        return None

    async def summarize_session(
        self,
        history: list[dict[str, object]],
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.summaries.append((list(history), dict(context or {}), mode))
        return TutorReply(
            operation="summarize_session",
            input_text="session",
            reply="Study session summary",
            payload={
                "summary": "Study session summary",
                "key_insight": "Derivative rules improved.",
                "questions_attempted": 2,
                "correct_rate": 0.5,
            },
            created_at="2026-05-11T00:00:00Z",
        )


def test_study_store_round_trip_and_export(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    config = StudyConfig(language="en", history_limit=2)
    state = build_initial_state(mode=config.mode)
    state.last_ocr_text = "photosynthesis"

    store.save_config(config)
    store.save_state(state)
    store.append_interaction(
        kind="concept_explain", input_text="a", output_text="b", history_limit=2
    )
    store.append_interaction(
        kind="concept_explain", input_text="c", output_text="d", history_limit=2
    )
    store.append_interaction(
        kind="concept_explain", input_text="e", output_text="f", history_limit=2
    )
    store.ensure_topic(topic_id="photosynthesis_topic", name="Photosynthesis")
    store.ensure_session(session_id="session-1", mode="interactive")
    store.add_qa_record(
        session_id="session-1",
        topic_id="photosynthesis_topic",
        question={"question": "What is photosynthesis?"},
        user_answer="Plants make sugar.",
        eval_result={"verdict": "correct"},
        mode="interactive",
    )
    store.append_review_log(
        topic_id="photosynthesis_topic",
        card_id=None,
        rating=3,
        scheduled_days=1,
        actual_days=0,
    )
    candidate = store.upsert_candidate_item(
        item_type="topic",
        payload={"topic_id": "photosynthesis_topic", "name": "Photosynthesis"},
        source="test",
        dedupe_key="topic:photosynthesis",
    )
    store.add_knowledge_evidence(
        item_id=candidate["id"],
        event_type="mentioned",
        weight=0.2,
        context={"source": "unit"},
    )
    for index in range(2):
        store.add_qa_record(
            session_id="session-1",
            topic_id="photosynthesis_topic",
            question={"question": f"Recent QA {index}"},
            user_answer="answer",
            eval_result={"verdict": "correct"},
            mode="interactive",
        )
        store.append_review_log(
            topic_id="photosynthesis_topic",
            card_id=None,
            rating=index + 1,
            scheduled_days=index + 1,
            actual_days=0,
        )
        store.add_knowledge_evidence(
            item_id=candidate["id"],
            event_type="mentioned",
            weight=0.3 + index,
            context={"index": index},
        )

    assert store.load_config(StudyConfig()).language == "en"
    assert store.load_state(build_initial_state()).last_ocr_text == "photosynthesis"
    assert [item["input_text"] for item in store.list_interactions(limit=10)] == [
        "e",
        "c",
    ]
    exported = store.export_json()
    assert exported["config"]["language"] == "en"
    assert exported["sessions"][0]["id"] == "session-1"
    assert [
        item["question"]["question"] for item in store.list_qa_records(limit=2)
    ] == ["Recent QA 0", "Recent QA 1"]
    assert [item["rating"] for item in store.list_review_log(limit=2)] == [1, 2]
    assert [
        item["context"].get("index") for item in store.list_knowledge_evidence(limit=2)
    ] == [0, 1]
    assert exported["qa_records"][-1]["question"]["question"] == "Recent QA 1"
    assert exported["review_log"][0]["topic_id"] == "photosynthesis_topic"
    assert exported["knowledge_evidence"][0]["item_id"] == candidate["id"]
    store.close()


def test_study_store_seed_topic_upsert_preserves_seed_metadata(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.upsert_topic(
            {
                "id": "seed_topic",
                "name": "Seed Topic",
                "subject": "math",
                "chapter": "Seed Chapter",
                "depth": 2,
                "difficulty": 0.7,
                "prerequisites": [{"id": "pre_seed", "required_mastery": 0.5}],
                "related": [{"id": "related_seed", "relation": "next"}],
                "typical_misconceptions": ["seed misconception"],
                "source": "seed",
            }
        )
        store.upsert_topic(
            {
                "id": "seed_topic",
                "name": "Runtime Topic",
                "subject": "science",
                "chapter": "Runtime Chapter",
                "depth": 5,
                "difficulty": 0.2,
                "prerequisites": [{"id": "pre_runtime", "required_mastery": 0.9}],
                "related": [{"id": "related_runtime", "relation": "runtime"}],
                "typical_misconceptions": ["runtime misconception"],
                "source": "runtime",
            }
        )

        topic = store.get_topic("seed_topic")
        assert topic is not None
        assert topic["name"] == "Seed Topic"
        assert topic["subject"] == "math"
        assert topic["chapter"] == "Seed Chapter"
        assert topic["depth"] == 2
        assert topic["difficulty"] == 0.7
        assert topic["prerequisites"] == [{"id": "pre_seed", "required_mastery": 0.5}]
        assert topic["related"] == [{"id": "related_seed", "relation": "next"}]
        assert topic["typical_misconceptions"] == ["seed misconception"]
        assert topic["source"] == "seed"
    finally:
        store.close()


def test_study_store_enforces_sqlite_foreign_keys(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        row = store._require_conn().execute("PRAGMA foreign_keys").fetchone()
        assert row is not None
        assert int(row[0]) == 1
    finally:
        store.close()


def test_status_summary_tracked_topic_count_is_not_limited(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        for index in range(12):
            topic_id = f"topic_{index}"
            store.ensure_topic(topic_id=topic_id, name=f"Topic {index}")
            mastery = 1.0 if index < 8 else 0.0
            store.append_mastery_snapshot(
                {
                    "topic_id": topic_id,
                    "mastery": mastery,
                    "accuracy": 0.8,
                    "recency": 0.7,
                    "consistency": 0.6,
                    "confidence": 0.9,
                    "level": "mastered",
                    "attempts": 3,
                    "flags": [],
                }
            )

        summary = KnowledgeTracker(store).get_status_summary(limit=8)

        assert len(store.list_mastery_overview(limit=8)) == 8
        assert summary["tracked_topic_count"] == 12
        assert summary["average_mastery"] == 0.6667
        assert summary["weak_topic_count"] == 4
        assert len(KnowledgeTracker(store).get_weak_topics(limit=2)) == 2
    finally:
        store.close()


def test_knowledge_map_related_object_edges_use_topic_ids() -> None:
    payload = build_knowledge_map_payload(
        topics=[
            {
                "id": "quadratic_vertex_form",
                "name": "Quadratic vertex form",
                "related": [{"id": "linear_function_kb", "relation": "compare"}],
            },
            {"id": "linear_function_kb", "name": "Linear function"},
        ]
    )

    assert {
        "from": "quadratic_vertex_form",
        "to": "linear_function_kb",
        "relation": "compare",
    } in payload["edges"]
    assert not any(str(edge["to"]).startswith("{") for edge in payload["edges"])


def test_build_tutor_payload_preserves_structured_summary() -> None:
    reply = TutorReply(
        operation="summarize_session",
        input_text="session",
        reply="## Summary\n\nThe learner reviewed photosynthesis.",
        payload={
            "summary": "The learner reviewed photosynthesis.",
            "markdown": "## Summary\n\nThe learner reviewed photosynthesis.",
            "highlights": ["Generated one question"],
        },
        created_at="2026-05-11T00:00:00Z",
    )

    payload = study_service.build_tutor_payload(reply)

    assert payload["summary"] == "The learner reviewed photosynthesis."
    assert payload["markdown"] == "## Summary\n\nThe learner reviewed photosynthesis."
    assert payload["reply"] == "## Summary\n\nThe learner reviewed photosynthesis."


def test_study_mode_manager_intent_switch_rules() -> None:
    assert normalize_mode("concept_explain") == MODE_COMPANION
    assert "already in" in build_transition_phrase(
        MODE_COMPANION, language="en-GB", outcome="same"
    )
    assert "already in" in build_transition_phrase(
        MODE_COMPANION, language="eng", outcome="same"
    )
    pure = handle_user_intent("教我")
    assert pure["mode"] == MODE_TEACHING
    assert pure["pure_switch"] is True

    short_with_text = handle_user_intent("教我微分")
    assert short_with_text["mode"] == MODE_TEACHING
    assert short_with_text["pure_switch"] is False
    assert short_with_text["remaining_text"] == "微分"

    explained = handle_user_intent("解释光合作用")
    assert explained["kind"] == "concept_explain"
    assert explained["mode"] == "concept_explain"
    assert explained["remaining_text"] == "光合作用"

    with_text = handle_user_intent("教我光合作用")
    assert with_text["mode"] == MODE_TEACHING
    assert with_text["pure_switch"] is False
    assert with_text["remaining_text"] == "光合作用"

    english = handle_user_intent(r"\teaching mode photosynthesis", language="en")
    assert english["mode"] == MODE_TEACHING
    assert english["keyword"] == "teaching mode"
    assert english["remaining_text"] == "photosynthesis"

    cross_mode = handle_user_intent("教我互动模式 光合作用")
    assert cross_mode["mode"] == MODE_INTERACTIVE
    assert cross_mode["keyword"] == "互动模式"
    assert mode_label(MODE_TEACHING, language="ja") == "指導"
    assert mode_label(MODE_TEACHING, language="pt-BR") == "Ensino"
    assert "教学" not in build_transition_phrase(
        MODE_TEACHING, language="ja", outcome="changed"
    )

    manager = ModeManager(current_mode=MODE_COMPANION)
    first = manager.switch_to(MODE_INTERACTIVE, "unit", now=1000.0)
    assert first["changed"] is True
    same = manager.switch_to(MODE_INTERACTIVE, "unit", now=1010.0)
    assert same["changed"] is False
    assert same["new_mode"] == MODE_INTERACTIVE
    dwell = manager.switch_to(MODE_TEACHING, "unit", now=1010.0)
    assert dwell["changed"] is False
    assert dwell["lock_reason"] == "minimum_dwell"
    rate_limited = manager.switch_to(MODE_COMPANION, "unit", now=1020.0)
    assert rate_limited["changed"] is False
    assert rate_limited["lock_reason"] == "mode_lock"
    assert rate_limited["new_mode"] == MODE_INTERACTIVE
    assert rate_limited["lock_until"] > 1020.0

    manager = ModeManager(current_mode=MODE_COMPANION)
    assert manager.switch_to(MODE_INTERACTIVE, "unit", now=1000.0)["changed"] is True
    manager.mode_started_at = 0.0
    assert manager.switch_to(MODE_TEACHING, "unit", now=1010.0)["changed"] is True
    manager.mode_started_at = 0.0
    locked = manager.switch_to(MODE_COMPANION, "unit", now=1020.0)
    assert locked["changed"] is True
    assert locked["lock_until"] > 1020.0
    blocked = manager.switch_to(MODE_INTERACTIVE, "unit", now=1030.0)
    assert blocked["changed"] is False
    assert blocked["lock_reason"] == "mode_lock"


def test_study_config_and_state_legacy_mode_migration(tmp_path: Path) -> None:
    legacy = build_config({"study": {"default_mode": "concept_explain"}})
    assert legacy.mode == MODE_COMPANION
    assert legacy.default_mode == MODE_COMPANION

    llm_timeout = build_config({"llm": {"call_timeout_seconds": 42}})
    assert llm_timeout.llm_call_timeout_seconds == 42
    assert llm_timeout.llm_vision_enabled is False
    assert llm_timeout.llm_vision_max_image_px == 768
    llm_vision = build_config(
        {"llm": {"llm_vision_enabled": True, "llm_vision_max_image_px": 128}}
    )
    assert llm_vision.llm_vision_enabled is True
    assert llm_vision.llm_vision_max_image_px == 128
    flat_llm_vision = build_config(
        {"llm_vision_enabled": True, "llm_vision_max_image_px": 256}
    )
    assert flat_llm_vision.llm_vision_enabled is True
    assert flat_llm_vision.llm_vision_max_image_px == 256
    direct_vision = StudyConfig(llm_vision_max_image_px=99999)
    assert direct_vision.llm_vision_max_image_px == 4096
    fsrs_config = build_config(
        {"fsrs": {"retention_target": 0.88, "auto_optimize_interval_days": 14}}
    )
    assert fsrs_config.fsrs_retention_target == 0.88
    assert fsrs_config.fsrs_auto_optimize_interval_days == 14
    llm_section_legacy_timeout = build_config({"llm": {"llm_call_timeout_seconds": 84}})
    assert llm_section_legacy_timeout.llm_call_timeout_seconds == 84

    interactive = build_config({"study": {"default_mode": MODE_INTERACTIVE}})
    assert interactive.mode == MODE_INTERACTIVE
    assert interactive.default_mode == MODE_INTERACTIVE

    invalid = build_config({"study": {"default_mode": "not_a_mode"}})
    assert invalid.mode == MODE_COMPANION
    assert invalid.default_mode == MODE_COMPANION

    direct = StudyConfig(mode="not_a_mode", default_mode="invalid", history_limit=0)
    assert direct.mode == MODE_COMPANION
    assert direct.default_mode == MODE_COMPANION
    assert direct.history_limit == 1

    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.set_raw(
            "state",
            {
                "status": "ready",
                "active_mode": "concept_explain",
                "last_ocr_text": "legacy",
            },
        )
        loaded = store.load_state(build_initial_state())
        assert loaded.active_mode == MODE_COMPANION
        assert loaded.last_ocr_text == "legacy"
        assert loaded.recent_mode_switches == []
        assert loaded.suggestion_cooldowns == {}
        assert loaded.session_suggestions == []
    finally:
        store.close()


def test_trusted_knowledge_candidate_is_deprecated_after_conflict(
    tmp_path: Path,
) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    quality = KnowledgeQualityStore(store, trusted_negative_threshold=1)

    try:
        payload = {
            "subject": "math",
            "topic_id": "linear_equation",
            "name": "linear equation",
        }
        candidate = store.upsert_candidate_item(
            item_type=KnowledgeCandidateType.TOPIC.value,
            payload=payload,
            source="unit-test",
            dedupe_key="math:linear equation",
            status=KnowledgeCandidateStatus.TRUSTED.value,
        )

        assert candidate["status"] == KnowledgeCandidateStatus.TRUSTED.value
        assert quality.prompt_evidence_summary(topic_id="linear_equation")

        quality.add_evidence(
            candidate["id"],
            KnowledgeEvidenceType.CONFLICT_DETECTED.value,
            -1.0,
            {"source": "unit-test"},
        )

        updated = store.get_candidate_item(candidate["id"])
        assert updated is not None
        assert updated["status"] == KnowledgeCandidateStatus.DEPRECATED.value
        assert quality.prompt_evidence_summary(topic_id="linear_equation") == []
    finally:
        store.close()


def test_study_screen_classifier_routes_summary_keywords_to_summary() -> None:
    english = classify_screen_from_ocr(
        "## Summary\n\nThe learner reviewed photosynthesis."
    )
    assert english.screen_type == "summary"
    assert "summary" in english.signals.get("summary_hits", [])

    chinese = classify_screen_from_ocr(
        "本节总结\n光合作用的主要过程包括光反应和暗反应。"
    )
    assert chinese.screen_type == "summary"
    assert "总结" in chinese.signals.get("summary_hits", [])


def test_study_screen_classifier_covers_core_screen_types_and_smoothing() -> None:
    assert (
        classify_screen_from_ocr("", window_title="").to_payload()["screen_type"]
        == "idle"
    )

    question = classify_screen_from_ocr(
        "Problem 1: What is the derivative?", window_title="Quiz exercise"
    )
    assert question.screen_type == "question"
    assert question.confidence > 0.0

    answering = classify_screen_from_ocr(
        "Answer submitted\nScore: incorrect\nFeedback: retry",
        window_title="Answer review",
    )
    assert answering.screen_type in {"answering", "review"}
    assert answering.signals["answer_hits"]

    notes = classify_screen_from_ocr(
        "Notes\nmemo outline for photosynthesis", window_title="Study note"
    )
    assert notes.screen_type == "notes"

    reading = classify_screen_from_ocr(
        "Chapter section lesson text definition concept " * 8,
        window_title="Reading lesson",
    )
    assert reading.screen_type == "reading"
    assert len(reading.text_excerpt) <= 143

    recent = [
        ScreenClassification(screen_type="review", confidence=0.7),
        {"screen_type": "review", "screen_confidence": 0.72},
        {"screen_type": "review", "confidence": 0.74},
    ]
    smoothed = classify_screen_from_ocr("tiny", recent_classifications=recent)
    assert smoothed.screen_type == "review"
    assert smoothed.signals["smoothed_from"] == "review"


def test_compact_prompt_value_stops_at_max_depth() -> None:
    nested = {"a": {"b": {"c": {"d": "bottom"}}}}

    compacted = _compact_prompt_value(
        nested, list_limit=5, string_limit=50, max_depth=2
    )

    assert compacted == {"a": {"b": "...[max depth reached]"}}


def test_study_prompt_builder_compacts_large_context_and_rejects_unknown_operation() -> (
    None
):
    context = {
        "text": "x" * 20000,
        "language": "en",
        "items": [{"body": "y" * 2000} for _ in range(30)],
        **{f"k{i}": i for i in range(90)},
    }

    messages = build_operation_messages("question_generate", context)

    assert messages[1]["content"].count("_prompt_truncated") == 1
    assert len(messages[1]["content"]) <= 9500
    with pytest.raises(ValueError):
        build_operation_messages("unsupported", {})


def test_study_open_ui_payload_returns_message_key() -> None:
    payload = build_open_ui_payload(plugin_id="study_companion", available=True)
    assert payload["available"] is True
    assert payload["path"] == "/plugin/study_companion/ui/"
    assert payload["message_key"] == "ui.open.available"
    assert "message" not in payload


def test_study_knowledge_map_payload_uses_topic_ids_for_object_edges() -> None:
    payload = build_knowledge_map_payload(
        topics=[
            {
                "id": "number_axis",
                "name": "Number Axis",
                "prerequisites": [
                    {"id": "real_number_concept", "required_mastery": 0.55}
                ],
                "related": [{"id": "absolute_value", "relation": "next"}],
            },
            {"id": "real_number_concept", "name": "Real Numbers"},
            {"id": "absolute_value", "name": "Absolute Value"},
        ]
    )

    assert {
        "from": "real_number_concept",
        "to": "number_axis",
        "relation": "prerequisite",
        "required_mastery": 0.55,
    } in payload["edges"]
    assert {
        "from": "number_axis",
        "to": "absolute_value",
        "relation": "next",
    } in payload["edges"]
    assert all(not edge["from"].startswith("{") for edge in payload["edges"])


def test_study_knowledge_map_weak_topic_count_matches_visible_nodes() -> None:
    payload = build_knowledge_map_payload(
        topics=[
            {"id": "visible_weak", "name": "Visible weak topic"},
            {"id": "visible_strong", "name": "Visible strong topic"},
        ],
        weak_topics=[
            {"topic_id": "visible_weak", "name": "Visible weak topic"},
            {"topic_id": "hidden_weak", "name": "Hidden weak topic"},
        ],
    )

    assert payload["summary"]["weak_topic_count"] == 1
    assert [node["id"] for node in payload["nodes"] if node["weak"]] == ["visible_weak"]
    assert len(payload["weak_topics"]) == 2


def test_study_companion_i18n_bundles_are_present() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    locales = ["zh-CN", "en", "ja", "ko", "ru", "zh-TW", "es", "pt"]
    phase3_keys = [
        "ui.label.screen",
        "ui.label.question",
        "ui.label.answer",
        "ui.label.classification",
        "ui.label.history",
        "ui.button.generate_question",
        "ui.button.evaluate_answer",
        "ui.button.summarize_session",
        "ui.status.generating_question",
        "ui.status.evaluating_answer",
        "ui.status.summarizing_session",
        "ui.status.screen.idle",
        "ui.status.screen.reading",
        "ui.status.screen.question",
        "ui.status.screen.answering",
        "ui.status.screen.review",
        "ui.status.screen.notes",
        "ui.status.screen.summary",
        "ui.error.missing_question",
        "ui.error.missing_answer",
        "ui.surface.knowledge_map",
        "ui.surface.knowledge_contribution_settings",
        "ui.surface.note_exporter",
        "ui.surface.quickstart",
        "ui.button.export",
    ]
    bundles: dict[str, dict[str, str]] = {}
    for locale in locales:
        bundle_path = plugin_dir / "i18n" / f"{locale}.json"
        assert bundle_path.is_file()
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundles[locale] = bundle
        assert "plugin.name" in bundle
        assert "ui.title" in bundle
        assert "ui.surface.study_panel" in bundle
        assert "ui.button.explain" in bundle
        assert "status.mode.companion" in bundle
        assert "status.mode.interactive" in bundle
        assert "status.mode.teaching" in bundle
        assert "ui.status.mode_switching" in bundle
        assert "ui.error.mode_switch_failed" in bundle
        assert "entries.knowledge_map.name" in bundle
        assert "entries.set_knowledge_contribution_opt_in.name" in bundle
        assert "entries.export_notes.name" in bundle

    en_bundle = json.loads(
        (plugin_dir / "i18n" / "en.json").read_text(encoding="utf-8")
    )
    for locale in locales:
        assert set(bundles[locale]) == set(en_bundle)
    for locale in [item for item in locales if item != "en"]:
        bundle = bundles[locale]
        assert any(bundle[key] != en_bundle[key] for key in phase3_keys)
    assert bundles["ja"]["entries.open_ui.name"] != en_bundle["entries.open_ui.name"]
    assert (
        bundles["ja"]["entries.download_rapidocr_models.description"]
        != en_bundle["entries.download_rapidocr_models.description"]
    )

    with (plugin_dir / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)
    plugin_ui = normalize_plugin_ui_manifest(config, plugin_id="study_companion")
    assert plugin_ui is not None
    meta = {
        "id": "study_companion",
        "config_path": str(plugin_dir / "plugin.toml"),
        "plugin_ui": plugin_ui,
        "i18n": config["plugin"]["i18n"],
    }
    surfaces, warnings = _build_surfaces_sync("study_companion", meta)
    assert warnings == []
    assert any(
        surface["id"] == "study-panel" and surface["available"] is True
        for surface in surfaces
    )
    assert any(
        surface["id"] == "knowledge-map" and surface["available"] is True
        for surface in surfaces
    )
    assert any(
        surface["id"] == "knowledge-contribution-settings"
        and surface["available"] is True
        for surface in surfaces
    )
    assert any(
        surface["id"] == "note-exporter" and surface["available"] is True
        for surface in surfaces
    )
    assert any(
        surface["id"] == "quickstart" and surface["available"] is True
        for surface in surfaces
    )

    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    assert "./i18n.js" in index_html
    assert 'data-i18n="ui.title"' in index_html
    assert "I18n.init" in main_js


def test_study_companion_static_ui_smoke_with_mocked_runs() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if shutil.which("node") is None:
        pytest.skip(
            "node is not installed; frontend/plugin-manager happy-dom smoke test requires node"
        )
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip(
            "frontend/plugin-manager node_modules with happy-dom is not installed"
        )

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const mainJs = fs.readFileSync(path.join(staticDir, 'main.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(i18nDir, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();

const runEntries = new Map();
let activeMode = 'companion';
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = body.entry_id === 'study_explain_text'
      ? 'run-explain'
      : body.entry_id === 'study_set_mode'
        ? 'run-mode'
        : 'run-status';
    runEntries.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (url === '/runs/run-status') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-mode') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-explain') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-status/export') {
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { status: 'ready', active_mode: activeMode } } }],
    });
  }
  if (url === '/runs/run-mode/export') {
    const run = runEntries.get('run-mode') || {};
    activeMode = run.args.mode || activeMode;
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data: {
            changed: true,
            old_mode: 'companion',
            new_mode: activeMode,
            transition_phrase: `${activeMode} mode enabled`,
            reply: `${activeMode} mode enabled`,
          },
        },
      }],
    });
  }
  if (url === '/runs/run-explain/export') {
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { reply: 'A derivative is slope at one point.', degraded: false } } }],
    });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(mainJs);

async function waitFor(predicate, label) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

  await waitFor(() => document.getElementById('statusLine').textContent.includes('Ready'), 'ready status');
if (document.title !== 'Study Companion') {
  throw new Error(`unexpected title: ${document.title}`);
}

document.getElementById('modeInteractiveBtn').click();
await waitFor(() => document.getElementById('statusLine').textContent.includes('Interactive'), 'interactive mode');
if (!runEntries.get('run-mode') || runEntries.get('run-mode').args.mode !== 'interactive') {
  throw new Error(`mode run args mismatch: ${JSON.stringify(runEntries.get('run-mode'))}`);
}

document.getElementById('studyInput').value = 'Explain derivative';
document.getElementById('explainBtn').click();
await waitFor(() => document.getElementById('replyText').textContent === 'A derivative is slope at one point.', 'explain reply');

const explainRun = runEntries.get('run-explain');
if (!explainRun || explainRun.args.text !== 'Explain derivative') {
  throw new Error(`explain run args mismatch: ${JSON.stringify(explainRun)}`);
}
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_STATIC_DIR": str(plugin_dir / "static"),
        "STUDY_COMPANION_I18N_DIR": str(plugin_dir / "i18n"),
    }
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_companion_hosted_panel_uses_long_running_entry_poll_budget() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    assert "ENTRY_TIMEOUT_MS" in source
    assert "study_set_mode: 15000" in source
    assert "study_explain_text: 60000" in source
    assert "const deadline = Date.now() + timeoutForEntry(entryId);" in source
    assert "for (let i = 0; i < 40; i += 1)" not in source
    assert (
        "async function refresh(signal?: AbortSignal, options: { updateReply?: boolean } = {})"
        in source
    )
    assert "await refresh(controller.signal, { updateReply: false });" in source
    assert "const appliedMode = String(" in source
    assert "setStatus((prev) => ({" in source
    assert "active_mode: appliedMode," in source
    assert "mode: appliedMode," in source
    assert "study-panel__modes" in source
    assert "study_set_mode" in source
    assert "status.mode.companion" in source


def test_study_companion_note_exporter_uses_backend_export_poll_budget() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "note_exporter.tsx").read_text(encoding="utf-8")

    assert "DEFAULT_EXPORT_TIMEOUT_MS = 80_000" in source
    assert "POLL_TIMEOUT_BUFFER_MS = 5_000" in source
    assert "const timeoutSeconds = Number(entry?.timeout);" in source
    assert "return timeoutSeconds * 1000 + POLL_TIMEOUT_BUFFER_MS;" in source
    assert (
        "const deadline = Date.now() + Math.max(timeoutMs, POLL_INTERVAL_MS);" in source
    )
    assert "while (Date.now() < deadline)" in source
    assert "pollTimeoutMs = getEntryTimeoutMs(exportEntry)" in source
    assert "}, pollTimeoutMs);" in source
    assert "for (let attempt = 0; attempt < 40; attempt += 1)" not in source
    assert "for (let i = 0; i < 40; i += 1)" not in source


def test_study_companion_ui_export_failures_are_not_silent_successes() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted_source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    assert "RUN_EXPORT_RETRY_COUNT = 3" in hosted_source
    assert "throw new Error(`Run export failed: HTTP ${lastStatus}`);" in hosted_source
    assert (
        "const exported = exportResp.ok ? await exportResp.json() : {};"
        not in hosted_source
    )
    assert "return item?.json?.data || {};" not in hosted_source
    assert "study_set_mode" in hosted_source

    assert "RUN_EXPORT_RETRY_COUNT = 3" in static_source
    assert "throw new Error(tf('ui.error.run_export_failed'" in static_source
    assert "if (!response.ok) {\n    return {};" not in static_source
    assert "callPlugin('study_set_mode'" in static_source
    assert "Array.isArray(deck.due_reviews)" in static_source
    assert "Number(deck.item_count)" in static_source
    assert "currentMemoryCard.front || currentMemoryCard.item?.prompt" in static_source


def test_study_companion_static_mode_switch_uses_applied_mode() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    set_mode_start = static_source.index("async function setMode(mode)")
    set_mode_end = static_source.index("function bindButton", set_mode_start)
    set_mode = static_source[set_mode_start:set_mode_end]

    assert "async function setMode(mode)" in set_mode
    assert "if (mode === currentMode)" in set_mode
    assert "callPlugin('study_set_mode'" in set_mode
    assert "data.new_mode" in set_mode
    assert "data && data.changed === false" in set_mode
    assert "setModeButtons(currentMode, false)" in set_mode
    assert "answerInput.value = data.answer;" in static_source
    assert "answerInput.value = '';" not in static_source


def test_study_companion_static_panel_keeps_mode_highlight_when_status_refresh_fails() -> (
    None
):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip(
            "frontend/plugin-manager node_modules with happy-dom is not installed"
        )

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const html = `<!doctype html><html><head><title>Study Companion</title></head><body>
  <div id="statusLine"></div>
  <div id="replyText"></div>
  <textarea id="studyInput"></textarea>
  <button id="refreshBtn"></button>
  <button id="ocrBtn"></button>
  <button id="explainBtn"></button>
  <button id="modeCompanionBtn" data-mode="companion"></button>
  <button id="modeInteractiveBtn" data-mode="interactive"></button>
  <button id="modeTeachingBtn" data-mode="teaching"></button>
</body></html>`;

const i18nJs = fs.readFileSync(process.env.STUDY_COMPANION_I18N_JS, 'utf8');
const mainJs = fs.readFileSync(process.env.STUDY_COMPANION_STATIC_JS, 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(process.env.STUDY_COMPANION_I18N_DIR, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();

const runEntries = new Map();
let activeMode = 'companion';
let failStatusExport = false;
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = body.entry_id === 'study_set_mode'
      ? 'run-mode'
      : 'run-status';
    runEntries.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (url === '/runs/run-status') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-mode') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-status/export') {
    if (failStatusExport) {
      return new Response('boom', { status: 500 });
    }
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { status: 'ready', active_mode: activeMode } } }],
    });
  }
  if (url === '/runs/run-mode/export') {
    const run = runEntries.get('run-mode') || {};
    activeMode = run.args.mode || activeMode;
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data: {
            changed: true,
            old_mode: 'companion',
            new_mode: activeMode,
            transition_phrase: `${activeMode} mode enabled`,
            reply: `${activeMode} mode enabled`,
          },
        },
      }],
    });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(mainJs);

async function waitFor(predicate, label) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

await waitFor(() => document.getElementById('statusLine').textContent.includes('Ready'), 'ready status');

failStatusExport = true;
document.getElementById('modeTeachingBtn').click();
await waitFor(() => document.getElementById('statusLine').textContent.includes('Error'), 'status error');

const teachingButton = document.querySelector('[data-mode="teaching"]');
if (!teachingButton || teachingButton.getAttribute('aria-pressed') !== 'true') {
  throw new Error(`teaching mode not highlighted: ${teachingButton && teachingButton.outerHTML}`);
}
if (document.querySelector('[data-mode="interactive"]').getAttribute('aria-pressed') !== 'false') {
  throw new Error('interactive mode still highlighted after failed refresh');
}
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_STATIC_JS": str(plugin_dir / "static" / "main.js"),
        "STUDY_COMPANION_I18N_JS": str(plugin_dir / "static" / "i18n.js"),
        "STUDY_COMPANION_I18N_DIR": str(plugin_dir / "i18n"),
    }
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_surface_utils_preserves_nonstandard_backend_error_details() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "study_surface_utils.ts").read_text(
        encoding="utf-8"
    )

    assert "function pluginErrorMessage" in source
    assert "typeof error === 'string'" in source
    assert "JSON.stringify(error)" in source
    assert "throw new Error(pluginErrorMessage(item.json.error))" in source


def test_study_companion_i18n_prefers_traditional_chinese_bundle() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.env.STUDY_COMPANION_I18N_JS, 'utf8');

globalThis.window = globalThis;
globalThis.document = { documentElement: { lang: '' } };
globalThis.location = { search: '?locale=zh-TW', pathname: '/plugin/study_companion/ui/' };
Object.defineProperty(globalThis, 'navigator', {
  value: { languages: ['zh-TW', 'zh-CN'], language: 'zh-TW' },
  configurable: true,
});
globalThis.console = console;

let bundleRequests = [];
globalThis.fetch = async (url) => {
  const href = String(url);
  if (href.includes('/ui-api/i18n/')) {
    bundleRequests.push(href);
  }
  if (href.endsWith('/zh-TW.json')) {
    return { ok: true, json: async () => ({ 'ui.title': '繁體中文' }) };
  }
  if (href.endsWith('/zh-CN.json')) {
    return { ok: true, json: async () => ({ 'ui.title': '简体中文' }) };
  }
  return { ok: false, json: async () => ({}) };
};

eval(source);

(async () => {
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected lang: ${window.I18n.lang()}`);
  }
  if (document.documentElement.lang !== 'zh-TW') {
    throw new Error(`unexpected document lang: ${document.documentElement.lang}`);
  }
  if (window.I18n.t('ui.title', 'fallback') !== '繁體中文') {
    throw new Error(`unexpected bundle text: ${window.I18n.t('ui.title', 'fallback')}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected query locale request order: ${JSON.stringify(bundleRequests)}`);
  }

  bundleRequests = [];
  window.I18n._bundle = {};
  window.I18n.setLang('zh-CN');
  location.search = '';
  navigator.languages = ['zh-TW', 'zh-CN'];
  navigator.language = 'zh-TW';
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected browser lang: ${window.I18n.lang()}`);
  }
  if (window.I18n.t('ui.title', 'fallback') !== '繁體中文') {
    throw new Error(`unexpected browser bundle text: ${window.I18n.t('ui.title', 'fallback')}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected browser locale request order: ${JSON.stringify(bundleRequests)}`);
  }

  bundleRequests = [];
  window.I18n._bundle = {};
  window.I18n.setLang('zh-CN');
  location.search = '?locale=zh-Hant-HK';
  navigator.languages = ['zh-Hant-HK', 'zh-CN'];
  navigator.language = 'zh-Hant-HK';
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected hant lang: ${window.I18n.lang()}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected hant locale request order: ${JSON.stringify(bundleRequests)}`);
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_I18N_JS": str(plugin_dir / "static" / "i18n.js"),
    }
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=plugin_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_ocr_pipeline_uses_local_capture_profile() -> None:
    capture = _FakeCaptureBackend(image=object())
    ocr = _FakeOcrBackend("captured text")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            ocr_left_inset_ratio=0.11,
            ocr_right_inset_ratio=0.12,
            ocr_top_ratio=0.13,
            ocr_bottom_inset_ratio=0.14,
        ),
        ocr_backend=ocr,
        capture_backend=capture,
    )

    snapshot = pipeline.capture_snapshot(target=object())

    assert snapshot.status == "ok"
    assert snapshot.text == "captured text"
    assert len(capture.calls) == 1
    profile = capture.calls[0][1]
    assert isinstance(profile, StudyCaptureProfile)
    assert profile.left_inset_ratio == 0.11
    assert profile.right_inset_ratio == 0.12
    assert profile.top_ratio == 0.13
    assert profile.bottom_inset_ratio == 0.14


def test_study_companion_does_not_import_galgame_namespace_directly() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    for path in plugin_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "plugin.plugins.galgame_plugin" not in source


def test_printwindow_capture_uses_hwnd_capture_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    calls: list[tuple[int, tuple[int, int, int, int]]] = []

    def _capture_full_window(hwnd: int, rect: tuple[int, int, int, int]):
        calls.append((hwnd, rect))
        return Image.new("RGB", (rect[2] - rect[0], rect[3] - rect[1]))

    monkeypatch.setattr(
        PrintWindowCaptureBackend,
        "_capture_full_window",
        staticmethod(_capture_full_window),
    )
    target = SimpleNamespace(
        hwnd=1234,
        left=10,
        top=20,
        width=100,
        height=80,
        is_minimized=False,
        eligible=True,
    )

    image = PrintWindowCaptureBackend().capture_frame(
        target,
        StudyCaptureProfile(left_inset_ratio=0.0, right_inset_ratio=0.0),
    )

    assert image.size == (100, 80)
    assert calls == [(1234, (10, 20, 110, 100))]


def test_printwindow_capture_requires_hwnd() -> None:
    target = SimpleNamespace(
        hwnd=0,
        left=10,
        top=20,
        width=100,
        height=80,
        is_minimized=False,
        eligible=True,
    )

    with pytest.raises(RuntimeError, match="target hwnd"):
        PrintWindowCaptureBackend().capture_frame(target, StudyCaptureProfile())


def test_printwindow_capture_releases_window_dc_without_deleting_wrapped_hdc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    previous_bitmap = object()

    class _Bitmap:
        def CreateCompatibleBitmap(self, _source_dc, width: int, height: int) -> None:
            calls.append(f"create_bitmap:{width}x{height}")

        def GetInfo(self) -> dict[str, int]:
            return {"bmWidth": 2, "bmHeight": 2}

        def GetBitmapBits(self, _as_bytes: bool) -> bytes:
            return b"\x00" * 16

        def GetHandle(self) -> int:
            return 123

    bitmap = _Bitmap()

    class _MemDc:
        def SelectObject(self, obj):
            calls.append(
                "restore_bitmap" if obj is previous_bitmap else "select_bitmap"
            )
            return previous_bitmap

        def GetSafeHdc(self) -> int:
            return 456

        def BitBlt(self, *_args) -> None:
            calls.append("bitblt")

        def DeleteDC(self) -> None:
            calls.append("mem_delete")

    mem_dc = _MemDc()

    class _SourceDc:
        def CreateCompatibleDC(self):
            return mem_dc

        def DeleteDC(self) -> None:
            calls.append("source_delete")

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            GetWindowDC=lambda _hwnd: 789,
            DeleteObject=lambda _handle: calls.append("delete_object"),
            ReleaseDC=lambda _hwnd, _hdc: calls.append("release_dc"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32ui",
        SimpleNamespace(
            CreateDCFromHandle=lambda _hdc: _SourceDc(),
            CreateBitmap=lambda: bitmap,
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(SRCCOPY=1))
    monkeypatch.setattr(
        study_capture_backends_module.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(PrintWindow=lambda *_args: 0)),
        raising=False,
    )

    image = PrintWindowCaptureBackend._capture_full_window(1234, (0, 0, 2, 2))

    assert image.size == (2, 2)
    assert calls == [
        "create_bitmap:2x2",
        "select_bitmap",
        "bitblt",
        "restore_bitmap",
        "mem_delete",
        "delete_object",
        "release_dc",
    ]
    assert "source_delete" not in calls


def test_win32_target_window_rect_reads_under_dpi_aware_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class _User32:
        def SetThreadDpiAwarenessContext(self, context):
            value = getattr(context, "value", context)
            calls.append(value)
            return 99

    monkeypatch.setattr(study_capture_backends_module.sys, "platform", "win32")
    monkeypatch.setattr(
        study_capture_backends_module.ctypes,
        "windll",
        SimpleNamespace(user32=_User32()),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            GetWindowRect=lambda _hwnd: (
                calls.append("get_window_rect") or (10, 20, 110, 100)
            )
        ),
    )

    rect = study_capture_backends_module._target_window_rect(SimpleNamespace(hwnd=1234))

    assert rect == (10, 20, 110, 100)
    per_monitor_v2 = study_capture_backends_module.ctypes.c_void_p(-4).value
    assert calls == [per_monitor_v2, "get_window_rect", 99]


def test_pyautogui_capture_rejects_secondary_monitor_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot_calls = 0

    def _screenshot(**_kwargs):
        nonlocal screenshot_calls
        screenshot_calls += 1

    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(size=lambda: (1920, 1080), screenshot=_screenshot),
    )
    monkeypatch.setattr(study_capture_backends_module.sys, "platform", "win32")
    target = SimpleNamespace(
        hwnd=0,
        left=2000,
        top=100,
        width=400,
        height=300,
        is_minimized=False,
        eligible=True,
    )

    with pytest.raises(RuntimeError, match="secondary_monitor|spans_across"):
        PyAutoGuiCaptureBackend().capture_frame(target, StudyCaptureProfile())
    assert screenshot_calls == 0


def test_ocr_pipeline_handles_empty_text_repeats_and_errors() -> None:
    cfg = StudyConfig()
    empty = StudyOcrPipeline(
        logger=_Logger(), config=cfg, ocr_backend=_FakeOcrBackend("")
    )
    assert empty.snapshot_from_image(object()).status == "empty"
    assert empty.snapshot_from_image(None).diagnostic == "no image supplied"

    disabled = StudyOcrPipeline(logger=_Logger(), config=StudyConfig(ocr_enabled=False))
    disabled_snapshot = disabled.capture_snapshot()
    assert disabled_snapshot.status == "disabled"

    repeated = StudyOcrPipeline(
        logger=_Logger(),
        config=cfg,
        ocr_backend=_FakeOcrBackend(["Alpha", "Alpha", "Beta"]),
    )
    snapshot = repeated.snapshot_from_image(object())
    assert snapshot.status == "ok"
    assert snapshot.text == "Alpha Alpha Beta"

    broken = StudyOcrPipeline(
        logger=_Logger(),
        config=cfg,
        ocr_backend=_FakeOcrBackend(RuntimeError("ocr boom")),
    )
    failed = broken.snapshot_from_image(object())
    assert failed.status == "ocr_failed"
    assert "ocr boom" in failed.diagnostic


def test_ocr_pipeline_normalizes_box_objects_and_capture_failures() -> None:
    class _Box:
        text = "Box text"

        def to_dict(self):
            return {"text": self.text, "x": 1}

    class _BrokenCapture:
        def capture_frame(self, _target, _profile):
            raise RuntimeError("target capture boom")

    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_FakeOcrBackend([_Box(), {"text": "Dict text", "x": 2}, "Tail"]),
    )
    snapshot = pipeline.snapshot_from_image(object(), backend_name="fake")

    assert snapshot.status == "ok"
    assert snapshot.backend == "fake"
    assert snapshot.text == "Box text Dict text Tail"
    assert snapshot.boxes == [
        {"text": "Box text", "x": 1},
        {"text": "Dict text", "x": 2},
    ]

    broken = StudyOcrPipeline(
        logger=_Logger(), config=StudyConfig(), capture_backend=_BrokenCapture()
    )
    failed = broken.capture_snapshot(target=object())
    assert failed.status == "capture_failed"
    assert "target capture boom" in failed.diagnostic


def test_ocr_pipeline_reports_fullscreen_capture_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _capture_boom():
        raise RuntimeError("capture boom")

    monkeypatch.setattr(
        StudyOcrPipeline, "_capture_fullscreen", staticmethod(_capture_boom)
    )
    pipeline = StudyOcrPipeline(logger=_Logger(), config=StudyConfig())

    snapshot = pipeline.capture_snapshot()

    assert snapshot.status == "capture_failed"
    assert "capture boom" in snapshot.diagnostic


def test_available_tesseract_languages_logs_timeout_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    detected = tmp_path / "tesseract.exe"
    target_dir = tmp_path / "install"
    tessdata = target_dir / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_text("stub", encoding="utf-8")

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=[str(detected), "--list-langs"], timeout=5.0
        )

    monkeypatch.setattr(subprocess, "run", _timeout)
    with caplog.at_level("WARNING", logger=study_service._LOGGER.name):
        languages = _available_tesseract_languages(detected, target_dir)

    assert languages == {"eng"}
    assert any("timed out" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_study_install_tesseract_uses_local_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    calls: list[dict[str, object]] = []

    async def _fake_install_tesseract(**kwargs):
        calls.append(dict(kwargs))
        progress_callback = kwargs.get("progress_callback")
        if progress_callback is not None:
            maybe_awaitable = progress_callback(
                {
                    "phase": "completed",
                    "message": "Tesseract is ready",
                    "progress": 1.0,
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "resume_from": 0,
                    "asset_name": "",
                    "release_name": "",
                }
            )
            if hasattr(maybe_awaitable, "__await__"):
                await maybe_awaitable
        return {
            "summary": "Tesseract is ready",
            "detected_path": "C:/Tesseract/tesseract.exe",
        }

    monkeypatch.setattr(
        study_tesseract_support, "install_tesseract", _fake_install_tesseract
    )
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {
                "enabled": True,
                "install_target_dir": str(tmp_path / "Tesseract-OCR"),
                "languages": "eng",
            },
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        install_result = await plugin.study_install_tesseract(
            force=True, _ctx={"run_id": "run-study-install"}
        )

        assert isinstance(install_result, Ok)
        assert install_result.value["summary"] == "Tesseract is ready"
        assert calls
        assert calls[0]["plugin_id"] == "study_companion"
        assert calls[0]["task_id"] == "run-study-install"
        assert calls[0]["force"] is True
        assert ctx.run_updates
        assert ctx.run_updates[-1]["run_id"] == "run-study-install"
    finally:
        await plugin.shutdown()


def test_study_ocr_pipeline_uses_local_tesseract_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    class _FakeTesseractBackend:
        def __init__(self, **kwargs) -> None:
            created.append(dict(kwargs))

    monkeypatch.setattr(
        study_tesseract_support, "TesseractOcrBackend", _FakeTesseractBackend
    )
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            ocr_backend_selection="tesseract",
            ocr_tesseract_path="C:/Tesseract/tesseract.exe",
            ocr_install_target_dir="C:/Tesseract",
            ocr_languages="eng",
        ),
    )

    backend = pipeline._resolve_ocr_backend()

    assert isinstance(backend, _FakeTesseractBackend)
    assert created == [
        {
            "tesseract_path": "C:/Tesseract/tesseract.exe",
            "install_target_dir_raw": "C:/Tesseract",
            "languages": "eng",
        }
    ]


def test_study_tesseract_backend_restores_global_tesseract_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tesseract.exe"
    executable.write_text("", encoding="utf-8")
    calls: list[tuple[object, str]] = []
    fake_pytesseract = SimpleNamespace()
    fake_pytesseract.pytesseract = SimpleNamespace(tesseract_cmd="original-cmd")

    def image_to_string(candidate: object, *, lang: str, config: str) -> str:
        calls.append((candidate, fake_pytesseract.pytesseract.tesseract_cmd))
        return "recognized text"

    fake_pytesseract.image_to_string = image_to_string
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(
        study_tesseract_support, "_prepare_ocr_image", lambda image: "prepared-image"
    )

    backend = study_tesseract_support.TesseractOcrBackend(
        tesseract_path=str(executable),
        languages="eng",
    )

    assert backend.extract_text("source-image") == "recognized text"
    assert calls == [
        ("source-image", str(executable)),
        ("prepared-image", str(executable)),
    ]
    assert fake_pytesseract.pytesseract.tesseract_cmd == "original-cmd"


@pytest.mark.asyncio
async def test_study_ocr_snapshot_preserves_last_text_when_capture_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        async with plugin._lock:
            plugin._state.last_ocr_text = "photosynthesis"
            plugin._state.last_ocr_at = "2026-05-10T00:00:00Z"
        plugin._ocr_pipeline = _FakeStudyOcrPipeline(
            OcrSnapshot(
                status="capture_failed",
                captured_at="2026-05-11T00:00:00Z",
                diagnostic="capture boom",
            )
        )
        plugin._agent = _FakeTutorAgent()

        snapshot_result = await plugin.study_ocr_snapshot()
        assert isinstance(snapshot_result, Ok)
        assert snapshot_result.value["status"] == "capture_failed"
        assert snapshot_result.value["text"] == ""

        async with plugin._lock:
            assert plugin._state.last_ocr_text == "photosynthesis"
            assert plugin._state.last_ocr_at == "2026-05-10T00:00:00Z"

        stored_state = plugin._store.load_state(build_initial_state())
        assert stored_state.last_ocr_text == "photosynthesis"

        explain_result = await plugin.study_explain_text()
        assert isinstance(explain_result, Ok)
        assert explain_result.value["input_text"] == "photosynthesis"
        assert plugin._agent.inputs[0][0] == "photosynthesis"
        assert plugin._agent.inputs[0][2] == MODE_COMPANION
        assert plugin._agent.inputs[0][1]["source"] == "ocr_snapshot"
        assert plugin._agent.inputs[0][1]["mode"] == MODE_COMPANION
        assert plugin._agent.inputs[0][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[0][1]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_ocr_snapshot_feeds_supervision_activity() -> None:
    class _Supervision:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def observe_activity(
            self, *, ocr_text: str, sensor_available: bool
        ) -> dict[str, object]:
            self.calls.append(
                {"ocr_text": ocr_text, "sensor_available": sensor_available}
            )
            return {
                "sensor_available": sensor_available,
                "inactivity_detected": False,
            }

    persisted: list[bool] = []

    async def _persist_state() -> None:
        persisted.append(True)

    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._ocr_pipeline = _FakeStudyOcrPipeline(
        OcrSnapshot(
            text="photosynthesis note",
            status="ok",
            captured_at="2026-05-11T00:00:00Z",
        )
    )
    plugin._supervision = supervision
    plugin._lock = asyncio.Lock()
    plugin._state = build_initial_state()
    plugin._persist_state = _persist_state

    async def _update_screen_classification(text, update_empty=False):
        return {
            "screen_type": "reading",
            "text": text,
            "update_empty": update_empty,
        }

    plugin._update_screen_classification = _update_screen_classification

    result = await plugin.study_ocr_snapshot()

    assert isinstance(result, Ok)
    assert result.value["supervision"]["sensor_available"] is True
    assert supervision.calls == [
        {"ocr_text": "photosynthesis note", "sensor_available": True}
    ]
    assert persisted == [True]


@pytest.mark.asyncio
async def test_study_explain_text_detects_mode_intent_and_continues_when_content_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        pure = await plugin.study_explain_text("教我")
        assert isinstance(pure, Ok)
        assert pure.value["new_mode"] == MODE_TEACHING
        assert pure.value["reply"]
        assert "教学模式" in pure.value["reply"]
        assert plugin._cfg.mode == MODE_TEACHING
        assert plugin._cfg.default_mode == MODE_COMPANION
        assert plugin._store.load_config(StudyConfig()).default_mode == MODE_COMPANION

        explain_only = await plugin.study_explain_text("解释光合作用")
        assert isinstance(explain_only, Ok)
        assert explain_only.value["intent"]["kind"] == "concept_explain"
        assert "mode_switch" not in explain_only.value
        assert explain_only.value["reply"] == "explained[teaching]: 光合作用"

        async with plugin._lock:
            plugin._state.last_ocr_text = "细胞呼吸"
            plugin._state.last_ocr_at = "2026-05-12T00:00:00Z"
        explain_latest_ocr = await plugin.study_explain_text("解释一下")
        assert isinstance(explain_latest_ocr, Ok)
        assert explain_latest_ocr.value["intent"]["kind"] == "concept_explain"
        assert explain_latest_ocr.value["input_text"] == "细胞呼吸"
        assert explain_latest_ocr.value["reply"] == "explained[teaching]: 细胞呼吸"
        assert plugin._agent.inputs[-1][0] == "细胞呼吸"
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "ocr_snapshot"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]

        async with plugin._lock:
            plugin._state.active_mode = MODE_COMPANION
            plugin._state.mode_started_at = 0.0
            plugin._state.mode_lock_until = 0.0
            plugin._state.recent_mode_switches = []
            plugin._cfg.mode = MODE_COMPANION
            plugin._cfg.default_mode = MODE_COMPANION
        plugin._mode_manager.restore(
            {
                "current_mode": MODE_COMPANION,
                "mode_started_at": 0.0,
                "recent_mode_switches": [],
                "suggestion_cooldowns": {},
                "session_suggestions": [],
                "mode_lock_until": 0.0,
            }
        )

        explained = await plugin.study_explain_text("教我光合作用")
        assert isinstance(explained, Ok)
        assert explained.value["intent"]["mode"] == MODE_TEACHING
        assert explained.value["mode_switch"]["changed"] is True
        assert (
            explained.value["reply"]
            == f"explained[teaching]: {explained.value['input_text']}"
        )
        assert plugin._agent.inputs[-1][0] == explained.value["input_text"]
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is True
        assert "screen_classification" in plugin._agent.inputs[-1][1]

        short_explained = await plugin.study_explain_text("??????")
        assert isinstance(short_explained, Ok)
        assert (
            short_explained.value.get("intent", {}).get("pure_switch", False) is False
        )
        assert (
            short_explained.value["reply"]
            == f"explained[teaching]: {short_explained.value['input_text']}"
        )
        assert plugin._agent.inputs[-1][0] == short_explained.value["input_text"]
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_explain_intent_without_content_returns_err(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        async with plugin._lock:
            plugin._state.last_ocr_text = ""
        explain_empty = await plugin.study_explain_text("explain")
        assert isinstance(explain_empty, Err)
        assert explain_empty.error.code == "MISSING_TEXT"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_generate_question_without_content_returns_err(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        async with plugin._lock:
            plugin._state.last_ocr_text = ""
        question_empty = await plugin.study_generate_question()

        assert isinstance(question_empty, Err)
        assert question_empty.error.code == "MISSING_TEXT"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_continues_when_mode_switch_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        lock_until = time.time() + 300.0
        async with plugin._lock:
            plugin._state.active_mode = MODE_COMPANION
            plugin._state.mode_started_at = 0.0
            plugin._state.mode_lock_until = lock_until
            plugin._state.recent_mode_switches = []
            plugin._cfg.mode = MODE_COMPANION
            plugin._cfg.default_mode = MODE_COMPANION
        plugin._mode_manager.restore(
            {
                "current_mode": MODE_COMPANION,
                "mode_started_at": 0.0,
                "recent_mode_switches": [],
                "suggestion_cooldowns": {},
                "session_suggestions": [],
                "mode_lock_until": lock_until,
            }
        )

        explained = await plugin.study_explain_text("教我光合作用")
        assert isinstance(explained, Ok)
        assert explained.value["intent"]["mode"] == MODE_TEACHING
        assert explained.value["mode_switch"]["changed"] is False
        assert explained.value["mode_switch"]["locked"] is True
        assert plugin._agent.inputs[-1][0] == "光合作用"
        assert plugin._agent.inputs[-1][2] == MODE_COMPANION
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_COMPANION
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]
        assert explained.value["reply"].startswith("explained[companion]: 光合作用")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_learning_context_builds_question_params_off_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ThreadCheckingTracker:
        def __init__(self, event_loop_thread_id: int) -> None:
            self.event_loop_thread_id = event_loop_thread_id
            self.calls: list[tuple[str, bool]] = []

        def get_next_question_params(self, topic_id: str = "") -> dict[str, object]:
            self.calls.append(
                (topic_id, threading.get_ident() != self.event_loop_thread_id)
            )
            return {"target_topic_id": topic_id, "threaded": self.calls[-1][1]}

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    tracker = _ThreadCheckingTracker(threading.get_ident())
    plugin._knowledge_tracker = tracker  # type: ignore[assignment]

    try:
        context = await plugin._build_learning_context(
            "question_generate",
            input_text="二次函数",
            extra={"topic_hint": "quadratic_vertex_form"},
        )

        assert (
            context["knowledge_question_params"]["target_topic_id"]
            == "quadratic_vertex_form"
        )
        assert tracker.calls == [("quadratic_vertex_form", True)]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_evaluate_answer_does_not_reuse_old_expected_answer_for_custom_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    fake_agent = _FakeTutorAgent()
    plugin._agent = fake_agent

    try:
        async with plugin._lock:
            plugin._state.current_question = {
                "question": "What process converts light to chemical energy?",
                "answer": "Photosynthesis",
            }

        evaluated = await plugin.study_evaluate_answer(
            question="What organelle stores genetic material?",
            answer="The nucleus.",
        )

        assert isinstance(evaluated, Ok)
        assert (
            fake_agent.evaluations[-1][0] == "What organelle stores genetic material?"
        )
        assert fake_agent.evaluations[-1][2] == ""
        assert fake_agent.evaluations[-1][3]["expected_answer"] == ""
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_evaluate_answer_custom_question_does_not_reuse_old_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _TrackingTutorAgent(_FakeTutorAgent):
        async def answer_evaluate(
            self,
            *,
            question: str = "",
            answer: str = "",
            expected_answer: str = "",
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            self.evaluations.append(
                (question, answer, expected_answer, dict(context or {}), mode)
            )
            return TutorReply(
                operation="answer_evaluate",
                input_text=answer,
                reply="The answer confuses organelles.",
                payload={
                    "verdict": "wrong",
                    "score": 10,
                    "error_type": "organelle_function",
                    "feedback": "The nucleus stores genetic material.",
                    "next_action": "Review nucleus function.",
                },
                created_at="2026-05-11T00:00:00Z",
            )

        async def knowledge_track(
            self,
            *,
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            return TutorReply(
                operation="knowledge_track",
                input_text=str((context or {}).get("input_text") or ""),
                reply="cell nucleus",
                payload={
                    "topic": "cell_nucleus",
                    "mastery_delta": -0.1,
                    "confidence": 0.8,
                    "weak_points": ["organelle_function"],
                    "next_steps": ["Review nucleus function"],
                },
                created_at="2026-05-11T00:00:00Z",
            )

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _TrackingTutorAgent()

    try:
        plugin._store.ensure_topic(
            topic_id="photosynthesis_topic", name="Photosynthesis"
        )
        plugin._store.ensure_topic(topic_id="cell_nucleus", name="Cell nucleus")
        async with plugin._lock:
            plugin._state.current_question = {
                "question": "What process converts light to chemical energy?",
                "answer": "Photosynthesis",
                "topic": "photosynthesis_topic",
                "difficulty": 2,
            }

        evaluated = await plugin.study_evaluate_answer(
            question="What organelle stores genetic material?",
            answer="The mitochondria.",
        )

        assert isinstance(evaluated, Ok)
        assert plugin._agent.evaluations[-1][3]["current_question"] == {}
        assert plugin._agent.evaluations[-1][3]["question_payload"] == {
            "question": "What organelle stores genetic material?",
            "answer": "",
        }
        assert plugin._store.get_latest_mastery("photosynthesis_topic") is None
        assert plugin._store.get_fsrs_card("photosynthesis_topic") is None
        assert plugin._store.list_wrong_questions(topic_id="photosynthesis_topic") == []
        assert plugin._store.get_latest_mastery("cell_nucleus") is not None
        assert plugin._store.get_fsrs_card("cell_nucleus") is not None
        assert plugin._store.list_wrong_questions(topic_id="cell_nucleus")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_evaluate_answer_persists_knowledge_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _TrackingTutorAgent(_FakeTutorAgent):
        async def answer_evaluate(
            self,
            *,
            question: str = "",
            answer: str = "",
            expected_answer: str = "",
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            self.evaluations.append(
                (question, answer, expected_answer, dict(context or {}), mode)
            )
            return TutorReply(
                operation="answer_evaluate",
                input_text=answer,
                reply="符号方向反了",
                payload={
                    "verdict": "wrong",
                    "score": 20,
                    "error_type": "sign_reversal",
                    "feedback": "符号方向反了",
                    "next_action": "复习顶点式中的 h",
                },
                created_at="2026-05-11T00:00:00Z",
            )

        async def knowledge_track(
            self,
            *,
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            return TutorReply(
                operation="knowledge_track",
                input_text=str((context or {}).get("input_text") or ""),
                reply="二次函数顶点式",
                payload={
                    "topic": "quadratic_vertex_form",
                    "mastery_delta": -0.1,
                    "confidence": 0.7,
                    "weak_points": ["sign_reversal"],
                    "next_steps": ["复习顶点式"],
                },
                created_at="2026-05-11T00:00:00Z",
            )

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_TEACHING},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _TrackingTutorAgent()

    try:
        async with plugin._lock:
            plugin._state.current_question = {
                "question": "二次函数 y=a(x-h)^2+k 的顶点是什么？",
                "answer": "(h,k)",
                "topic": "quadratic_vertex_form",
                "difficulty": 3,
            }

        evaluated = await plugin.study_evaluate_answer(
            answer="(-h,k)", _ctx={"run_id": "answer-run-1"}
        )
        assert isinstance(evaluated, Ok)
        assert evaluated.value["verdict"] == "wrong"

        mastery = plugin._store.get_latest_mastery("quadratic_vertex_form")
        assert mastery is not None
        assert mastery["level"] in {"薄弱", "进行中", "未接触"}
        assert plugin._store.get_fsrs_card("quadratic_vertex_form") is not None
        assert plugin._store.list_wrong_questions(topic_id="quadratic_vertex_form")
        session = next(
            item
            for item in plugin._store.list_sessions()
            if item["id"] == "answer-run-1"
        )
        assert session["question_count"] == 1
        assert session["topics_touched"] == ["quadratic_vertex_form"]

        status = await plugin.study_status()
        assert isinstance(status, Ok)
        assert status.value["knowledge_summary"]["tracked_topic_count"] >= 1
        assert status.value["knowledge_quality_summary"]["total"] >= 1
        assert "anonymous_knowledge_stats_summary" in status.value
        assert status.value["weak_topics"]
        assert status.value["mastery_overview"]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_tutor_agent_prompt_and_reply_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = build_concept_explain_messages(
        text="A derivative measures instantaneous rate of change.",
        language="en",
        mode=MODE_INTERACTIVE,
        context={"source": "unit-test", "mode": MODE_INTERACTIVE},
    )
    assert messages[0]["role"] == "system"
    assert "unit-test" in messages[1]["content"]
    assert "Mode: interactive" in messages[1]["content"]

    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages):
        return "A derivative is the slope at one point."

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await agent.concept_explain("derivative", mode=MODE_INTERACTIVE)

    assert reply.operation == "concept_explain"
    assert reply.reply == "A derivative is the slope at one point."
    assert reply.degraded is False


@pytest.mark.asyncio
async def test_tutor_agent_teaching_prefix_is_applied_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    teaching_prefix = build_transition_phrase(
        MODE_TEACHING, language="en", outcome="changed"
    )

    async def _fake_call_model(_messages):
        return f"{teaching_prefix}\n\nA derivative is the slope at one point."

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await agent.concept_explain("derivative", mode=MODE_TEACHING)

    assert reply.operation == "concept_explain"
    assert reply.reply.count(teaching_prefix) == 1
    assert reply.reply.startswith(teaching_prefix)


@pytest.mark.asyncio
async def test_tutor_agent_handles_empty_and_model_failures() -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    empty = await agent.concept_explain(" ")
    assert empty.degraded is True
    assert empty.diagnostic == "empty_input"

    async def _broken_call_model(_messages):
        raise RuntimeError("llm unavailable")

    agent._call_model = _broken_call_model  # type: ignore[method-assign]
    fallback = await agent.concept_explain("photosynthesis converts light")

    assert fallback.degraded is True
    assert fallback.diagnostic == "llm_call_failed"
    assert "photosynthesis converts light" in fallback.reply

    zh_agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))
    zh_empty = await zh_agent.concept_explain(" ")
    assert zh_empty.diagnostic == "empty_input"
    assert "请先提供文本" in zh_empty.reply

    zh_agent._call_model = _broken_call_model  # type: ignore[method-assign]
    zh_fallback = await zh_agent.concept_explain("光合作用")
    assert zh_fallback.diagnostic == "llm_call_failed"
    assert "关键文本：光合作用" in zh_fallback.reply

    ja_agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="ja"))
    ja_empty = await ja_agent.concept_explain(" ")
    assert "テキスト" in ja_empty.reply

    ja_agent._call_model = _broken_call_model  # type: ignore[method-assign]
    ja_fallback = await ja_agent.concept_explain("微分")
    assert ja_fallback.diagnostic == "llm_call_failed"
    assert "重要なテキスト：微分" in ja_fallback.reply


def test_json_corrector_parses_plain_json() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object('{"answer": "42"}') == {"answer": "42"}


def test_json_corrector_parses_code_fenced_json() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object('```json\n{"question": "What is it?"}\n```') == {
        "question": "What is it?"
    }


def test_json_corrector_recovers_json_from_surrounding_text() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object(
        'noise before {"topic": "biology", "score": 90} noise after'
    ) == {
        "topic": "biology",
        "score": 90,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_name, kwargs, response_json, expected_field, expected_value",
    [
        (
            "question_generate",
            {
                "text": "Photosynthesis converts light to chemical energy.",
                "mode": MODE_INTERACTIVE,
                "context": {"screen_classification": {"screen_type": "reading"}},
            },
            {
                "question": "What process converts light to chemical energy?",
                "answer": "Photosynthesis",
                "hint": "Look for the named process.",
                "difficulty": 2,
                "topic": "biology",
                "screen_type": "reading",
            },
            "question",
            "What process converts light to chemical energy?",
        ),
        (
            "answer_evaluate",
            {
                "question": "What process converts light to chemical energy?",
                "answer": "It is photosynthesis.",
                "expected_answer": "Photosynthesis",
                "mode": MODE_COMPANION,
                "context": {"screen_classification": {"screen_type": "answering"}},
            },
            {
                "verdict": "correct",
                "score": 95,
                "error_type": "none",
                "feedback": "Correct.",
                "next_action": "Move on.",
                "screen_type": "answering",
            },
            "verdict",
            "correct",
        ),
        (
            "knowledge_track",
            {
                "mode": MODE_COMPANION,
                "context": {
                    "screen_classification": {"screen_type": "review"},
                    "session_summary_seed": {"weak_points": ["definition"]},
                },
            },
            {
                "topic": "photosynthesis",
                "mastery_delta": 0.1,
                "confidence": 0.8,
                "weak_points": ["definition"],
                "next_steps": ["Review the definition"],
                "session_summary_seed": {"weak_points": ["definition"]},
                "screen_type": "review",
            },
            "topic",
            "photosynthesis",
        ),
        (
            "summarize_session",
            {
                "history": [
                    {
                        "kind": "question_generate",
                        "output_text": "What process converts light to chemical energy?",
                    }
                ],
                "mode": MODE_TEACHING,
                "context": {"screen_classification": {"screen_type": "summary"}},
            },
            {
                "summary": "The learner reviewed photosynthesis.",
                "highlights": ["Generated one question"],
                "weak_points": ["definition"],
                "next_actions": ["Review the definition"],
                "markdown": "## Summary\n\nThe learner reviewed photosynthesis.",
                "screen_type": "summary",
            },
            "summary",
            "The learner reviewed photosynthesis.",
        ),
    ],
)
async def test_tutor_agent_structured_operations_normal_path(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    kwargs: dict[str, object],
    response_json: dict[str, object],
    expected_field: str,
    expected_value: object,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages, *, operation: str = "concept_explain"):
        assert operation == operation_name
        return json.dumps(response_json)

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await getattr(agent, operation_name)(**kwargs)

    assert reply.operation == operation_name
    assert reply.degraded is False
    assert reply.payload[expected_field] == expected_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_name, kwargs",
    [
        (
            "question_generate",
            {
                "text": "Photosynthesis converts light to chemical energy.",
                "context": {"screen_classification": {"screen_type": "reading"}},
            },
        ),
        (
            "answer_evaluate",
            {
                "question": "What process converts light to chemical energy?",
                "answer": "It is photosynthesis.",
                "expected_answer": "Photosynthesis",
                "context": {"screen_classification": {"screen_type": "answering"}},
            },
        ),
        (
            "knowledge_track",
            {"context": {"screen_classification": {"screen_type": "review"}}},
        ),
        (
            "summarize_session",
            {
                "history": [
                    {
                        "kind": "question_generate",
                        "output_text": "What process converts light to chemical energy?",
                    }
                ],
                "context": {"screen_classification": {"screen_type": "summary"}},
            },
        ),
    ],
)
async def test_tutor_agent_structured_operations_degrade_with_generic_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    kwargs: dict[str, object],
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _broken_call_model(_messages, *, operation: str = "concept_explain"):
        raise RuntimeError("secret api endpoint https://example.invalid/v1 key=sk-123")

    monkeypatch.setattr(agent, "_call_model", _broken_call_model)
    reply = await getattr(agent, operation_name)(**kwargs)

    assert reply.operation == operation_name
    assert reply.degraded is True
    assert reply.diagnostic == "llm_call_failed"
    assert "secret api endpoint" not in reply.reply


@pytest.mark.asyncio
async def test_tutor_agent_llm_cache_distinguishes_rotated_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils import config_manager, llm_client, token_tracker

    config_groups: list[str] = []
    call_types: list[str] = []

    class _ConfigManager:
        def __init__(self) -> None:
            self.api_key = "old-key"

        def get_model_api_config(self, group: str):
            config_groups.append(group)
            return {
                "base_url": "https://llm.example.test/v1",
                "model": "study-model",
                "api_key": self.api_key,
            }

    class _FakeLLM:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        async def ainvoke(self, _messages):
            return SimpleNamespace(content=f"reply from {self.api_key}")

    cfg_mgr = _ConfigManager()
    created_keys: list[str] = []
    create_kwargs: list[dict[str, object]] = []

    def _create_chat_llm(*, api_key: str, **kwargs):
        created_keys.append(api_key)
        create_kwargs.append(dict(kwargs))
        return _FakeLLM(api_key)

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: cfg_mgr)
    monkeypatch.setattr(llm_client, "create_chat_llm", _create_chat_llm)
    monkeypatch.setattr(token_tracker, "set_call_type", call_types.append)

    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    first = await agent._call_model([{"role": "user", "content": "one"}])
    cfg_mgr.api_key = "new-key"
    second = await agent._call_model([{"role": "user", "content": "two"}])

    assert first == "reply from old-key"
    assert second == "reply from new-key"
    assert config_groups == ["agent", "agent"]
    assert call_types == ["agent", "agent"]
    assert created_keys == ["old-key", "new-key"]
    assert create_kwargs
    assert all("temperature" not in item for item in create_kwargs)
    assert all("max_completion_tokens" not in item for item in create_kwargs)
    assert "old-key" not in repr(agent._client_cache._cache)
    assert "new-key" not in repr(agent._client_cache._cache)


@pytest.mark.asyncio
async def test_study_pomodoro_status_offloads_timer_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Timer:
        def status(self) -> dict[str, object]:
            return {"state": "focusing", "remaining_seconds": 1}

        def tick(self) -> dict[str, object]:
            return {"state": "short_break", "remaining_seconds": 300}

    class _Supervision:
        def __init__(self) -> None:
            self.focus_end_count = 0

        def on_focus_end(self) -> None:
            self.focus_end_count += 1

    to_thread_calls: list[str] = []

    async def _to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_status()

    assert isinstance(status, Ok)
    assert status.value["state"] == "short_break"
    assert to_thread_calls == ["status", "tick"]
    assert supervision.focus_end_count == 1


@pytest.mark.asyncio
async def test_study_pomodoro_status_drives_supervision_reminders() -> None:
    class _Timer:
        def status(self) -> dict[str, object]:
            return {"state": "focusing", "remaining_seconds": 120}

        def tick(self) -> dict[str, object]:
            return {"state": "focusing", "remaining_seconds": 119}

    class _Supervision:
        def __init__(self) -> None:
            self.reminder_count = 0

        def due_reminder(self) -> dict[str, object]:
            self.reminder_count += 1
            return {"due": True, "reminder_level": "low_frequency"}

    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_status()

    assert isinstance(status, Ok)
    assert status.value["state"] == "focusing"
    assert status.value["supervision_reminder"] == {
        "due": True,
        "reminder_level": "low_frequency",
    }
    assert supervision.reminder_count == 1


@pytest.mark.asyncio
async def test_study_pomodoro_stop_offloads_timer_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Timer:
        def stop(self) -> dict[str, object]:
            return {
                "state": "cancelled",
                "current_focus_session": {"id": "focus-1", "status": "cancelled"},
            }

    class _Supervision:
        def __init__(self) -> None:
            self.focus_end_count = 0

        def on_focus_end(self) -> None:
            self.focus_end_count += 1

    to_thread_calls: list[str] = []

    async def _to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_stop()

    assert isinstance(status, Ok)
    assert status.value["state"] == "cancelled"
    assert to_thread_calls == ["stop"]
    assert supervision.focus_end_count == 1


@pytest.mark.asyncio
async def test_study_pomodoro_start_does_not_restart_supervision_on_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Habits:
        def get_goal(self, _goal_id: str) -> dict[str, object]:
            return {"id": "goal-1"}

    class _Timer:
        def status(self) -> dict[str, object]:
            return {
                "state": "focusing",
                "current_focus_session": {"id": "focus-1"},
                "config": {"focus_minutes": 25},
            }

        def start(self, **_kwargs) -> dict[str, object]:
            return self.status()

    class _Supervision:
        def __init__(self) -> None:
            self.focus_start_count = 0

        def on_focus_start(self, **_kwargs) -> None:
            self.focus_start_count += 1

    to_thread_calls: list[str] = []

    async def _to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._cfg = StudyConfig()
    plugin._habit_store = _Habits()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_start(goal_id="goal-1")

    assert isinstance(status, Ok)
    assert status.value["state"] == "focusing"
    assert to_thread_calls == ["status", "start"]
    assert supervision.focus_start_count == 0


@pytest.mark.asyncio
async def test_study_pomodoro_start_offloads_blocking_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Habits:
        def get_goal(self, goal_id: str) -> dict[str, object]:
            return {"id": goal_id, "title": "read"}

    class _Timer:
        def status(self) -> dict[str, object]:
            return {"state": "idle", "current_focus_session": {}}

        def start(self, **_kwargs) -> dict[str, object]:
            return {
                "state": "focusing",
                "current_focus_session": {"id": "focus-2"},
                "config": {"focus_minutes": 30},
            }

    class _Supervision:
        def __init__(self) -> None:
            self.goal: dict[str, object] = {}
            self.planned_minutes = 0.0

        def on_focus_start(
            self, *, goal: dict[str, object], planned_minutes: float
        ) -> None:
            self.goal = goal
            self.planned_minutes = planned_minutes

    to_thread_calls: list[str] = []

    async def _to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._cfg = StudyConfig()
    plugin._habit_store = _Habits()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_start(goal_id="goal-1", focus_minutes=30)

    assert isinstance(status, Ok)
    assert status.value["state"] == "focusing"
    assert to_thread_calls == ["status", "start", "get_goal"]
    assert supervision.goal == {"id": "goal-1", "title": "read"}
    assert supervision.planned_minutes == 30.0


@pytest.mark.asyncio
async def test_study_pomodoro_start_uses_validated_focus_minutes_as_supervision_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Habits:
        def get_goal(self, goal_id: str) -> dict[str, object]:
            return {"id": goal_id}

    class _Timer:
        def status(self) -> dict[str, object]:
            return {"state": "idle", "current_focus_session": {}}

        def start(self, **_kwargs) -> dict[str, object]:
            return {"state": "focusing", "current_focus_session": {"id": "focus-4"}}

    class _Supervision:
        def __init__(self) -> None:
            self.planned_minutes = -1.0

        def on_focus_start(
            self, *, goal: dict[str, object], planned_minutes: float
        ) -> None:
            self.planned_minutes = planned_minutes

    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._cfg = StudyConfig()
    plugin._habit_store = _Habits()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = supervision

    status = await plugin.study_pomodoro_start(goal_id="goal-1")

    assert isinstance(status, Ok)
    assert supervision.planned_minutes == 25.0


@pytest.mark.asyncio
async def test_study_pomodoro_start_sanitizes_deck_focus_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Habits:
        def get_goal(self, goal_id: str) -> dict[str, object]:
            return {"id": goal_id, "title": "deck"}

    class _Timer:
        def __init__(self) -> None:
            self.started_focus_minutes = 0

        def status(self) -> dict[str, object]:
            return {"state": "idle", "current_focus_session": {}}

        def start(self, **kwargs) -> dict[str, object]:
            self.started_focus_minutes = int(kwargs["focus_minutes"])
            return {
                "state": "focusing",
                "current_focus_session": {"id": "focus-3"},
                "config": {"focus_minutes": self.started_focus_minutes},
            }

    class _Bridge:
        def __init__(self) -> None:
            self.focus_minutes = 0.0

        def resolve_focus_goal(self, **kwargs) -> dict[str, object]:
            self.focus_minutes = float(kwargs["focus_minutes"])
            return {"goal": {"id": "deck-goal"}}

    class _Supervision:
        def on_focus_start(self, **_kwargs) -> None:
            return None

    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    timer = _Timer()
    bridge = _Bridge()
    plugin._cfg = StudyConfig()
    plugin._habit_store = _Habits()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = timer
    plugin._supervision = _Supervision()
    plugin._memory_habit_bridge = bridge

    status = await plugin.study_pomodoro_start(deck_id="deck-1", focus_minutes=500)

    assert isinstance(status, Ok)
    assert bridge.focus_minutes == 25.0
    assert timer.started_focus_minutes == 25


@pytest.mark.asyncio
async def test_study_pomodoro_start_does_not_create_deck_goal_on_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Habits:
        def get_goal(self, _goal_id: str) -> dict[str, object]:
            return {}

    class _Timer:
        def status(self) -> dict[str, object]:
            return {
                "state": "focusing",
                "current_focus_session": {"id": "focus-existing"},
                "config": {"focus_minutes": 25},
            }

        def start(self, **_kwargs) -> dict[str, object]:
            return self.status()

    class _Bridge:
        def resolve_focus_goal(self, **_kwargs) -> dict[str, object]:
            raise AssertionError("deck goal should not be resolved for a no-op start")

    class _Supervision:
        def on_focus_start(self, **_kwargs) -> None:
            raise AssertionError("supervision should not restart")

    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(study_companion_module.asyncio, "to_thread", _to_thread)
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig()
    plugin._habit_store = _Habits()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = _Timer()
    plugin._supervision = _Supervision()
    plugin._memory_habit_bridge = _Bridge()

    status = await plugin.study_pomodoro_start(deck_id="deck-1", focus_minutes=25)

    assert isinstance(status, Ok)
    assert status.value["current_focus_session"]["id"] == "focus-existing"


@pytest.mark.asyncio
async def test_study_supervision_toggle_respects_disable_guard() -> None:
    class _Supervision:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def set_enabled(self, enabled: bool) -> dict[str, object]:
            self.calls.append(enabled)
            return {"enabled": enabled}

    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._cfg = StudyConfig()
    plugin._cfg.supervision.allow_disable_by_chat = False
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = object()
    plugin._supervision = supervision

    result = await plugin.study_supervision_toggle(enabled=False)

    assert isinstance(result, Err)
    assert "blocked by config" in str(result.error)
    assert supervision.calls == []


@pytest.mark.asyncio
async def test_study_supervision_toggle_parses_string_false() -> None:
    class _Supervision:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def set_enabled(self, enabled: bool) -> dict[str, object]:
            self.calls.append(enabled)
            return {"enabled": enabled}

    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    supervision = _Supervision()
    plugin._cfg = StudyConfig()
    plugin._cfg.supervision.allow_disable_by_chat = True
    plugin._habit_store = object()
    plugin._checkin_manager = object()
    plugin._pomodoro_timer = object()
    plugin._supervision = supervision

    result = await plugin.study_supervision_toggle(enabled="false")

    assert isinstance(result, Ok)
    assert result.value["enabled"] is False
    assert supervision.calls == [False]


@pytest.mark.asyncio
async def test_study_plugin_starts_and_collects_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()

    assert isinstance(result, Ok)
    entries = plugin.collect_entries()
    assert "study_status" in entries
    assert "study_explain_text" in entries
    assert "study_ocr_snapshot" in entries
    assert "study_set_mode" in entries
    assert "study_detect_mode_intent" in entries
    assert "study_export_notes" not in entries
    assert "study_knowledge_map" in entries
    assert "study_memory_card_upsert" in entries
    assert "study_memory_deck" in entries
    assert "study_memory_card_review" in entries
    assert "study_memory_create_deck" in entries
    assert "study_memory_import_words" in entries
    assert "study_memory_import_passage" in entries
    assert "study_memory_due_reviews" in entries
    assert "study_memory_review_item" in entries
    assert "study_memory_recitation_attempt" in entries
    assert "study_set_knowledge_contribution_opt_in" in entries
    disabled_export = await plugin._study_export_notes_entry(
        fmt="markdown", preview_only=True, title="Default Notes"
    )
    assert isinstance(disabled_export, Err)
    memory_card = await plugin.study_memory_card_upsert(
        topic_id="phase7_plugin_memory",
        front="What does the study memory deck store?",
        back="Reviewable recall cards.",
        tags=["phase7"],
    )
    assert isinstance(memory_card, Ok)
    card_item_id = memory_card.value["card"]["item_id"]
    updated_memory_card = await plugin.study_memory_card_upsert(
        topic_id="phase7_plugin_memory",
        front="Updated study memory prompt",
        back="Updated reviewable recall card.",
        difficulty=0.0,
        tags=["phase7", "updated"],
    )
    assert isinstance(updated_memory_card, Ok)
    assert updated_memory_card.value["created"] is False
    assert updated_memory_card.value["card"]["item_id"] == card_item_id
    assert updated_memory_card.value["card"]["topic_id"] == "phase7_plugin_memory"
    assert updated_memory_card.value["card"]["front"] == "Updated study memory prompt"
    assert (
        updated_memory_card.value["card"]["item"]["metadata"]["topic_id"]
        == "phase7_plugin_memory"
    )
    assert updated_memory_card.value["card"]["item"]["metadata"]["difficulty"] == 0.0
    fresh_memory_card = await plugin.study_memory_card_upsert(
        topic_id="phase7_plugin_recent",
        front="Recently updated prompt",
        back="Recently updated answer.",
    )
    assert isinstance(fresh_memory_card, Ok)
    fresh_item_id = fresh_memory_card.value["card"]["item_id"]
    reviewed_fresh = await plugin.study_memory_card_review(
        topic_id="phase7_plugin_recent", rating="easy"
    )
    assert isinstance(reviewed_fresh, Ok)
    assert reviewed_fresh.value["card"]["item_id"] == fresh_item_id
    assert reviewed_fresh.value["card"]["topic_id"] == "phase7_plugin_recent"
    plugin._store.ensure_topic(topic_id="phase7_topic_due", name="Phase 7 Topic")
    topic_card = plugin._knowledge_tracker.fsrs.new_knowledge_card(
        "phase7_topic_due"
    ).to_dict()
    plugin._store.upsert_fsrs_card(
        topic_id="phase7_topic_due", card=topic_card, last_rating=0
    )
    due_deck = await plugin.study_memory_deck(limit=5, due_only=True)
    assert isinstance(due_deck, Ok)
    assert any(item["item_id"] == card_item_id for item in due_deck.value["cards"])
    topic_due_deck = await plugin.study_memory_deck(
        limit=1, due_only=True, include_topic_cards=True
    )
    assert isinstance(topic_due_deck, Ok)
    assert all(item["is_due"] for item in topic_due_deck.value["cards"])
    assert any(
        item["item_id"] == card_item_id for item in topic_due_deck.value["cards"]
    )
    assert all(
        item["item_id"] != fresh_item_id for item in topic_due_deck.value["cards"]
    )
    assert topic_due_deck.value["due_count"] >= len(topic_due_deck.value["due_cards"])
    topic_deck = await plugin.study_memory_deck(
        limit=5, due_only=True, include_topic_cards=True
    )
    assert isinstance(topic_deck, Ok)
    assert any(
        item["topic_id"] == "phase7_topic_due" for item in topic_deck.value["cards"]
    )
    assert topic_deck.value["card_count"] == len(topic_deck.value["cards"])
    merged_deck = await plugin.study_memory_deck(
        limit=1, due_only=False, include_topic_cards=True
    )
    assert isinstance(merged_deck, Ok)
    assert len(merged_deck.value["cards"]) == 1
    assert merged_deck.value["card_count"] == 1
    loose_card = await plugin.study_memory_card_upsert(front="Loose prompt", back="A")
    loose_card_again = await plugin.study_memory_card_upsert(
        front="Another loose prompt", back="B"
    )
    assert isinstance(loose_card, Ok)
    assert isinstance(loose_card_again, Ok)
    assert loose_card.value["created"] is True
    assert loose_card_again.value["created"] is True
    assert (
        loose_card.value["card"]["item_id"] != loose_card_again.value["card"]["item_id"]
    )
    reviewed_again = await plugin.study_memory_review_item(
        item_id=card_item_id, rating="again"
    )
    assert isinstance(reviewed_again, Ok)
    assert reviewed_again.value["review_record"]["correct"] == 0
    inferred_review = await plugin.study_memory_review_item(
        item_id=card_item_id, correct=False, error_type="spelling"
    )
    assert isinstance(inferred_review, Ok)
    assert inferred_review.value["rating"] == 2
    reviewed = await plugin.study_memory_card_review(
        topic_id="phase7_plugin_memory", rating="good"
    )
    assert isinstance(reviewed, Ok)
    assert reviewed.value["rating"] == 3
    reviewed_topic = await plugin.study_memory_card_review(
        topic_id="phase7_topic_due", rating="good"
    )
    assert isinstance(reviewed_topic, Ok)
    assert reviewed_topic.value["topic_id"] == "phase7_topic_due"
    assert reviewed_topic.value["rating"] == 3
    assert reviewed_topic.value["card"]["topic_id"] == "phase7_topic_due"
    status = await plugin.study_status()
    assert isinstance(status, Ok)
    assert status.value["status"] == "ready"
    assert status.value["is_first_run"] is True
    assert status.value["active_mode"] == MODE_COMPANION
    assert "mode_started_at" in status.value
    assert "recent_mode_switches" in status.value
    assert status.value["knowledge_summary"]["topic_count"] >= 120
    assert "review_queue" in status.value
    assert status.value["memory_deck"]["card_count"] >= 1
    assert "weak_topics" in status.value
    assert "mastery_overview" in status.value
    assert (
        runtime_root / "plugins" / "study_companion" / "data" / "study_companion.db"
    ).is_file()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_status_degrades_when_habit_payload_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)

        class _BrokenHabitStore:
            def list_goals(self, **_kwargs):
                raise RuntimeError("habit db unavailable")

        plugin._habit_store = _BrokenHabitStore()  # type: ignore[assignment]

        status = await plugin.study_status()

        assert isinstance(status, Ok)
        assert status.value["status"] == "ready"
        assert status.value["habit"]["available"] is False
        assert "habit db unavailable" in status.value["habit"]["error"]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_communication_disabled_skips_eventbus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        status = await plugin.study_neko_communication_status()
        assert isinstance(status, Ok)
        assert status.value == {
            "available": False,
            "events_emitted": 0,
            "events_blocked": 0,
        }
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_screen_classification_change_emits_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(
        study_companion_module,
        "classify_screen_from_ocr",
        lambda *_args, **_kwargs: ScreenClassification(
            screen_type="question",
            confidence=0.95,
            reason="unit-test",
        ),
    )
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        for _ in range(3):
            await plugin._update_screen_classification("Question: solve x + 1 = 2")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        texts = _study_push_texts(ctx)
        assert len(texts) == 1
        assert "[Screen Context Changed]" in texts[0]
        assert "question" in texts[0]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_screen_classification_no_change_skips_duplicate_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(
        study_companion_module,
        "classify_screen_from_ocr",
        lambda *_args, **_kwargs: ScreenClassification(
            screen_type="question",
            confidence=0.95,
            reason="unit-test",
        ),
    )
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        for _ in range(4):
            await plugin._update_screen_classification("Question: solve x + 1 = 2")
        await _drain_scheduled_events()

        assert len(_study_push_texts(ctx)) == 1
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_evaluate_answer_emits_answer_evaluated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()
    try:
        evaluated = await plugin.study_evaluate_answer(
            question="What is a derivative?",
            answer="A slope.",
        )

        assert isinstance(evaluated, Ok)
        await _drain_scheduled_events()
        texts = _study_push_texts(ctx)
        assert any("[Answer Evaluated]" in text for text in texts)
        assert any("What is a derivative?" in text for text in texts)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_evaluate_answer_mastery_lookup_failure_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TopicTrackingAgent(_FakeTutorAgent):
        async def answer_evaluate(
            self,
            *,
            question: str = "",
            answer: str = "",
            expected_answer: str = "",
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            self.evaluations.append(
                (question, answer, expected_answer, dict(context or {}), mode)
            )
            return TutorReply(
                operation="answer_evaluate",
                input_text=answer,
                reply="evaluated",
                payload={
                    "verdict": "partial",
                    "score": 50,
                    "topic": "derivatives",
                    "feedback": "review derivative rules",
                },
                created_at="2026-05-11T00:00:00Z",
            )

        async def knowledge_track(
            self,
            *,
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            return TutorReply(
                operation="knowledge_track",
                input_text=str((context or {}).get("input_text") or ""),
                reply="derivatives",
                payload={"topic": "derivatives"},
                created_at="2026-05-11T00:00:00Z",
            )

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin.logger = ctx.logger
    plugin._agent = _TopicTrackingAgent()

    def _fail_get_mastery(_topic: str) -> float:
        raise RuntimeError("mastery read failed")

    original_on_answer = plugin._knowledge_tracker.on_answer
    on_answer_topics: list[str] = []

    def _record_on_answer(**kwargs):
        on_answer_topics.append(str(kwargs.get("topic_id") or ""))
        return original_on_answer(**kwargs)

    monkeypatch.setattr(plugin._knowledge_tracker, "get_mastery", _fail_get_mastery)
    monkeypatch.setattr(plugin._knowledge_tracker, "on_answer", _record_on_answer)
    try:
        evaluated = await plugin.study_evaluate_answer(
            question="What is a derivative?",
            expected_answer="A rate of change.",
            answer="A slope.",
        )

        assert isinstance(evaluated, Ok)
        assert on_answer_topics == ["derivatives"]
        await _drain_scheduled_events()
        assert any("[Answer Evaluated]" in text for text in _study_push_texts(ctx))
        warnings = [str(args[0]) for args, _kwargs in ctx.logger.warnings]
        assert any(
            "study knowledge tracker mastery-before read failed" in item
            for item in warnings
        )
        assert any(
            "study knowledge tracker mastery-after read failed" in item
            for item in warnings
        )
        assert any(
            "study answer mastery enrichment failed" in item for item in warnings
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_memory_review_emits_answer_evaluated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        deck = plugin._memory_deck_store.create_deck(
            name="Exam Words", deck_type="word", language="en"
        )
        item = plugin._memory_deck_store.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
        )["item"]

        reviewed = await plugin.study_memory_review_item(
            item_id=item["id"], rating="good", correct=True
        )

        assert isinstance(reviewed, Ok)
        await _drain_scheduled_events()
        assert any("[Answer Evaluated]" in text for text in _study_push_texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_memory_review_event_failure_does_not_fail_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        plugin.logger = ctx.logger
        deck = plugin._memory_deck_store.create_deck(
            name="Exam Words", deck_type="word", language="en"
        )
        item = plugin._memory_deck_store.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
        )["item"]

        async def _fail_emit(_payload: dict[str, object]) -> None:
            raise RuntimeError("event enrichment failed")

        plugin._emit_memory_review_answer_event = _fail_emit  # type: ignore[method-assign]

        reviewed = await plugin.study_memory_review_item(
            item_id=item["id"], rating="good", correct=True
        )

        assert isinstance(reviewed, Ok)
        assert reviewed.value["review_record"]["item_id"] == item["id"]
        assert any(
            "memory review event emission degraded" in str(args[0])
            for args, _kwargs in ctx.logger.warnings
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_review_due_background_task_emits_without_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(
        study_companion_module,
        "_REVIEW_DUE_INTERVAL_SECONDS",
        0.01,
    )
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        assert plugin._review_due_task is not None
        assert not plugin._review_due_task.done()
        deck = plugin._memory_deck_store.create_deck(
            name="Exam Words", deck_type="word", language="en"
        )
        plugin._memory_deck_store.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            await _drain_scheduled_events()
            if any("[Review Due]" in text for text in _study_push_texts(ctx)):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("timed out waiting for review due push")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_review_due_event_includes_knowledge_tracker_cards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        await plugin._cancel_review_due_task()
        plugin._store.ensure_topic(
            topic_id="derivatives",
            name="Derivatives",
            subject="math",
            chapter="calculus",
        )
        card = plugin._knowledge_tracker.fsrs.new_knowledge_card(
            "derivatives"
        ).to_dict()
        plugin._store.upsert_fsrs_card(topic_id="derivatives", card=card, last_rating=0)

        await plugin._emit_review_due_if_needed()
        await _drain_scheduled_events()

        texts = _study_push_texts(ctx)
        assert any("[Review Due]" in text for text in texts)
        assert any("Derivatives" in text for text in texts)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_status_does_not_drive_review_due_emission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        await plugin._cancel_review_due_task()
        calls = 0

        async def _count_review_due() -> None:
            nonlocal calls
            calls += 1

        plugin._emit_review_due_if_needed = (  # type: ignore[method-assign]
            _count_review_due
        )

        status = await plugin.study_status()

        assert isinstance(status, Ok)
        assert calls == 0
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_recitation_emits_answer_evaluated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        deck = plugin._memory_deck_store.create_deck(name="Texts", deck_type="passage")
        imported = plugin._memory_deck_store.import_passage(
            deck_id=deck["id"],
            title="Short Text",
            text="First sentence. Second sentence.",
        )

        recited = await plugin.study_memory_recitation_attempt(
            item_id=imported["items"][0]["id"],
            user_input_text="First sentence.",
        )

        assert isinstance(recited, Ok)
        await _drain_scheduled_events()
        assert any("[Answer Evaluated]" in text for text in _study_push_texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_summarize_session_emits_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()
    try:
        summarized = await plugin.study_summarize_session()

        assert isinstance(summarized, Ok)
        await _drain_scheduled_events()
        texts = _study_push_texts(ctx)
        assert any("[Session Summarized]" in text for text in texts)
        assert any("Derivative rules improved." in text for text in texts)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_session_summarized_falls_back_to_answer_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        async with plugin._lock:
            plugin._state.session_summary_seed = {
                "answer_count": 3,
                "question_count": 5,
                "verdict_counts": {"correct": 2},
                "last_topic": "Derivatives",
            }

        await plugin._emit_session_summarized_event(
            {
                "duration_minutes": "12.5",
                "questions_attempted": "two",
                "summary": "Answered supplied derivative questions.",
            }
        )
        await _drain_scheduled_events()

        texts = _study_push_texts(ctx)
        assert any("12 min" in text for text in texts)
        assert any("3 question(s)" in text for text in texts)
        assert any("67%" in text for text in texts)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_evaluate_answer_emits_mastery_updated_on_threshold_cross(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CorrectTrackingAgent(_FakeTutorAgent):
        async def answer_evaluate(
            self,
            *,
            question: str = "",
            answer: str = "",
            expected_answer: str = "",
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            self.evaluations.append(
                (question, answer, expected_answer, dict(context or {}), mode)
            )
            return TutorReply(
                operation="answer_evaluate",
                input_text=answer,
                reply="Correct.",
                payload={
                    "verdict": "correct",
                    "score": 100,
                    "feedback": "Correct.",
                },
                created_at="2026-05-11T00:00:00Z",
            )

        async def knowledge_track(
            self,
            *,
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            return TutorReply(
                operation="knowledge_track",
                input_text=str((context or {}).get("input_text") or ""),
                reply="derivatives",
                payload={"topic": "derivatives"},
                created_at="2026-05-11T00:00:00Z",
            )

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _CorrectTrackingAgent()
    try:
        plugin._store.ensure_topic(topic_id="derivatives", name="Derivatives")

        evaluated = await plugin.study_evaluate_answer(
            question="What is d/dx x^2?",
            expected_answer="2x",
            answer="2x",
        )

        assert isinstance(evaluated, Ok)
        await _drain_scheduled_events()
        texts = _study_push_texts(ctx)
        assert any("[Mastery Updated]" in text for text in texts)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_shutdown_continues_when_dynamic_entry_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    shutdown_called = False

    class _Agent:
        async def shutdown(self):
            nonlocal shutdown_called
            shutdown_called = True

    def _fail_unregister(_entry_id: str) -> None:
        raise RuntimeError("unregister failed")

    plugin._agent = _Agent()
    plugin.logger = ctx.logger
    plugin.unregister_dynamic_entry = _fail_unregister  # type: ignore[method-assign]

    shutdown_result = await plugin.shutdown()

    assert isinstance(shutdown_result, Ok)
    assert shutdown_called is True
    assert any(
        "dynamic entry cleanup failed" in str(args[0])
        for args, _kwargs in ctx.logger.warnings
    )


@pytest.mark.asyncio
async def test_study_plugin_doc_export_dynamic_entry_and_knowledge_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
            "doc_export": {
                "enabled": True,
                "default_style": "compact",
                "xmind_enabled": False,
            },
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        entries = plugin.collect_entries()
        assert "study_export_notes" in entries
        properties = entries["study_export_notes"].meta.input_schema["properties"]
        export_formats = properties["fmt"]["enum"]
        assert export_formats == ["markdown", "pdf", "docx"]
        assert "range" not in properties
        assert properties["style"]["default"] == "compact"
        assert properties["time_range"]["default"] == "recent"
        assert properties["recent_limit"]["default"] == 30
        assert properties["topic_ids"]["default"] == []
        plugin._store.append_interaction(
            kind="concept_explain",
            input_text="derivative",
            output_text="A derivative is a rate of change.",
            history_limit=10,
        )

        exported = await entries["study_export_notes"].handler(
            fmt="markdown", preview_only=False, title="Unit Notes"
        )
        assert isinstance(exported, Ok)
        assert exported.value["filename"] == "unit-notes.md"
        assert exported.value["format"] == "markdown"
        assert exported.value["style"] == "compact"
        assert exported.value["content_base64"]
        assert "Range: recent" in exported.value["markdown"]
        assert "derivative" in exported.value["markdown"]

        explicit_style = await entries["study_export_notes"].handler(
            fmt="markdown",
            style="academic",
            preview_only=True,
            title="Academic Notes",
        )
        assert isinstance(explicit_style, Ok)
        assert explicit_style.value["style"] == "academic"

        knowledge_map = await plugin.study_knowledge_map(limit=10)
        assert isinstance(knowledge_map, Ok)
        assert knowledge_map.value["summary"]["topic_count"] >= 0
        assert isinstance(knowledge_map.value["nodes"], list)

        opt_in = await plugin.study_set_knowledge_contribution_opt_in(opt_in=True)
        assert isinstance(opt_in, Ok)
        assert opt_in.value["opt_in"] is True
        preview = await plugin.study_anonymous_knowledge_preview(limit=10)
        assert isinstance(preview, Ok)
        assert preview.value["opt_in"] is True
        assert (
            plugin._store.load_config(StudyConfig()).knowledge_contribution_opt_in
            is True
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_knowledge_contribution_opt_in_preview_failure_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, {"study": {"language": "en"}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    def _raise_preview(self, *, limit: int = 100):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(PublicGraphContributionBuilder, "preview", _raise_preview)

    try:
        result = await plugin.study_set_knowledge_contribution_opt_in(opt_in=True)
        assert isinstance(result, Err)
        assert plugin._cfg.knowledge_contribution_opt_in is False
        assert (
            plugin._store.load_config(StudyConfig()).knowledge_contribution_opt_in
            is False
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_doc_export_schema_includes_xmind_only_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
            "doc_export": {"enabled": True, "xmind_enabled": True},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        entries = plugin.collect_entries()
        export_formats = entries["study_export_notes"].meta.input_schema["properties"][
            "fmt"
        ]["enum"]
        assert export_formats == ["markdown", "pdf", "docx", "xmind"]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_restores_runtime_mode_without_overwriting_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    state = build_initial_state(mode=MODE_TEACHING)
    plugin._store.open()
    try:
        plugin._store.save_config(
            StudyConfig(mode=MODE_COMPANION, default_mode=MODE_COMPANION, language="en")
        )
        plugin._store.save_state(state)
    finally:
        plugin._store.close()

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert plugin._state.active_mode == MODE_TEACHING
        assert plugin._cfg.mode == MODE_TEACHING
        assert plugin._cfg.default_mode == MODE_COMPANION
        assert plugin._store.load_config(StudyConfig()).default_mode == MODE_COMPANION
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_failure_cleans_partial_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))

    async def _fail_persist(_self) -> None:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(StudyCompanionPlugin, "_persist_state", _fail_persist)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    assert not isinstance(result, Ok)
    assert plugin._agent is None
    assert plugin._ocr_pipeline is None
    assert plugin._store._conn is None
    assert plugin.get_static_ui_config() is None
    assert plugin.get_list_actions() == []
