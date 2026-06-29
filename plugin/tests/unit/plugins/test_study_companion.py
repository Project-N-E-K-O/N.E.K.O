from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

HOSTED_SURFACE_ACTION_IDS = [
    "study_anonymous_knowledge_preview",
    "study_checkin_manual",
    "study_checkin_status",
    "study_evaluate_answer",
    "study_explain_text",
    "study_generate_question",
    "study_goal_create",
    "study_goal_delete",
    "study_goals",
    "study_knowledge_map",
    "study_review_knowledge_candidate",
    "study_memory_create_deck",
    "study_memory_delete_deck",
    "study_memory_due_reviews",
    "study_memory_habit_status",
    "study_memory_import_words",
    "study_memory_list_decks",
    "study_memory_recitation_attempt",
    "study_memory_review_item",
    "study_memory_set_deck_goal",
    "study_pomodoro_pause",
    "study_pomodoro_resume",
    "study_pomodoro_skip_break",
    "study_pomodoro_start",
    "study_pomodoro_status",
    "study_session_summary",
    "study_set_knowledge_contribution_opt_in",
    "study_set_mode",
    "study_status",
    "study_summarize_session",
    "study_supervision_status",
    "study_supervision_toggle",
]

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.plugins import study_companion as study_companion_module
from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.awareness_buffer import ActivityBuffer
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
from plugin.plugins.study_companion.knowledge_graph_guidance import (
    build_knowledge_guidance_payload,
    match_topics,
)
from plugin.plugins.study_companion.knowledge_graph_index import (
    KnowledgeGraphIndex,
    SubgraphBudget,
    build_relevant_subgraph,
    compress_subgraph_payload,
)
from plugin.plugins.study_companion.knowledge_retrieval_eval import (
    evaluate_knowledge_retrieval_queries,
)
from plugin.plugins.study_companion.knowledge_seed_validator import (
    validate_knowledge_seed_manifest,
)
from plugin.plugins.study_companion.knowledge_contribution import (
    PublicGraphContributionBuilder,
)
from plugin.plugins.study_companion.knowledge_tracker import KnowledgeTracker
from plugin.plugins.study_companion.models import (
    AwarenessConfig,
    OcrSnapshot,
    StudyConfig,
    TutorReply,
    build_config,
    public_current_question_payload,
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
    LightweightSnapshot,
    StudyCaptureProfile,
    StudyOcrPipeline,
)
from plugin.plugins.study_companion._event_bus import StudyEvent
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
from plugin.sdk.plugin import Err, Ok, OsActivitySnapshot
from plugin.sdk.shared.constants import EVENT_META_ATTR


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.exceptions: list[tuple[tuple[object, ...], dict[str, object]]] = []

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
        self.exceptions.append((args, kwargs))
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
        self._config = dict(config)
        study_config = self._config.get("study")
        if isinstance(study_config, dict):
            self._config["study"] = {"auto_open_ui": True, **study_config}
        else:
            self._config["study"] = {"auto_open_ui": True}
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


@pytest.mark.asyncio
async def test_awareness_disabled_does_not_start_loop_on_startup(
    tmp_path: Path,
) -> None:
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": False, "awareness": {"enabled": False}},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    assert isinstance(result, Ok)
    assert plugin.is_awareness_active() is False
    assert plugin._awareness_task is None
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_auto_opens_panel_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49888")
    monkeypatch.delenv("NEKO_STUDY_COMPANION_PANEL_URL", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_MANAGER_URL", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_MANAGER_BASE_URL", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_MANAGER_PORT", raising=False)
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", opened.append)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert opened == [
            "http://127.0.0.1:49888/plugin/study_companion/ui/"
        ]
        assert plugin.get_list_actions()[0]["target"] == (
            "/plugin/study_companion/ui/"
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_auto_open_can_be_disabled_by_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49888")
    monkeypatch.delenv("NEKO_STUDY_COMPANION_DISABLE_AUTO_OPEN_UI", raising=False)
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", opened.append)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": False},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert opened == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_auto_open_uses_configured_panel_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEKO_STUDY_COMPANION_PANEL_URL", "http://127.0.0.1:48916/ui")
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", opened.append)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert opened == [
            "http://127.0.0.1:48916/plugin/study_companion/ui/"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_auto_open_can_be_disabled_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEKO_STUDY_COMPANION_DISABLE_AUTO_OPEN_UI", "true")
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", opened.append)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert opened == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_auto_open_falls_back_for_invalid_manager_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("NEKO_STUDY_COMPANION_PANEL_URL", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_MANAGER_URL", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_MANAGER_BASE_URL", raising=False)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "70000")
    monkeypatch.setenv("NEKO_PLUGIN_MANAGER_PORT", "5173")
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", opened.append)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert opened == [
            "http://127.0.0.1:48916/plugin/study_companion/ui/"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_auto_open_failure_does_not_block_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_open(_url: str) -> None:
        raise RuntimeError("browser unavailable")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", _fail_open)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    plugin.logger = ctx.logger

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert any(
            warning[0][0] == "study auto-open UI failed: {}"
            for warning in ctx.logger.warnings
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_auto_open_timeout_does_not_block_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unblock = threading.Event()
    opened: list[str] = []

    def _hang_open(url: str) -> None:
        opened.append(url)
        unblock.wait(timeout=1.0)

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(study_companion_module, "_open_url_in_browser", _hang_open)
    monkeypatch.setattr(
        study_companion_module,
        "_AUTO_OPEN_UI_TASK_TIMEOUT_SECONDS",
        0.01,
    )
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": True},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    plugin.logger = ctx.logger

    try:
        result = await plugin.startup()

        assert isinstance(result, Ok)
        assert any(
            warning[0][0] == "study auto-open UI failed: {}"
            for warning in ctx.logger.warnings
        )
    finally:
        unblock.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_start_awareness_loop_runs_async_tick_and_pushes_context(
    tmp_path: Path,
) -> None:
    class _FakeAwarenessPipeline:
        def capture_lightweight(self) -> LightweightSnapshot:
            return LightweightSnapshot(
                status="ok",
                captured_at="2026-06-01T00:00:00Z",
                window_title="Quiz - Google Chrome",
                app_type="web_page",
                activity_type="question",
                ocr_text_snippet="Question: Why?",
                has_content_change=True,
                thumbnail_phash="0" * 16,
            )

    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    plugin = StudyCompanionPlugin(ctx)
    plugin._cfg = StudyConfig(
        awareness=AwarenessConfig(
            enabled=True,
            snapshot_interval_seconds=60,
            push_to_llm_mode="read",
            os_signals_enabled=False,
        )
    )
    plugin._ocr_pipeline = _FakeAwarenessPipeline()

    plugin.start_awareness_loop()
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if any(
                message.get("source") == "awareness"
                for message in ctx.pushed_messages
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("timed out waiting for awareness push")

        assert plugin.is_awareness_active() is True
        pushed = next(
            message
            for message in ctx.pushed_messages
            if message.get("source") == "awareness"
        )
        assert pushed["ai_behavior"] == "read"
        assert "app_distribution" not in pushed["parts"][0]["text"]
    finally:
        plugin.stop_awareness_loop()
        await plugin._await_awareness_stop()

    assert plugin.is_awareness_active() is False
    assert plugin._awareness_task is None


@pytest.mark.asyncio
async def test_start_awareness_loop_primes_first_push_before_uptime_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StudyCompanionPlugin(
        _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    )
    plugin._cfg = StudyConfig(
        awareness=AwarenessConfig(
            enabled=True,
            snapshot_interval_seconds=60,
            push_to_llm_mode="read",
            os_signals_enabled=False,
        )
    )
    plugin._ocr_pipeline = object()
    monkeypatch.setattr(study_companion_module.time, "monotonic", lambda: 10.0)

    plugin.start_awareness_loop()
    try:
        assert plugin._last_awareness_push_at == pytest.approx(-290.0)
        assert plugin._should_push_context() is True
    finally:
        plugin.stop_awareness_loop()
        await plugin._await_awareness_stop()


@pytest.mark.asyncio
async def test_awareness_tick_counts_unusable_snapshot_as_idle(tmp_path: Path) -> None:
    class _NoActivitySnapshot:
        status = "ok"

        def to_activity_snapshot(self):
            return None

    class _FakeAwarenessPipeline:
        def capture_lightweight(self):
            return _NoActivitySnapshot()

    plugin = StudyCompanionPlugin(_Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}}))
    plugin._cfg = StudyConfig(
        awareness=AwarenessConfig(
            push_to_llm_mode="blind",
            os_signals_enabled=False,
        )
    )
    plugin._buffer = ActivityBuffer()
    plugin._ocr_pipeline = _FakeAwarenessPipeline()

    await plugin.awareness_tick()

    assert plugin._awareness_idle_ticks == 1


@pytest.mark.asyncio
async def test_awareness_tick_private_activity_tracker_skips_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CaptureShouldNotRun:
        def __init__(self) -> None:
            self.calls = 0

        def capture_lightweight(self):
            self.calls += 1
            raise AssertionError("private activity must not capture the screen")

    async def _activity_snapshot(source: str, *, now: float | None = None):
        assert source == "study_companion"
        assert now is not None
        return OsActivitySnapshot(
            os_signals_available=True,
            foreground_category=None,
            system_idle_seconds=12.0,
            privacy_state="private",
        )

    plugin = StudyCompanionPlugin(
        _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    )
    plugin._cfg = StudyConfig(awareness=AwarenessConfig(push_to_llm_mode="blind"))
    plugin._buffer = ActivityBuffer()
    pipeline = _CaptureShouldNotRun()
    plugin._ocr_pipeline = pipeline
    monkeypatch.setattr(
        study_companion_module,
        "get_os_activity_snapshot",
        _activity_snapshot,
    )

    await plugin.awareness_tick()

    assert pipeline.calls == 0
    assert plugin._awareness_idle_ticks == 1
    summary = await plugin._buffer.summarize()
    assert summary["current_app"] == "private"
    assert summary["current_activity"] == "private"


@pytest.mark.asyncio
async def test_awareness_tick_uses_activity_tracker_foreground_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAwarenessPipeline:
        def capture_lightweight(self) -> LightweightSnapshot:
            return LightweightSnapshot(
                status="ok",
                captured_at="2026-06-01T00:00:00Z",
                window_title="Quiz - Google Chrome",
                app_type="unknown",
                activity_type="question",
                ocr_text_snippet="Question: Why?",
                has_content_change=True,
                thumbnail_phash="1" * 16,
            )

    async def _activity_snapshot(source: str, *, now: float | None = None):
        assert source == "study_companion"
        assert now is not None
        return OsActivitySnapshot(
            os_signals_available=True,
            foreground_category="gaming",
            system_idle_seconds=42.0,
            privacy_state="visible",
        )

    class _Supervision:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def observe_activity(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {}

    plugin = StudyCompanionPlugin(
        _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    )
    plugin._cfg = StudyConfig(awareness=AwarenessConfig(push_to_llm_mode="blind"))
    plugin._buffer = ActivityBuffer()
    plugin._ocr_pipeline = _FakeAwarenessPipeline()
    monkeypatch.setattr(
        study_companion_module,
        "get_os_activity_snapshot",
        _activity_snapshot,
    )
    supervision = _Supervision()
    plugin._supervision = supervision

    await plugin.awareness_tick()

    summary = await plugin._buffer.summarize()
    assert summary["current_app"] == "game"
    assert supervision.calls == [
        {
            "ocr_text": "Question: Why?",
            "sensor_available": True,
            "idle_seconds": 42.0,
            "foreground_category": "gaming",
        }
    ]


@pytest.mark.asyncio
async def test_awareness_tick_ignores_unavailable_os_signals_for_supervision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAwarenessPipeline:
        def capture_lightweight(self) -> LightweightSnapshot:
            return LightweightSnapshot(
                status="ok",
                captured_at="2026-06-01T00:00:00Z",
                window_title="Quiz - Google Chrome",
                app_type="unknown",
                activity_type="question",
                ocr_text_snippet="Question: Why?",
                has_content_change=True,
                thumbnail_phash="2" * 16,
            )

    async def _activity_snapshot(source: str, *, now: float | None = None):
        assert source == "study_companion"
        assert now is not None
        return OsActivitySnapshot(
            os_signals_available=False,
            foreground_category=None,
            system_idle_seconds=None,
            privacy_state="unavailable",
        )

    class _Supervision:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def observe_activity(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {}

    plugin = StudyCompanionPlugin(
        _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    )
    plugin._cfg = StudyConfig(awareness=AwarenessConfig(push_to_llm_mode="blind"))
    plugin._buffer = ActivityBuffer()
    plugin._ocr_pipeline = _FakeAwarenessPipeline()
    monkeypatch.setattr(
        study_companion_module,
        "get_os_activity_snapshot",
        _activity_snapshot,
    )
    supervision = _Supervision()
    plugin._supervision = supervision

    await plugin.awareness_tick()

    summary = await plugin._buffer.summarize()
    assert summary["current_app"] == "web_page"
    assert supervision.calls == [
        {
            "ocr_text": "Question: Why?",
            "sensor_available": True,
            "idle_seconds": None,
            "foreground_category": None,
        }
    ]


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
    store._INTERACTION_TRIM_INTERVAL = 3

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


@pytest.mark.asyncio
async def test_study_settings_entry_persists_and_updates_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
            "ocr_reader": {"enabled": True, "languages": "eng"},
            "llm": {"llm_call_timeout_seconds": 45, "llm_vision_enabled": False},
            "study_companion": {"communication": {"enabled": False}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        result = await plugin.study_update_settings_config(
            config={
                "study": {"default_mode": MODE_TEACHING, "auto_open_ui": True},
                "ocr_reader": {"enabled": "false", "languages": "chi_sim+eng"},
                "llm": {
                    "llm_call_timeout_seconds": 90,
                    "llm_vision_enabled": True,
                    "llm_vision_max_image_px": "128",
                },
            }
        )

        assert isinstance(result, Ok)
        assert result.value["config"]["study"]["default_mode"] == MODE_TEACHING
        assert result.value["config"]["study"]["auto_open_ui"] is True
        assert result.value["config"]["ocr_reader"]["enabled"] is False
        assert result.value["config"]["ocr_reader"]["languages"] == "chi_sim+eng"
        assert result.value["config"]["llm"]["llm_call_timeout_seconds"] == 90.0
        assert result.value["config"]["llm"]["llm_vision_enabled"] is True
        assert result.value["config"]["llm"]["llm_vision_max_image_px"] == 128
        assert plugin._cfg.default_mode == MODE_TEACHING
        assert plugin._cfg.auto_open_ui is True
        assert plugin._cfg.ocr_enabled is False
        assert plugin._cfg.ocr_languages == "chi_sim+eng"
        assert plugin._cfg.llm_call_timeout_seconds == 90.0
        assert plugin._cfg.llm_vision_enabled is True
        assert plugin._cfg.llm_vision_max_image_px == 128
        persisted = plugin._store.load_config(StudyConfig())
        assert persisted.default_mode == MODE_TEACHING
        assert persisted.auto_open_ui is True
        assert persisted.ocr_enabled is False
        assert persisted.ocr_languages == "chi_sim+eng"
        assert persisted.llm_call_timeout_seconds == 90.0
        assert persisted.llm_vision_enabled is True
        assert persisted.llm_vision_max_image_px == 128
        assert plugin._agent._config is plugin._cfg
        assert plugin._ocr_pipeline is not None
        assert plugin._ocr_pipeline._config is plugin._cfg
    finally:
        await plugin.shutdown()


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
                "stage": "junior_high",
                "unit": "Seed Unit",
                "skills": ["seed skill"],
                "question_types": ["seed question type"],
                "examples": [{"prompt": "seed example", "answer_outline": ["seed step"]}],
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
                "stage": "college",
                "unit": "Runtime Unit",
                "skills": ["runtime skill"],
                "question_types": ["runtime question type"],
                "examples": [{"prompt": "runtime example"}],
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
        assert topic["stage"] == "junior_high"
        assert topic["unit"] == "Seed Unit"
        assert topic["skills"] == ["seed skill"]
        assert topic["question_types"] == ["seed question type"]
        assert topic["examples"] == [{"prompt": "seed example", "answer_outline": ["seed step"]}]
        assert topic["depth"] == 2
        assert topic["difficulty"] == 0.7
        assert topic["prerequisites"] == [{"id": "pre_seed", "required_mastery": 0.5}]
        assert topic["related"] == [{"id": "related_seed", "relation": "next"}]
        assert topic["typical_misconceptions"] == ["seed misconception"]
        assert topic["source"] == "seed"
    finally:
        store.close()


def test_study_store_seed_topic_upsert_refreshes_seed_metadata(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.upsert_topic(
            {
                "id": "seed_topic",
                "name": "Old Seed Topic",
                "subject": "math",
                "chapter": "Old Chapter",
                "prerequisites": [{"id": "old_pre"}],
                "related": [{"id": "old_related", "relation": "next"}],
                "source": "seed",
            }
        )
        store.upsert_topic(
            {
                "id": "seed_topic",
                "name": "New Seed Topic",
                "subject": "math",
                "chapter": "New Chapter",
                "prerequisites": [{"id": "new_pre"}],
                "related": [{"id": "new_related", "relation": "application"}],
                "source": "seed",
            }
        )

        topic = store.get_topic("seed_topic")
        assert topic is not None
        assert topic["name"] == "New Seed Topic"
        assert topic["chapter"] == "New Chapter"
        assert topic["prerequisites"] == [{"id": "new_pre"}]
        assert topic["related"] == [
            {"id": "new_related", "relation": "application"}
        ]
        assert topic["source"] == "seed"
    finally:
        store.close()


def test_study_store_knowledge_seed_inherits_top_level_stage(
    tmp_path: Path,
) -> None:
    knowledge_seed = tmp_path / "knowledge_seed.json"
    knowledge_seed.write_text(
        json.dumps(
            {
                "subject": "math",
                "grade_level": "junior_high",
                "topics": [
                    {
                        "id": "linear_equation",
                        "name": "一元一次方程",
                        "chapter": "方程与不等式",
                        "unit": "一元方程",
                        "skills": ["识别一元一次方程", "求解一元一次方程"],
                        "question_types": ["解方程", "应用题"],
                        "examples": [
                            {
                                "prompt": "解方程 2x + 3 = 9。",
                                "answer_outline": ["移项", "合并同类项", "求出 x = 3"],
                            }
                        ],
                    },
                    {
                        "id": "calculus_intro",
                        "name": "微积分入门",
                        "chapter": "高等数学",
                        "stage": "college",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = StudyStore(
        tmp_path / "study.db",
        tmp_path / "seed.json",
        _Logger(),
        knowledge_seed,
    )
    store.open()
    try:
        inherited = store.get_topic("linear_equation")
        explicit = store.get_topic("calculus_intro")

        assert inherited is not None
        assert inherited["stage"] == "junior_high"
        assert inherited["unit"] == "一元方程"
        assert inherited["skills"] == ["识别一元一次方程", "求解一元一次方程"]
        assert inherited["question_types"] == ["解方程", "应用题"]
        assert inherited["examples"] == [
            {
                "prompt": "解方程 2x + 3 = 9。",
                "answer_outline": ["移项", "合并同类项", "求出 x = 3"],
            }
        ]
        assert explicit is not None
        assert explicit["stage"] == "college"
        assert [item["id"] for item in store.list_topics(stage="junior_high")] == [
            "linear_equation"
        ]
    finally:
        store.close()


def test_study_store_knowledge_seed_repairs_empty_seed_stage(
    tmp_path: Path,
) -> None:
    knowledge_seed = tmp_path / "knowledge_seed.json"
    knowledge_seed.write_text(
        json.dumps(
            {
                "subject": "math",
                "grade_level": "junior_high",
                "topics": [
                    {
                        "id": "absolute_value",
                        "name": "绝对值",
                        "chapter": "实数与代数式",
                        "unit": "Seed Unit",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.upsert_topic(
            {
                "id": "absolute_value",
                "name": "绝对值",
                "subject": "math",
                "chapter": "实数与代数式",
                "stage": "",
                "source": "seed",
            }
        )

        assert store.get_topic("absolute_value")["stage"] == ""
        assert store.load_knowledge_seed(knowledge_seed) == 1
        repaired = store.get_topic("absolute_value")
        assert repaired["stage"] == "junior_high"
        assert repaired["unit"] == "Seed Unit"
    finally:
        store.close()


def test_study_store_knowledge_seed_manifest_loads_multiple_files(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "knowledge_seeds"
    seed_dir.mkdir()
    (seed_dir / "math.json").write_text(
        json.dumps(
            {
                "subject": "math",
                "grade_level": "junior_high",
                "topics": [
                    {
                        "id": "manifest_math_topic",
                        "name": "Manifest Math Topic",
                        "chapter": "Manifest Math",
                        "unit": "Manifest Unit",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (seed_dir / "english.json").write_text(
        json.dumps(
            {
                "subject": "english",
                "stage": "senior_high",
                "topics": [
                    {
                        "id": "manifest_english_topic",
                        "name": "Manifest English Topic",
                        "chapter": "Manifest English",
                        "unit": "Manifest Unit",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "knowledge_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "type": "knowledge_seed_manifest",
                "files": [
                    {"path": "knowledge_seeds/math.json"},
                    {"path": "knowledge_seeds/english.json"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        assert store.load_knowledge_seed(manifest_path) == 2
        assert store.get_topic("manifest_math_topic")["subject"] == "math"
        assert store.get_topic("manifest_math_topic")["stage"] == "junior_high"
        assert store.get_topic("manifest_english_topic")["subject"] == "english"
        assert store.get_topic("manifest_english_topic")["stage"] == "senior_high"
    finally:
        store.close()


def test_study_store_schema_includes_standard_knowledge_fields(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        columns = {
            str(row["name"])
            for row in store._require_conn().execute("PRAGMA table_info(topics)")
        }
        assert {"unit", "skills", "question_types", "examples"}.issubset(columns)
    finally:
        store.close()


def _read_static_knowledge_seed_payload() -> dict[str, object]:
    seed_path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    topics = payload.get("topics")
    if isinstance(topics, list):
        return payload
    files = payload.get("files")
    if not isinstance(files, list):
        return {**payload, "topics": []}
    merged_topics: list[dict[str, object]] = []
    for item in files:
        raw_path = item.get("path") if isinstance(item, dict) else item
        if not raw_path:
            continue
        child_payload = json.loads(
            (seed_path.parent / str(raw_path)).read_text(encoding="utf-8")
        )
        child_topics = child_payload.get("topics")
        if isinstance(child_topics, list):
            default_subject = str(child_payload.get("subject") or "")
            default_stage = str(
                child_payload.get("stage")
                or child_payload.get("grade_level")
                or child_payload.get("education_level")
                or child_payload.get("course_level")
                or ""
            )
            for topic in child_topics:
                if not isinstance(topic, dict):
                    continue
                merged = dict(topic)
                merged.setdefault("subject", default_subject)
                merged.setdefault("stage", default_stage)
                merged_topics.append(merged)
    return {**payload, "topics": merged_topics}


def _static_subject_component_counts() -> dict[str, int]:
    payload = _read_static_knowledge_seed_payload()
    topics = [
        topic
        for topic in payload.get("topics", [])
        if isinstance(topic, dict) and topic.get("id") and topic.get("subject")
    ]
    by_id = {str(topic["id"]): topic for topic in topics}
    by_subject: dict[str, set[str]] = {}
    adjacency: dict[str, dict[str, set[str]]] = {}
    for topic in topics:
        subject = str(topic["subject"])
        topic_id = str(topic["id"])
        by_subject.setdefault(subject, set()).add(topic_id)
        adjacency.setdefault(subject, {}).setdefault(topic_id, set())
    for topic in topics:
        source_id = str(topic["id"])
        source_subject = str(topic["subject"])
        for bucket in ("prerequisites", "related"):
            edges = topic.get(bucket)
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                target_id = str(edge.get("id") or "")
                target = by_id.get(target_id)
                if not target or str(target.get("subject")) != source_subject:
                    continue
                adjacency[source_subject].setdefault(source_id, set()).add(target_id)
                adjacency[source_subject].setdefault(target_id, set()).add(source_id)

    counts: dict[str, int] = {}
    for subject, subject_ids in by_subject.items():
        seen: set[str] = set()
        components = 0
        for topic_id in sorted(subject_ids):
            if topic_id in seen:
                continue
            components += 1
            stack = [topic_id]
            seen.add(topic_id)
            while stack:
                current = stack.pop()
                for neighbor in adjacency.get(subject, {}).get(current, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
        counts[subject] = components
    return counts


def _static_cross_subject_bridge_counts() -> dict[tuple[str, str], int]:
    payload = _read_static_knowledge_seed_payload()
    topics = [
        topic
        for topic in payload.get("topics", [])
        if isinstance(topic, dict) and topic.get("id") and topic.get("subject")
    ]
    by_id = {str(topic["id"]): topic for topic in topics}
    counts: dict[tuple[str, str], int] = {}
    for topic in topics:
        source_subject = str(topic["subject"])
        for bucket in ("prerequisites", "related"):
            edges = topic.get(bucket)
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                target = by_id.get(str(edge.get("id") or ""))
                if not target:
                    continue
                target_subject = str(target.get("subject") or "")
                if source_subject == target_subject:
                    continue
                pair = (source_subject, target_subject)
                counts[pair] = counts.get(pair, 0) + 1
    return counts


def test_study_static_knowledge_seed_contains_standard_base_library_fields() -> None:
    payload = _read_static_knowledge_seed_payload()
    topics = payload["topics"]

    topic_ids = {str(topic.get("id") or "") for topic in topics}
    stages = {
        str(topic.get("stage") or payload.get("grade_level") or "")
        for topic in topics
    }
    subjects = {
        str(topic.get("subject") or payload.get("subject") or "")
        for topic in topics
    }
    college_subject_counts = {
        subject: sum(
            1
            for topic in topics
            if topic.get("subject") == subject and topic.get("stage") == "college"
        )
        for subject in ("math", "physics", "computer_science", "economics")
    }
    curriculum_versions = {
        str(value)
        for topic in topics
        for value in (
            topic.get("curriculum_version")
            if isinstance(topic.get("curriculum_version"), list)
            else []
        )
    }
    exam_regions = {
        str(value)
        for topic in topics
        for value in (
            topic.get("exam_region") if isinstance(topic.get("exam_region"), list) else []
        )
    }
    exam_types = {
        str(value)
        for topic in topics
        for value in (
            topic.get("exam_type") if isinstance(topic.get("exam_type"), list) else []
        )
    }
    senior_subject_counts = {
        subject: sum(
            1
            for topic in topics
            if topic.get("subject") == subject and topic.get("stage") == "senior_high"
        )
        for subject in ("physics", "chemistry", "biology", "english", "chinese")
    }
    senior_core_topics = [
        topic
        for topic in topics
        if topic.get("subject") in senior_subject_counts
        and topic.get("stage") == "senior_high"
    ]
    tagged_topics = [
        topic for topic in topics if isinstance(topic.get("curriculum_tags"), list)
    ]
    rich_example_topics = [
        topic
        for topic in topics
        if any(
            isinstance(example, dict)
            and example.get("common_traps")
            and example.get("grading_points")
            for example in topic.get("examples", [])
        )
    ]
    assert len(topics) >= 750
    assert len({topic.get("chapter") for topic in topics}) >= 100
    assert len({topic.get("unit") for topic in topics}) >= 240
    assert len(tagged_topics) >= 300
    assert all(count >= 35 for count in senior_subject_counts.values())
    assert college_subject_counts["math"] >= 70
    assert college_subject_counts["physics"] >= 30
    assert college_subject_counts["computer_science"] >= 40
    assert college_subject_counts["economics"] >= 30
    assert len(rich_example_topics) >= 180
    assert all(topic in rich_example_topics for topic in senior_core_topics)
    assert all(topic.get("curriculum_version") for topic in topics)
    assert all(topic.get("exam_region") for topic in topics)
    assert all(topic.get("exam_type") for topic in topics)
    assert {
        "renjiao_high_school",
        "beishi_high_school",
        "sujiao_high_school",
        "renjiao_junior",
        "beishi_junior",
        "sujiao_junior",
        "renjiao_primary",
        "beishi_primary",
        "sujiao_primary",
    }.issubset(curriculum_versions)
    assert {
        "new_gaokao_i",
        "new_gaokao_ii",
        "national_a",
        "national_b",
        "zhongkao_beijing_style",
        "zhongkao_shanghai_style",
        "zhongkao_jiangsu_style",
    }.issubset(exam_regions)
    assert {
        "gaokao_style",
        "zhongkao_style",
        "primary_term_exam",
        "college_course_exam",
    }.issubset(exam_types)
    assert {
        "math",
        "english",
        "chinese",
        "physics",
        "chemistry",
        "biology",
        "history",
        "geography",
        "politics",
        "computer_science",
        "economics",
    }.issubset(subjects)
    assert {"primary", "junior_high", "senior_high", "college"}.issubset(stages)
    assert {
        "function_composite_application",
        "geometric_proof_strategy",
        "mathematical_modeling",
        "exam_error_checklist",
        "senior_derivative_extrema",
        "senior_derivative_constant_problem",
        "senior_conic_synthesis",
        "senior_conic_fixed_point",
        "senior_solid_vector_angle",
        "senior_new_gaokao_statistics_context",
        "college_limit_concept",
        "college_epsilon_delta_limit",
        "college_eigenvalue",
        "college_lagrange_multiplier",
        "college_confidence_interval",
        "college_hypothesis_testing",
        "college_physics_gauss_law",
        "college_physics_maxwell_equations_intro",
        "college_c_pointer_address",
        "college_ds_graph_traversal",
        "college_micro_demand_supply",
        "english_senior_continuation_writing",
        "chinese_senior_poetry_appreciation",
        "physics_senior_energy_momentum",
        "physics_senior_multistage_synthesis",
        "chem_senior_experiment_design",
        "chem_senior_industrial_process_flow",
        "bio_senior_experiment_design",
        "bio_senior_gene_engineering",
        "english_senior_cloze_logic",
        "history_senior_material_analysis",
        "geo_senior_regional_sustainability",
        "pol_senior_rule_of_law",
        "chinese_senior_new_gaokao_mixed_text",
    }.issubset(topic_ids)
    for topic in topics:
        assert topic.get("stage") or payload.get("grade_level")
        assert topic.get("subject") or payload.get("subject")
        assert topic.get("chapter")
        assert topic.get("unit")
        assert topic.get("skills")
        assert topic.get("question_types")
        assert topic.get("examples")


def test_study_knowledge_seed_validator_accepts_static_manifest() -> None:
    seed_path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )

    result = validate_knowledge_seed_manifest(seed_path)

    assert result.is_valid
    assert not result.issues
    assert len(result.topics) >= 650
    assert result.report is not None
    assert result.report["topic_count"] == len(result.topics)
    assert result.report["schema_ready_topics"] == len(result.topics)
    assert result.report["subject_schema_ready_counts"]["math"] > 0
    expected_subjects = {
        "biology",
        "chemistry",
        "chinese",
        "computer_science",
        "economics",
        "english",
        "geography",
        "history",
        "math",
        "physics",
        "politics",
    }
    assert set(result.report["subject_minimum_standards"]) == expected_subjects
    assert set(result.report["subject_minimum_standard_topic_counts"]) == expected_subjects
    assert set(result.report["subject_minimum_standard_ready_counts"]) == expected_subjects
    assert set(result.report["subject_minimum_standard_gap_counts"]) == expected_subjects
    for subject in expected_subjects:
        assert (
            result.report["subject_minimum_standard_ready_counts"][subject]
            + result.report["subject_minimum_standard_gap_counts"][subject]
            == result.report["subject_minimum_standard_topic_counts"][subject]
        )
    assert result.report["subject_minimum_standard_gap_counts"]["math"] <= 6
    assert result.report["subject_minimum_standard_ready_rates"]["math"] >= 0.98
    assert result.report["subject_minimum_standard_gap_rates"]["math"] <= 0.02
    assert set(result.report["subject_minimum_standard_relation_gap_counts"]) <= {
        "math"
    }
    assert result.report["subject_minimum_standard_uncovered_subject_counts"] == {}
    assert set(result.report["sample_subject_minimum_standard_gaps"]) <= {"math"}
    assert set(result.report["top_gap_topics_by_subject"]) <= {"math"}
    assert set(result.report["top_missing_relation_by_subject"]) <= {"math"}
    assert set(result.report["chapter_gap_counts"]) <= {"math"}
    assert set(result.report["recommended_next_batch"]) <= {"math"}
    assert len(result.report["recommended_next_batch"].get("math", [])) <= 6
    assert _static_subject_component_counts() == {
        subject: 1 for subject in expected_subjects
    }
    assert result.report["cross_subject_edge_counts"]
    assert result.report["cross_subject_relation_counts"]
    cross_subject_bridge_counts = _static_cross_subject_bridge_counts()
    assert cross_subject_bridge_counts[("history", "politics")] >= 8
    assert cross_subject_bridge_counts[("geography", "math")] >= 12
    assert result.report["legacy_edges"] == 0
    assert result.report["legacy_edge_samples"] == []
    assert result.report["missing_stage"] == 0
    assert result.report["missing_college_course_family"] == 0
    assert result.report["typed_edges"] > 0
    assert "application" in result.report["relation_counts"]


def test_study_knowledge_seed_math_relationship_density_targets() -> None:
    seed_path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )

    result = validate_knowledge_seed_manifest(seed_path)
    payload = _read_static_knowledge_seed_payload()
    topics = [
        topic
        for topic in payload.get("topics", [])
        if isinstance(topic, dict) and topic.get("id")
    ]
    by_id = {str(topic["id"]): topic for topic in topics}
    math_layer_counts = {
        str(topic["id"]): {"core": 0, "diagnostic": 0, "transfer": 0}
        for topic in topics
        if topic.get("subject") == "math"
    }
    for topic in topics:
        source_id = str(topic["id"])
        for bucket in ("prerequisites", "related"):
            edges = topic.get(bucket)
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                relation = str(
                    edge.get("relation")
                    or ("prerequisite" if bucket == "prerequisites" else "related")
                )
                target_id = str(edge.get("id") or "")
                for topic_id in (source_id, target_id):
                    if topic_id not in math_layer_counts or topic_id not in by_id:
                        continue
                    if relation in {"prerequisite", "procedure_step"}:
                        math_layer_counts[topic_id]["core"] += 1
                    if relation == "confusable":
                        math_layer_counts[topic_id]["diagnostic"] += 1
                    if relation in {"application", "co_occurs", "extends"}:
                        math_layer_counts[topic_id]["transfer"] += 1

    assert result.is_valid
    assert result.report is not None
    relation_counts = result.report["relation_counts"]
    assert result.report["typed_edges"] >= 1100
    assert result.report["legacy_edges"] == 0
    assert result.report["cycles_in_prerequisites"] == 0
    assert (
        result.report["subject_minimum_standard_ready_counts"]["math"]
        + result.report["subject_minimum_standard_gap_counts"]["math"]
        == result.report["subject_minimum_standard_topic_counts"]["math"]
    )
    assert result.report["subject_minimum_standard_gap_counts"]["math"] <= 6
    assert relation_counts.get("nearby", 0) == 0
    assert relation_counts["next"] < 30
    assert relation_counts["confusable"] >= 60
    assert relation_counts["procedure_step"] >= 90
    assert relation_counts["application"] >= 130
    assert math_layer_counts
    assert all(counts["core"] > 0 for counts in math_layer_counts.values())
    assert sum(
        counts["diagnostic"] == 0 for counts in math_layer_counts.values()
    ) <= 4
    assert sum(counts["transfer"] == 0 for counts in math_layer_counts.values()) <= 1


def test_study_knowledge_seed_validator_reports_schema_errors(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "knowledge_seeds"
    seed_dir.mkdir()
    (tmp_path / "knowledge_seed_taxonomy.json").write_text(
        json.dumps(
            {
                "curriculum_version": {"generic_cn_high_school": {}},
                "exam_region": {"new_gaokao_i": {}},
                "exam_type": {"gaokao_style": {}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (seed_dir / "math.json").write_text(
        json.dumps(
            {
                "subject": "math",
                "grade_level": "senior_high",
                "topics": [
                    {
                        "id": "duplicate_topic",
                        "name": "Bad Topic",
                        "chapter": "Bad Chapter",
                        "unit": "Bad Unit",
                        "prerequisites": [],
                        "related": [{"id": "missing_topic"}],
                        "skills": [],
                        "question_types": ["综合题"],
                        "examples": [{"prompt": "", "answer_outline": []}],
                        "typical_misconceptions": ["忽略条件"],
                        "exam_region": "",
                    },
                    {
                        "id": "duplicate_topic",
                        "name": "Duplicate Topic",
                        "chapter": "Bad Chapter",
                        "unit": "Bad Unit",
                        "prerequisites": [],
                        "related": [
                            {"id": "duplicate_topic", "relation": "application"},
                            {
                                "id": "duplicate_topic",
                                "relation": "mystery",
                                "reason": "bad relation",
                            },
                            {
                                "id": "duplicate_topic",
                                "relation": "application",
                                "reason": "bad use case",
                                "use_cases": ["unsupported_case"],
                            },
                            {
                                "id": "duplicate_topic",
                                "relation": "application",
                                "reason": "bad edge metadata",
                                "priority": "urgent",
                                "context": "quiz",
                                "confidence": 1.5,
                            },
                        ],
                        "skills": ["识别重复知识点"],
                        "question_types": ["综合题"],
                        "examples": [
                            {
                                "prompt": "指出该知识点和前一条为什么冲突。",
                                "answer_outline": ["比较 id。"],
                            }
                        ],
                        "typical_misconceptions": ["把同名条目当成不同知识点"],
                        "curriculum_version": ["unknown_curriculum"],
                        "exam_region": ["new_gaokao_i"],
                        "exam_type": ["gaokao_style"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "knowledge_graph_seed.json"
    manifest_path.write_text(
        json.dumps(
            {
                "type": "knowledge_seed_manifest",
                "files": [
                    {"path": "knowledge_seeds/math.json", "topic_count": 99},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(manifest_path)

    issue_codes = {issue.code for issue in result.issues}
    assert not result.is_valid
    assert {
        "duplicate_topic_id",
        "invalid_example",
        "missing_reference",
        "empty_reserved_field",
        "manifest_topic_count_mismatch",
        "unknown_reserved_field_value",
        "invalid_typed_edge",
        "unknown_edge_relation",
        "unknown_edge_use_case",
        "invalid_edge_priority",
        "invalid_edge_context",
        "invalid_edge_confidence",
    }.issubset(issue_codes)


def test_study_knowledge_seed_validator_reports_graph_quality_gaps(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.json"
    manifest = tmp_path / "knowledge_graph_seed.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "seed.json", "topic_count": 2}]}),
        encoding="utf-8",
    )
    common = {
        "subject": "math",
        "stage": "college",
        "chapter": "Calculus",
        "unit": "Derivatives",
        "curriculum_version": "generic_college",
        "exam_region": "college_course_generic",
        "exam_type": "college_course_exam",
        "skills": ["analyze prerequisite graph"],
        "question_types": ["concept"],
        "examples": [{"prompt": "Explain the dependency.", "answer_outline": ["A"]}],
        "typical_misconceptions": ["cyclic dependencies hide true prerequisites"],
        "related": [],
    }
    seed.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        **common,
                        "id": "college_cycle_a",
                        "name": "Cycle A",
                        "prerequisites": [
                            {
                                "id": "college_cycle_b",
                                "relation": "prerequisite",
                                "reason": "A points to B.",
                            }
                        ],
                    },
                    {
                        **common,
                        "id": "college_cycle_b",
                        "name": "Cycle B",
                        "prerequisites": [
                            {
                                "id": "college_cycle_a",
                                "relation": "prerequisite",
                                "reason": "B points back to A.",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(manifest)

    issue_codes = {issue.code for issue in result.issues}
    assert "prerequisite_cycle" in issue_codes
    assert "subject_minimum_standard_gap" not in issue_codes
    assert result.report is not None
    assert result.report["cycles_in_prerequisites"] == 2
    assert result.report["missing_college_course_family"] == 2
    assert result.report["subject_minimum_standard_topic_counts"]["math"] == 2
    assert result.report["subject_minimum_standard_ready_counts"]["math"] == 0
    assert result.report["subject_minimum_standard_gap_counts"]["math"] == 2
    assert result.report["subject_minimum_standard_gap_rates"]["math"] == 1.0
    assert result.report["subject_minimum_standard_relation_gap_counts"]["math"] == {
        "application": 2,
        "confusable": 2,
        "procedure_step": 2,
    }
    assert result.report["sample_subject_minimum_standard_gaps"]["math"] == [
        {
            "id": "college_cycle_a",
            "missing_relations": ["application", "confusable", "procedure_step"],
            "missing_fields": [],
        },
        {
            "id": "college_cycle_b",
            "missing_relations": ["application", "confusable", "procedure_step"],
            "missing_fields": [],
        },
    ]
    assert set(result.report["sample_prerequisite_cycle_nodes"]) == {
        "college_cycle_a",
        "college_cycle_b",
    }


def test_study_knowledge_seed_validator_reports_subject_minimum_standard_summary(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.json"
    manifest = tmp_path / "knowledge_graph_seed.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "seed.json", "topic_count": 3}]}),
        encoding="utf-8",
    )
    common = {
        "subject": "math",
        "stage": "college",
        "chapter": "Calculus",
        "unit": "Derivatives",
        "course_family": "calculus",
        "curriculum_version": "generic_college",
        "exam_region": "college_course_generic",
        "exam_type": "college_course_exam",
        "skills": ["analyze relation coverage"],
        "question_types": ["concept"],
        "examples": [{"prompt": "Explain the relation.", "answer_outline": ["A"]}],
        "typical_misconceptions": ["missing graph edges hide learning gaps"],
        "prerequisites": [],
    }
    seed.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        **common,
                        "id": "math_ready",
                        "name": "Ready",
                        "prerequisites": [
                            {
                                "id": "math_proc",
                                "relation": "prerequisite",
                                "reason": "required foundation",
                            }
                        ],
                        "related": [
                            {
                                "id": "math_proc",
                                "relation": "procedure_step",
                                "reason": "next step",
                            },
                            {
                                "id": "math_confusion",
                                "relation": "confusable",
                                "reason": "compare concepts",
                            },
                            {
                                "id": "math_confusion",
                                "relation": "application",
                                "reason": "apply the concept",
                            },
                        ],
                    },
                    {
                        **common,
                        "id": "math_proc",
                        "name": "Procedure Only",
                        "related": [
                            {
                                "id": "math_ready",
                                "relation": "procedure_step",
                                "reason": "next step",
                            }
                        ],
                    },
                    {
                        **common,
                        "id": "math_confusion",
                        "name": "Confusion Target",
                        "related": [
                            {
                                "id": "math_ready",
                                "relation": "confusable",
                                "reason": "compare concepts",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(manifest)

    assert result.is_valid
    assert result.report is not None
    assert result.report["subject_minimum_standard_topic_counts"]["math"] == 3
    assert result.report["subject_minimum_standard_ready_counts"]["math"] == 1
    assert result.report["subject_minimum_standard_gap_counts"]["math"] == 2
    assert result.report["subject_minimum_standard_ready_rates"]["math"] == pytest.approx(
        1 / 3
    )
    assert result.report["subject_minimum_standard_relation_gap_counts"]["math"] == {
        "application": 2,
        "confusable": 1,
        "prerequisite": 2,
        "procedure_step": 1,
    }
    assert result.report["top_missing_relation_by_subject"]["math"] == [
        {"relation": "application", "count": 2},
        {"relation": "prerequisite", "count": 2},
        {"relation": "confusable", "count": 1},
        {"relation": "procedure_step", "count": 1},
    ]
    assert result.report["chapter_ready_counts"]["math"] == {"Calculus": 1}
    assert result.report["chapter_gap_counts"]["math"] == {"Calculus": 2}
    assert result.report["recommended_next_batch"]["math"] == [
        "math_confusion",
        "math_proc",
    ]
    assert result.report["top_gap_topics_by_subject"]["math"] == [
        {
            "id": "math_confusion",
            "chapter": "Calculus",
            "unit": "Derivatives",
            "missing_relations": [
                "application",
                "prerequisite",
                "procedure_step",
            ],
            "missing_fields": [],
            "missing_count": 3,
            "edge_count": 1,
        },
        {
            "id": "math_proc",
            "chapter": "Calculus",
            "unit": "Derivatives",
            "missing_relations": ["application", "confusable", "prerequisite"],
            "missing_fields": [],
            "missing_count": 3,
            "edge_count": 1,
        },
    ]


def test_study_knowledge_seed_validator_reports_subject_minimum_field_gaps(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.json"
    manifest = tmp_path / "knowledge_graph_seed.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "seed.json", "topic_count": 2}]}),
        encoding="utf-8",
    )
    common = {
        "subject": "english",
        "stage": "senior_high",
        "chapter": "Reading",
        "unit": "Main idea",
        "curriculum_version": "generic_cn_high_school",
        "exam_region": "new_gaokao_i",
        "exam_type": "gaokao_style",
        "skills": ["read a paragraph"],
        "examples": [{"prompt": "Find the main idea.", "answer_outline": ["A"]}],
        "typical_misconceptions": ["confusing detail with main idea"],
        "prerequisites": [],
    }
    seed.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        **common,
                        "id": "english_missing_field",
                        "name": "Missing Field",
                        "question_types": [],
                        "related": [
                            {
                                "id": "english_ready",
                                "relation": "procedure_step",
                                "reason": "find the next step",
                            }
                        ],
                    },
                    {
                        **common,
                        "id": "english_ready",
                        "name": "Ready",
                        "question_types": ["main idea"],
                        "related": [
                            {
                                "id": "english_missing_field",
                                "relation": "procedure_step",
                                "reason": "find the next step",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(manifest)

    issue_codes = {issue.code for issue in result.issues}
    assert "subject_minimum_standard_gap" not in issue_codes
    assert result.report is not None
    assert result.report["subject_minimum_standard_topic_counts"]["english"] == 2
    assert result.report["subject_minimum_standard_ready_counts"]["english"] == 1
    assert result.report["subject_minimum_standard_field_gap_counts"]["english"] == {
        "question_types": 1
    }


def test_study_knowledge_seed_validator_reports_uncovered_subjects(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.json"
    manifest = tmp_path / "knowledge_graph_seed.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "seed.json", "topic_count": 1}]}),
        encoding="utf-8",
    )
    seed.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "id": "art_color_theory",
                        "name": "Color Theory",
                        "subject": "art",
                        "stage": "college",
                        "chapter": "Design",
                        "unit": "Color",
                        "course_family": "visual_arts",
                        "curriculum_version": "generic_college",
                        "exam_region": "college_course_generic",
                        "exam_type": "college_course_exam",
                        "skills": ["compare colors"],
                        "question_types": ["concept"],
                        "examples": [
                            {
                                "prompt": "Compare warm and cool colors.",
                                "answer_outline": ["A"],
                            }
                        ],
                        "typical_misconceptions": ["treating hue and value as the same"],
                        "prerequisites": [],
                        "related": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(manifest)

    assert result.is_valid
    assert result.report is not None
    assert result.report["subject_counts"]["art"] == 1
    assert result.report["subject_minimum_standard_uncovered_subject_counts"] == {
        "art": 1
    }


def test_study_store_enforces_sqlite_foreign_keys(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        row = store._require_conn().execute("PRAGMA foreign_keys").fetchone()
        assert row is not None
        assert int(row[0]) == 1
    finally:
        store.close()


def test_study_store_enables_wal_and_read_connection(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        write_mode = store._require_conn().execute("PRAGMA journal_mode").fetchone()
        read_mode = store._require_read_conn().execute("PRAGMA journal_mode").fetchone()

        assert write_mode is not None
        assert read_mode is not None
        assert str(write_mode[0]).lower() == "wal"
        assert str(read_mode[0]).lower() == "wal"
    finally:
        store.close()


def test_study_store_open_falls_back_when_wal_pragma_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    created: list[_ProxyConnection] = []

    class _ProxyConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            object.__setattr__(self, "_conn", conn)
            object.__setattr__(self, "wal_failures", 0)
            object.__setattr__(self, "closed", False)

        def execute(self, sql, *args, **kwargs):  # noqa: ANN001
            if str(sql).strip().upper().startswith("PRAGMA JOURNAL_MODE=WAL"):
                object.__setattr__(self, "wal_failures", self.wal_failures + 1)
                raise sqlite3.OperationalError("wal denied")
            return self._conn.execute(sql, *args, **kwargs)

        def close(self) -> None:
            object.__setattr__(self, "closed", True)
            self._conn.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name.startswith("_") or name in {"wal_failures", "closed"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    def connect_spy(*args, **kwargs):  # noqa: ANN001
        proxy = _ProxyConnection(real_connect(*args, **kwargs))
        created.append(proxy)
        return proxy

    logger = _Logger()
    monkeypatch.setattr(sqlite3, "connect", connect_spy)
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", logger)
    store.open()
    try:
        assert created[0].wal_failures == 1
        assert logger.warnings
        assert "falling back" in str(logger.warnings[-1][0][0])
    finally:
        store.close()


def test_study_store_open_closes_connection_when_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    created: list[_CloseTrackingConnection] = []

    class _CloseTrackingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            object.__setattr__(self, "_conn", conn)
            object.__setattr__(self, "closed", False)

        def close(self) -> None:
            object.__setattr__(self, "closed", True)
            self._conn.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name.startswith("_") or name == "closed":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    def connect_spy(*args, **kwargs):  # noqa: ANN001
        proxy = _CloseTrackingConnection(real_connect(*args, **kwargs))
        created.append(proxy)
        return proxy

    def fail_init(self):  # noqa: ANN001
        raise RuntimeError("init failed")

    monkeypatch.setattr(sqlite3, "connect", connect_spy)
    monkeypatch.setattr(StudyStore, "_init_db", fail_init)
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())

    with pytest.raises(RuntimeError, match="init failed"):
        store.open()

    assert created
    assert created[0].closed is True
    assert store._conn is None


def test_study_store_read_connection_requests_wal_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    statements: list[str] = []

    class _StatementProxy:
        def __init__(self, conn: sqlite3.Connection) -> None:
            object.__setattr__(self, "_conn", conn)

        def execute(self, sql, *args, **kwargs):  # noqa: ANN001
            statements.append(str(sql))
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()

    def connect_spy(*args, **kwargs):  # noqa: ANN001
        return _StatementProxy(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect_spy)
    try:
        store._require_read_conn()

        assert any(
            statement.strip().upper().startswith("PRAGMA JOURNAL_MODE=WAL")
            for statement in statements
        )
    finally:
        store.close()


def test_study_store_journal_config_falls_back_when_wal_returns_non_wal(
    tmp_path: Path,
) -> None:
    class _Cursor:
        def __init__(self, value: str) -> None:
            self.value = value

        def fetchone(self) -> tuple[str]:
            return (self.value,)

    class _Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str) -> _Cursor:
            self.statements.append(sql)
            if sql == "PRAGMA journal_mode=WAL":
                return _Cursor("delete")
            if sql == "PRAGMA journal_mode=DELETE":
                return _Cursor("delete")
            return _Cursor("")

    conn = _Connection()
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())

    mode = store._configure_connection_journal(conn, role="read")  # type: ignore[arg-type]

    assert mode == "delete"
    assert conn.statements == ["PRAGMA journal_mode=WAL", "PRAGMA journal_mode=DELETE"]


def test_study_store_serializes_fallback_reads_when_wal_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def configure_journal(self, conn, *, role: str):  # noqa: ANN001
        del self, conn
        return "delete" if role == "write" else "wal"

    monkeypatch.setattr(StudyStore, "_configure_connection_journal", configure_journal)
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()

    def unexpected_read_connect(*_args, **_kwargs):
        raise AssertionError("read connection should be disabled without WAL")

    monkeypatch.setattr(sqlite3, "connect", unexpected_read_connect)
    try:
        class _TrackingRLock:
            def __init__(self) -> None:
                self._lock = threading.RLock()
                self.depth = 0

            def acquire(self, *args, **kwargs) -> bool:  # noqa: ANN002, ANN003
                acquired = self._lock.acquire(*args, **kwargs)
                if acquired:
                    self.depth += 1
                return acquired

            def release(self) -> None:
                self.depth -= 1
                self._lock.release()

            def __enter__(self) -> "_TrackingRLock":
                self.acquire()
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                self.release()
                return False

            @property
            def held(self) -> bool:
                return self.depth > 0

        lock = _TrackingRLock()
        store._lock = lock  # type: ignore[assignment]
        observed: list[bool] = []

        def lock_held() -> int:
            observed.append(lock.held)
            return int(lock.held)

        store._require_conn().create_function("lock_held", 0, lock_held)

        row = (
            store._require_read_conn()
            .execute("SELECT lock_held() AS held")
            .fetchone()
        )

        assert row is not None
        assert int(row["held"]) == 1
        assert observed == [True]
        assert lock.held is False
    finally:
        store.close()


def test_study_store_uses_thread_local_read_connections(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        main_conn = store._require_read_conn()
        barrier = threading.Barrier(3)

        def read_conn_ids() -> tuple[int, int]:
            barrier.wait(timeout=1.0)
            first = store._require_read_conn()
            second = store._require_read_conn()
            return id(first), id(second)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(read_conn_ids) for _ in range(2)]
            barrier.wait(timeout=1.0)
            thread_results = [future.result(timeout=1.0) for future in futures]

        assert id(main_conn) not in {item[0] for item in thread_results}
        assert all(first == second for first, second in thread_results)
        assert len({item[0] for item in thread_results}) == 2
    finally:
        store.close()


def test_study_store_append_interaction_trims_on_interval(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store._INTERACTION_TRIM_INTERVAL = 3
        for index in range(2):
            store.append_interaction(
                kind="concept_explain",
                input_text=f"before-{index}",
                output_text="ok",
                history_limit=1,
            )

        assert len(store.list_interactions(limit=10)) == 2

        store.append_interaction(
            kind="concept_explain",
            input_text="trim",
            output_text="ok",
            history_limit=1,
        )

        remaining = store.list_interactions(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["input_text"] == "trim"
    finally:
        store.close()


def test_study_store_open_resets_interaction_trim_counter(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    store._interaction_count = 99
    store.close()

    store.open()
    try:
        assert store._interaction_count == 0
    finally:
        store.close()


def test_study_store_batch_write_answer_data_logs_rollback_context(
    tmp_path: Path,
) -> None:
    logger = _Logger()
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", logger)
    store.open()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.batch_write_answer_data(
                session_id="rollback-session",
                mode="teaching",
                topic_id="missing-topic",
                question={"question": "q"},
                user_answer="a",
                eval_result={"verdict": "correct"},
                response_time_ms=None,
                fsrs_card={"topic_id": "missing-topic", "state": "new"},
                fsrs_rating=3,
            )

        assert logger.exceptions
        assert "batch_write_answer_data failed" in str(logger.exceptions[-1][0][0])
        assert "rollback-session" in str(logger.exceptions[-1][0])
        assert "missing-topic" in str(logger.exceptions[-1][0])
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

    edge = next(
        edge
        for edge in payload["edges"]
        if edge["from"] == "quadratic_vertex_form" and edge["to"] == "linear_function_kb"
    )
    assert edge["relation"] == "compare"
    assert edge["priority"] == "optional"
    assert edge["context"] == "explanation"
    assert edge["confidence"] == 0.65
    assert not any(str(edge["to"]).startswith("{") for edge in payload["edges"])


def test_knowledge_map_keeps_subject_units_and_empty_misconceptions_distinct() -> None:
    payload = build_knowledge_map_payload(
        topics=[
            {
                "id": "math_unit",
                "name": "Math Unit",
                "subject": "math",
                "unit": "综合应用",
                "typical_misconceptions": [],
                "misconceptions": ["legacy value"],
            },
            {
                "id": "physics_unit",
                "name": "Physics Unit",
                "subject": "physics",
                "unit": "综合应用",
            },
        ]
    )

    assert payload["summary"]["unit_counts"] == {
        "math:综合应用": 1,
        "physics:综合应用": 1,
    }
    assert payload["nodes"][0]["typical_misconceptions"] == []


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

    auto_open = build_config({"study": {"auto_open_ui": True}})
    assert auto_open.auto_open_ui is True
    assert build_config({"auto_open_ui": True}).auto_open_ui is True
    assert StudyConfig(auto_open_ui="yes").auto_open_ui is True

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


@pytest.mark.asyncio
async def test_study_review_knowledge_candidate_approves_topic_into_base_library(
    tmp_path: Path,
) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._store = store
    plugin._knowledge_tracker = KnowledgeTracker(store)

    try:
        candidate = store.upsert_candidate_item(
            item_type=KnowledgeCandidateType.TOPIC.value,
            payload={
                "id": "candidate_linear_equation",
                "name": "Linear equation candidate",
                "subject": "math",
                "chapter": "Equations",
                "unit": "One-variable equations",
                "stage": "junior_high",
                "skills": ["solve one-variable linear equations"],
                "question_types": ["calculation"],
                "examples": [{"prompt": "Solve 2x + 3 = 9."}],
            },
            source="unit-test",
            dedupe_key="math:linear equation candidate",
        )

        result = await plugin.study_review_knowledge_candidate(
            candidate_id=candidate["id"], decision="approve"
        )

        assert isinstance(result, Ok)
        assert result.value["decision"] == "approve"
        assert result.value["topic"]["id"] == "candidate_linear_equation"
        assert result.value["topic"]["source"] == "runtime"
        assert result.value["candidate"]["status"] == KnowledgeCandidateStatus.TRUSTED.value
        topic = store.get_topic("candidate_linear_equation")
        assert topic is not None
        assert topic["unit"] == "One-variable equations"
        assert topic["skills"] == ["solve one-variable linear equations"]
        assert topic["question_types"] == ["calculation"]
        assert topic["examples"] == [{"prompt": "Solve 2x + 3 = 9."}]
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
                "stage": "junior_high",
                "unit": "Number system",
                "skills": ["read coordinates", "compare signed numbers"],
                "question_types": ["concept", "calculation"],
                "examples": [{"prompt": "Place -2 on the number line."}],
                "typical_misconceptions": ["Confusing position with distance."],
                "prerequisites": [
                    {
                        "id": "real_number_concept",
                        "required_mastery": 0.55,
                        "reason": "Number-axis work depends on real-number meaning.",
                        "use_cases": ["learning_path"],
                    }
                ],
                "related": [
                    {
                        "id": "absolute_value",
                        "relation": "application",
                        "reason": "Absolute value is interpreted as distance on the number axis.",
                        "use_cases": ["diagnosis", "hint_generation"],
                    }
                ],
            },
            {"id": "real_number_concept", "name": "Real Numbers"},
            {"id": "absolute_value", "name": "Absolute Value"},
        ]
    )

    prereq_edge = next(
        edge
        for edge in payload["edges"]
        if edge["from"] == "real_number_concept" and edge["to"] == "number_axis"
    )
    assert prereq_edge["relation"] == "prerequisite"
    assert prereq_edge["required_mastery"] == 0.55
    assert prereq_edge["reason"] == "Number-axis work depends on real-number meaning."
    assert prereq_edge["use_cases"] == ["learning_path"]
    assert prereq_edge["priority"] == "core"
    assert prereq_edge["context"] == "diagnosis"
    assert prereq_edge["confidence"] == 0.9
    application_edge = next(
        edge
        for edge in payload["edges"]
        if edge["from"] == "number_axis" and edge["to"] == "absolute_value"
    )
    assert application_edge["relation"] == "application"
    assert application_edge["reason"] == "Absolute value is interpreted as distance on the number axis."
    assert application_edge["use_cases"] == ["diagnosis", "hint_generation"]
    assert application_edge["priority"] == "useful"
    assert application_edge["context"] == "practice"
    assert application_edge["confidence"] == 0.9
    assert payload["nodes"][0]["stage"] == "junior_high"
    assert payload["nodes"][0]["unit"] == "Number system"
    assert payload["nodes"][0]["skills"] == ["read coordinates", "compare signed numbers"]
    assert payload["nodes"][0]["question_types"] == ["concept", "calculation"]
    assert payload["nodes"][0]["examples"] == [{"prompt": "Place -2 on the number line."}]
    assert payload["nodes"][0]["typical_misconceptions"] == ["Confusing position with distance."]
    assert payload["summary"]["stage_counts"]["junior_high"] == 1
    assert all(not edge["from"].startswith("{") for edge in payload["edges"])


def test_study_knowledge_guidance_payload_returns_path_applications_and_confusions() -> None:
    topics = [
        {
            "id": "college_derivative",
            "name": "导数",
            "stage": "college",
            "subject": "math",
            "related": [
                {
                    "id": "college_extreme_points",
                    "relation": "application",
                    "reason": "求极值点通常要先求导。",
                    "use_cases": ["diagnosis", "learning_path"],
                }
            ],
        },
        {
            "id": "college_monotonicity",
            "name": "函数单调性",
            "stage": "college",
            "subject": "math",
            "related": [
                {
                    "id": "college_extreme_points",
                    "relation": "procedure_step",
                    "reason": "判断临界点两侧单调性是确认极值的关键步骤。",
                }
            ],
        },
        {
            "id": "college_sign_chart",
            "name": "导数符号表",
            "stage": "college",
            "subject": "math",
            "related": [
                {
                    "id": "college_extreme_points",
                    "relation": "procedure_step",
                    "reason": "符号表能定位临界点两侧单调性变化。",
                }
            ],
        },
        {
            "id": "college_stationary_point",
            "name": "驻点",
            "stage": "college",
            "subject": "math",
        },
        {
            "id": "college_optimization",
            "name": "优化应用题",
            "stage": "college",
            "subject": "math",
        },
        {
            "id": "college_advanced_extrema",
            "name": "含参极值问题",
            "stage": "college",
            "subject": "math",
        },
        {
            "id": "college_graph_analysis",
            "name": "函数图像分析",
            "stage": "college",
            "subject": "math",
        },
        {
            "id": "college_extreme_points",
            "name": "求极值点",
            "stage": "college",
            "subject": "math",
            "typical_misconceptions": ["导数为 0 不一定是极值点。"],
            "prerequisites": [
                {
                    "id": "college_derivative",
                    "relation": "prerequisite",
                    "reason": "求极值点前要能计算导数。",
                },
                {
                    "id": "college_monotonicity",
                    "relation": "prerequisite",
                    "reason": "需要看临界点两侧单调性是否变化。",
                },
            ],
            "related": [
                {
                    "id": "college_optimization",
                    "relation": "application",
                    "reason": "优化题常把实际目标转化为极值问题。",
                },
                {
                    "id": "college_advanced_extrema",
                    "relation": "extends",
                    "reason": "含参极值问题是在基本极值判断上的进阶扩展。",
                },
                {
                    "id": "college_graph_analysis",
                    "relation": "co_occurs",
                    "reason": "图像分析常和单调性、极值、凹凸性一起出现。",
                },
                {
                    "id": "college_stationary_point",
                    "relation": "confusable",
                    "reason": "驻点不一定是极值点，还要检查单调性变化。",
                },
            ],
        },
    ]

    payload = build_knowledge_guidance_payload(
        topics=topics,
        query="我不会求极值点",
    )

    assert payload["topic"]["id"] == "college_extreme_points"
    assert {edge["from"] for edge in payload["learning_path"]} >= {
        "college_derivative",
        "college_monotonicity",
        "college_sign_chart",
    }
    assert payload["applications"][0]["to"] == "college_optimization"
    assert payload["confusions"][0]["to"] == "college_stationary_point"
    assert "导数为 0 不一定是极值点。" in payload["topic"]["typical_misconceptions"]
    assert {
        question["kind"] for question in payload["diagnosis_questions"]
    } >= {
        "prerequisite_probe",
        "procedure_probe",
        "confusion_check",
        "application_practice",
        "extension_suggestion",
        "related_review",
    }
    assert payload["summary"]["matched"] is True
    assert payload["summary"]["diagnosis_question_count"] >= 6


def test_study_knowledge_guidance_treats_confusions_as_bidirectional() -> None:
    topics = [
        {
            "id": "stationary_point",
            "name": "Stationary point",
            "subject": "math",
            "stage": "college",
            "related": [
                {
                    "id": "extreme_point",
                    "relation": "confusable",
                    "reason": "A stationary point is not always an extreme point.",
                }
            ],
        },
        {
            "id": "extreme_point",
            "name": "Extreme point",
            "subject": "math",
            "stage": "college",
        },
    ]

    payload = build_knowledge_guidance_payload(
        topics=topics,
        topic_id="extreme_point",
    )

    assert payload["topic"]["id"] == "extreme_point"
    assert payload["confusions"][0]["from"] == "stationary_point"
    assert payload["confusions"][0]["to"] == "extreme_point"
    assert payload["summary"]["confusion_count"] == 1


def test_study_knowledge_guidance_aliases_rank_natural_query_matches() -> None:
    topics = [
        {
            "id": "senior_derivative_extrema",
            "name": "导数与极值最值",
            "subject": "math",
            "stage": "senior_high",
            "typical_misconceptions": ["只令 f'(x)=0 而忘记检查端点和极值点"],
        },
        {
            "id": "college_monotonic_extrema",
            "name": "单调性极值与凹凸性",
            "subject": "math",
            "stage": "college",
            "course_family": "calculus",
            "aliases": ["求极值点", "极值点判断", "驻点与极值点"],
        },
    ]

    matches = match_topics(topics, query="我不会求极值点", limit=2)

    assert matches[0]["id"] == "college_monotonic_extrema"
    assert "求极值点" in matches[0]["matched_terms"]


def test_study_knowledge_guidance_prefers_specific_topic_over_learning_intent() -> None:
    topics = [
        {
            "id": "college_definite_integral",
            "name": "\u5b9a\u79ef\u5206",
            "subject": "math",
            "stage": "college",
            "course_family": "calculus",
            "aliases": ["\u5b9a\u79ef\u5206\u600e\u4e48\u5b66"],
        },
        {
            "id": "college_ds_linked_list",
            "name": "\u5355\u94fe\u8868\u64cd\u4f5c",
            "subject": "computer_science",
            "stage": "college",
            "course_family": "data_structures",
        },
    ]

    matches = match_topics(topics, query="\u94fe\u8868\u600e\u4e48\u5b66", limit=2)

    assert matches[0]["id"] == "college_ds_linked_list"


def test_study_knowledge_guidance_acceptance_queries_hit_relation_groups() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]
    cases = [
        (
            "\u6781\u9650\u548c\u51fd\u6570\u503c\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_limit_concept", "college_function_value"},
        ),
        (
            "\u8fde\u7eed\u4e3a\u4ec0\u4e48\u4e0d\u4e00\u5b9a\u53ef\u5bfc\uff1f",
            {"college_continuity", "college_derivative_existence"},
        ),
        (
            "\u5bfc\u6570\u600e\u4e48\u7528\u6765\u6c42\u6781\u503c\uff1f",
            {
                "senior_derivative_extrema",
                "college_monotonic_extrema",
                "college_stationary_points",
                "college_extreme_value_points",
            },
        ),
        (
            "\u6781\u503c\u548c\u6700\u503c\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_extreme_value_points", "college_absolute_extrema"},
        ),
        (
            "\u5b9a\u79ef\u5206\u9762\u79ef\u9898\u600e\u4e48\u505a\uff1f",
            {"college_integral_area_problem", "college_definite_integral", "college_integral_application"},
        ),
        (
            "\u7279\u5f81\u503c\u600e\u4e48\u6c42\uff1f",
            {
                "college_eigenvalue",
                "college_characteristic_matrix",
                "college_characteristic_polynomial",
            },
        ),
        (
            "\u7279\u5f81\u503c\u548c\u5947\u5f02\u503c\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_eigenvalue", "college_singular_value"},
        ),
        (
            "\u79e9\u548c\u7ef4\u6570\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_rank", "college_basis_dimension", "college_matrix_dimension"},
        ),
        (
            "\u6392\u5217\u548c\u7ec4\u5408\u600e\u4e48\u533a\u5206\uff1f",
            {"senior_probability_counting", "college_permutation", "college_combination"},
        ),
        (
            "\u4e8c\u9879\u5206\u5e03\u548c\u6b63\u6001\u5206\u5e03\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_binomial_distribution", "college_normal_distribution"},
        ),
        (
            "\u7f6e\u4fe1\u533a\u95f4\u548c\u5047\u8bbe\u68c0\u9a8c\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_confidence_interval", "college_hypothesis_testing"},
        ),
        (
            "\u4e0d\u5b9a\u79ef\u5206\u548c\u5b9a\u79ef\u5206\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_indefinite_integral", "college_definite_integral"},
        ),
        (
            "\u539f\u51fd\u6570\u548c\u4e0d\u5b9a\u79ef\u5206\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_antiderivative", "college_indefinite_integral"},
        ),
        (
            "\u7279\u5f81\u5411\u91cf\u548c\u666e\u901a\u5411\u91cf\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_eigenvalue", "college_regular_vector", "college_eigenvector_solution"},
        ),
        (
            "\u5047\u8bbe\u68c0\u9a8c\u600e\u4e48\u505a\uff1f",
            {"college_hypothesis_testing", "college_null_hypothesis", "college_test_statistic"},
        ),
        (
            "\u56fe\u7684\u6700\u77ed\u8def\u600e\u4e48\u5b66\uff1f",
            {"college_graph_shortest_path", "college_graph_theory_intro"},
        ),
        (
            "\u5bfc\u6570\u548c\u5207\u7ebf\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {
                "senior_derivative_tangent",
                "college_derivative_calculation",
                "college_tangent_line_equation",
            },
        ),
        (
            "\u671f\u671b\u548c\u5e73\u5747\u503c\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_expectation_variance", "college_sample_mean"},
        ),
        (
            "\u53d7\u529b\u5206\u6790\u600e\u4e48\u505a\uff1f",
            {"college_force_analysis"},
        ),
        (
            "BFS\u548cDFS\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_graph_traversal"},
        ),
        (
            "\u6700\u77ed\u8def\u7b97\u6cd5\u600e\u4e48\u5b66\uff1f",
            {"college_ds_shortest_path", "college_graph_shortest_path"},
        ),
        (
            "\u901f\u5ea6\u548c\u52a0\u901f\u5ea6\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"physics_senior_experiment_acceleration", "college_physics_kinematics_calculus"},
        ),
        (
            "\u725b\u987f\u7b2c\u4e8c\u5b9a\u5f8b\u9898\u600e\u4e48\u505a\uff1f",
            {"physics_senior_newton_laws"},
        ),
        (
            "\u529f\u548c\u80fd\u91cf\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"physics_junior_work_power", "college_physics_work_energy_theorem"},
        ),
        (
            "\u6c27\u5316\u5242\u548c\u8fd8\u539f\u5242\u600e\u4e48\u533a\u5206\uff1f",
            {"chem_senior_redox"},
        ),
        (
            "\u6c27\u5316\u8fd8\u539f\u53cd\u5e94\u600e\u4e48\u914d\u5e73\uff1f",
            {"chem_senior_redox", "chem_senior_redox_balance"},
        ),
        (
            "\u5316\u5b66\u5e73\u8861\u79fb\u52a8\u600e\u4e48\u5224\u65ad\uff1f",
            {"chem_senior_equilibrium", "chem_senior_equilibrium_constant"},
        ),
        (
            "pH\u600e\u4e48\u8ba1\u7b97\uff1f",
            {"chem_senior_ph_ionization"},
        ),
        (
            "\u57fa\u56e0\u578b\u548c\u8868\u73b0\u578b\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"bio_senior_genetics_law", "bio_junior_inheritance_intro"},
        ),
        (
            "\u9057\u4f20\u6982\u7387\u9898\u600e\u4e48\u505a\uff1f",
            {"bio_junior_inheritance_intro", "bio_senior_genetics_law"},
        ),
        (
            "\u51cf\u6570\u5206\u88c2\u548c\u6709\u4e1d\u5206\u88c2\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"bio_senior_meiosis", "bio_senior_mitosis_image"},
        ),
        (
            "\u9605\u8bfb\u7406\u89e3\u4e3b\u65e8\u9898\u600e\u4e48\u505a\uff1f",
            {"english_senior_reading_main_idea"},
        ),
        (
            "\u5b8c\u5f62\u586b\u7a7a\u600e\u4e48\u505a\uff1f",
            {"english_junior_cloze_context", "english_senior_cloze_logic"},
        ),
        (
            "\u6570\u7ec4\u548c\u94fe\u8868\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_ds_linear_list", "college_ds_linked_list", "college_ds_dynamic_array"},
        ),
        (
            "\u9012\u5f52\u548c\u8fed\u4ee3\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_ds_recursion_iteration", "college_c_recursion_basic"},
        ),
        (
            "\u6808\u548c\u961f\u5217\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_ds_stack", "college_ds_queue_circular"},
        ),
        (
            "\u5feb\u901f\u6392\u5e8f\u548c\u5f52\u5e76\u6392\u5e8f\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_ds_quick_merge_sort", "college_ds_sorting_basic"},
        ),
        (
            "\u54c8\u5e0c\u8868\u51b2\u7a81\u600e\u4e48\u5904\u7406\uff1f",
            {"college_ds_hash_table"},
        ),
        (
            "\u5730\u56fe\u6bd4\u4f8b\u5c3a\u600e\u4e48\u770b\uff1f",
            {"geo_junior_map_skills"},
        ),
        (
            "\u5b63\u98ce\u6c14\u5019\u600e\u4e48\u5224\u65ad\uff1f",
            {"geo_junior_climate_types", "geo_senior_atmospheric_circulation"},
        ),
        (
            "\u6cb3\u6d41\u5730\u8c8c\u600e\u4e48\u5f62\u6210\uff1f",
            {"geo_junior_river_landform", "geo_senior_geomorphology"},
        ),
        (
            "\u5de5\u4e1a\u533a\u4f4d\u56e0\u7d20\u600e\u4e48\u5206\u6790\uff1f",
            {"geo_senior_industrial_location", "geo_junior_agriculture_industry"},
        ),
        (
            "\u79e6\u671d\u4e2d\u592e\u96c6\u6743\u6709\u4ec0\u4e48\u7279\u70b9\uff1f",
            {"history_junior_qin_unification"},
        ),
        (
            "\u9e26\u7247\u6218\u4e89\u4e3a\u4ec0\u4e48\u662f\u8fd1\u4ee3\u5f00\u7aef\uff1f",
            {"history_junior_opium_war"},
        ),
        (
            "\u5de5\u4e1a\u9769\u547d\u6709\u4ec0\u4e48\u5f71\u54cd\uff1f",
            {"history_junior_industrial_revolution"},
        ),
        (
            "\u53f2\u6599\u9898\u600e\u4e48\u5206\u6790\uff1f",
            {"history_senior_material_analysis"},
        ),
        (
            "\u5e02\u573a\u673a\u5236\u548c\u5b8f\u89c2\u8c03\u63a7\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"pol_senior_economy_market", "pol_senior_socialism_market"},
        ),
        (
            "\u6743\u5229\u548c\u4e49\u52a1\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"pol_junior_rights_obligations"},
        ),
        (
            "\u552f\u7269\u8fa9\u8bc1\u6cd5\u600e\u4e48\u7528\uff1f",
            {"pol_senior_philosophy_dialectics"},
        ),
        (
            "\u4f9d\u6cd5\u6cbb\u56fd\u6750\u6599\u9898\u600e\u4e48\u7b54\uff1f",
            {"pol_senior_rule_of_law"},
        ),
        (
            "\u6587\u8a00\u865a\u8bcd\u600e\u4e48\u5224\u65ad\uff1f",
            {"chinese_junior_classical_function_words", "chinese_senior_classical_function_words"},
        ),
        (
            "\u8bd7\u6b4c\u610f\u8c61\u548c\u60c5\u611f\u600e\u4e48\u5206\u6790\uff1f",
            {"chinese_senior_poetry_image_emotion"},
        ),
        (
            "\u6750\u6599\u4f5c\u6587\u600e\u4e48\u5ba1\u9898\u7acb\u610f\uff1f",
            {"chinese_senior_composition_material_review"},
        ),
        (
            "\u5c0f\u8bf4\u4eba\u7269\u5f62\u8c61\u600e\u4e48\u5206\u6790\uff1f",
            {"chinese_senior_literary_image"},
        ),
        (
            "\u9700\u6c42\u548c\u4f9b\u7ed9\u66f2\u7ebf\u600e\u4e48\u770b\uff1f",
            {"college_micro_demand_supply"},
        ),
        (
            "\u673a\u4f1a\u6210\u672c\u662f\u4ec0\u4e48\uff1f",
            {"college_econ_scarcity_opportunity_cost"},
        ),
        (
            "\u901a\u8d27\u81a8\u80c0\u548cCPI\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_macro_cpi_inflation"},
        ),
        (
            "\u8d22\u653f\u653f\u7b56\u548c\u8d27\u5e01\u653f\u7b56\u6709\u4ec0\u4e48\u533a\u522b\uff1f",
            {"college_macro_fiscal_policy", "college_macro_monetary_policy"},
        ),
        (
            "\u5bfc\u6570\u548c\u901f\u5ea6\u52a0\u901f\u5ea6\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {
                "physics_senior_experiment_acceleration",
                "college_physics_kinematics_calculus",
                "college_derivative_calculation",
            },
        ),
        (
            "\u8fb9\u9645\u6210\u672c\u4e3a\u4ec0\u4e48\u7528\u5bfc\u6570\uff1f",
            {"college_derivative_definition", "college_micro_cost_short_run"},
        ),
        (
            "\u77e9\u9635\u548c\u56fe\u7b97\u6cd5\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_rank", "college_matrix_operations", "college_ds_graph_representation"},
        ),
        (
            "\u51fd\u6570\u56fe\u50cf\u600e\u4e48\u770b\u7269\u7406\u8fd0\u52a8\uff1f",
            {"function_graph_reading", "physics_senior_motion_graph_synthesis"},
        ),
        (
            "\u9700\u6c42\u4f9b\u7ed9\u66f2\u7ebf\u548c\u51fd\u6570\u56fe\u50cf\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_micro_demand_supply", "linear_function_graph"},
        ),
        (
            "\u5f39\u6027\u5206\u6790\u548c\u659c\u7387\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_micro_elasticity", "linear_function_kb"},
        ),
        (
            "\u6781\u503c\u600e\u4e48\u7528\u6765\u5206\u6790\u5229\u6da6\u6700\u5927\u5316\uff1f",
            {
                "college_micro_firm_profit",
                "college_extreme_value_points",
                "college_absolute_extrema",
            },
        ),
        (
            "\u79ef\u5206\u548c\u7269\u7406\u505a\u529f\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"physics_junior_work_power", "college_integral_physics_application"},
        ),
        (
            "\u4e09\u89d2\u51fd\u6570\u548c\u7b80\u8c10\u8fd0\u52a8\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {
                "trig_area",
                "physics_senior_simple_harmonic",
                "college_physics_simple_harmonic_oscillation",
                "senior_trig_graph",
            },
        ),
        (
            "\u5411\u91cf\u548c\u529b\u7684\u5206\u89e3\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"physics_senior_force_decomposition", "senior_plane_vector_concept"},
        ),
        (
            "\u9012\u5f52\u4e3a\u4ec0\u4e48\u548c\u8bc1\u660e\u601d\u8def\u6709\u5173\uff1f",
            {"geometric_proof_strategy", "college_c_recursion_basic", "proof_writing"},
        ),
        (
            "\u590d\u6742\u5ea6\u548c\u51fd\u6570\u589e\u957f\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {
                "quadratic_monotonicity",
                "college_ds_algorithm_complexity",
                "function_thinking",
                "senior_exponential_function",
            },
        ),
        (
            "\u56fe\u8bba\u548c\u56fe\u904d\u5386\u7b97\u6cd5\u600e\u4e48\u8fde\u8d77\u6765\uff1f",
            {
                "college_ds_algorithm_complexity",
                "college_graph_theory_intro",
                "college_ds_graph_traversal",
            },
        ),
        (
            "\u4e8c\u7ef4\u6570\u7ec4\u548c\u77e9\u9635\u8fd0\u7b97\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_matrix_operations", "college_c_2d_array_matrix"},
        ),
        (
            "\u52a8\u6001\u89c4\u5212\u548c\u77e9\u9635\u601d\u7ef4\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_ds_greedy_dp_intro", "college_matrix_operations"},
        ),
        (
            "\u8fb9\u9645\u6536\u76ca\u548c\u5bfc\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"college_derivative_definition", "college_micro_firm_profit"},
        ),
        (
            "\u4f9b\u9700\u5747\u8861\u4e3a\u4ec0\u4e48\u8981\u770b\u4ea4\u70b9\uff1f",
            {"linear_function_intersection", "college_micro_demand_supply"},
        ),
        (
            "\u6bd4\u7387\u53d8\u5316\u548c\u7ecf\u6d4e\u5f39\u6027\u600e\u4e48\u8fde\u63a5\uff1f",
            {"college_micro_elasticity", "function_concept"},
        ),
        (
            "\u4f4d\u79fb\u65f6\u95f4\u56fe\u50cf\u548c\u4e00\u6b21\u51fd\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"linear_function_graph", "physics_senior_motion_graph_synthesis"},
        ),
        (
            "\u52a0\u901f\u5ea6\u4e3a\u4ec0\u4e48\u53ef\u4ee5\u7528\u5bfc\u6570\u7406\u89e3\uff1f",
            {"physics_senior_experiment_acceleration", "college_derivative_calculation"},
        ),
        (
            "\u53d8\u529b\u505a\u529f\u4e3a\u4ec0\u4e48\u8981\u7528\u79ef\u5206\uff1f",
            {"college_integral_physics_application", "physics_senior_work_energy_path"},
        ),
        (
            "\u659c\u9762\u529b\u5206\u89e3\u548c\u4e09\u89d2\u51fd\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
            {"trig_area", "physics_senior_force_decomposition", "senior_trig_definitions"},
        ),
        (
            "矢量空间和物理矢量有什么关系？",
            {"college_physics_vector_calculus", "college_vector_space", "senior_vector_linear_operation"},
        ),
        (
            "功和动能定理为什么要用被积函数和数量积？",
            {
                "college_integral_application_integrand",
                "college_physics_work_energy_theorem",
                "senior_vector_dot_product",
            },
        ),
        (
            "简谐运动为什么可以用三角函数描述？",
            {
                "college_physics_simple_harmonic_oscillation",
                "physics_senior_simple_harmonic",
                "senior_trig_graph",
                "trig_area",
            },
        ),
        (
            "阻尼振动和指数函数增长衰减有什么关系？",
            {"college_physics_damped_forced_oscillation", "senior_exponential_function"},
        ),
        (
            "经济增长和指数函数有什么关系？",
            {"college_econ_growth_productivity", "senior_exponential_function"},
        ),
        (
            "经济数据图表为什么要懂期望方差和正态分布？",
            {"college_econ_data_graph", "college_expectation_variance", "college_normal_distribution"},
        ),
        (
            "生产可能性边界和数学建模怎么联系？",
            {"college_econ_ppf", "college_econ_scarcity_opportunity_cost", "mathematical_modeling"},
        ),
        (
            "短期成本曲线为什么要用导数和单调极值？",
            {"college_derivative_calculation", "college_micro_cost_short_run", "college_monotonic_extrema"},
        ),
        (
            "弹性分析为什么既看斜率又看导数？",
            {"college_derivative_calculation", "college_micro_elasticity", "linear_function_kb"},
        ),
        (
            "利润最大化为什么既看二次函数最值又看导数？",
            {
                "college_derivative_calculation",
                "college_extreme_value_points",
                "college_micro_firm_profit",
                "quadratic_max_min",
            },
        ),
        (
            "图的存储结构和邻接矩阵怎么联系？",
            {"college_c_2d_array_matrix", "college_ds_graph_representation", "college_matrix_operations"},
        ),
        (
            "算法复杂度为什么要用函数思维和对数函数？",
            {"college_ds_algorithm_complexity", "function_thinking", "senior_logarithmic_function"},
        ),
        (
            "递归程序和数列递推有什么关系？",
            {"college_c_recursion_basic", "college_ds_recursion_iteration", "senior_sequence_recursion"},
        ),
        (
            "二维数组表示矩阵时行列维度容易混在哪里？",
            {"college_c_2d_array_matrix", "college_matrix_dimension", "college_matrix_operations"},
        ),
        (
            "图论里的遍历和算法里的BFS DFS有什么关系？",
            {"college_ds_graph_traversal", "college_graph_theory_intro", "college_graph_traversal"},
        ),
        (
            "英语概要写作的转述压缩和写作衔接怎么一起练？",
            {"english_senior_summary_paraphrase", "english_senior_writing_cohesion"},
        ),
        (
            "英语作者态度题和词义猜测、七选五连贯有什么关系？",
            {
                "english_senior_reading_author_attitude",
                "english_senior_reading_word_guess",
                "english_senior_seven_choice_coherence",
            },
        ),
        (
            "中式表达纠偏和语境词汇复习怎么一起做？",
            {"english_senior_translation_chinese_interference", "english_senior_vocabulary_context_review"},
        ),
        (
            "实用类文本信息筛选和信息类文本比较整合有什么区别？",
            {"chinese_senior_modern_info_comparison", "chinese_senior_practical_filter"},
        ),
        (
            "议论文论证层次和素材作文审题立意怎么连接？",
            {
                "chinese_senior_composition_argument_layer",
                "chinese_senior_composition_example_argument",
                "chinese_senior_composition_material_review",
            },
        ),
        (
            "地图三要素和比例尺有什么关系？",
            {"geo_junior_map_skills", "primary_ratio_scale"},
        ),
        (
            "世界气候类型和函数图像信息读取有什么关系？",
            {"geo_junior_climate_types", "function_graph_reading"},
        ),
        (
            "农业工业区位和需求供给模型有什么关系？",
            {"geo_junior_agriculture_industry", "college_micro_demand_supply"},
        ),
        (
            "区域可持续发展和政策权衡分析有什么关系？",
            {"geo_senior_regional_sustainability", "college_econ_policy_tradeoff"},
        ),
        (
            "秦统一与中央集权和全面依法治国有什么关系？",
            {"history_junior_qin_unification", "pol_senior_rule_of_law"},
        ),
        (
            "秦统一与中央集权和人民民主与政治参与有什么关系？",
            {"history_junior_qin_unification", "pol_senior_political_participation"},
        ),
        (
            "鸦片战争与近代开端和国际关系与中国外交有什么关系？",
            {"history_junior_opium_war", "pol_senior_international_relations"},
        ),
        (
            "工业革命和产业区位与区域发展有什么关系？",
            {"history_junior_industrial_revolution", "geo_senior_industrial_location"},
        ),
        (
            "史料实证方法和实用类文本信息筛选有什么关系？",
            {"history_senior_material_analysis", "chinese_senior_practical_filter"},
        ),
        (
            "权利与义务和阅读题规范分点有什么关系？",
            {"pol_junior_rights_obligations", "chinese_senior_text_answer_template"},
        ),
        (
            "市场机制与宏观调控和财政政策有什么关系？",
            {"pol_senior_economy_market", "college_macro_fiscal_policy"},
        ),
        (
            "唯物辩证法和议论文论证层次有什么关系？",
            {"pol_senior_philosophy_dialectics", "chinese_senior_composition_argument_layer"},
        ),
        (
            "国际关系与中国外交和世界市场与全球化有什么关系？",
            {"pol_senior_international_relations", "history_senior_globalization"},
        ),
        (
            "阅读细节定位题和实用类文本信息筛选有什么关系？",
            {"english_senior_reading_detail_location", "chinese_senior_practical_filter"},
        ),
        (
            "作者态度与观点题和逻辑与思维方法有什么关系？",
            {"english_senior_reading_author_attitude", "pol_senior_logical_thinking"},
        ),
        (
            "概要写作和压缩语段与概括有什么关系？",
            {"english_senior_summary_writing", "chinese_senior_language_compression"},
        ),
        (
            "化学方程式配平和一元一次方程有什么关系？",
            {"chem_junior_chemical_equation", "linear_equation"},
        ),
        (
            "弱电解质电离与 pH 和对数函数有什么关系？",
            {"chem_senior_ph_ionization", "senior_logarithmic_function"},
        ),
        (
            "化学反应速率影响因素和函数单调性有什么关系？",
            {"chem_senior_rate_factors", "senior_function_monotonicity"},
        ),
        (
            "酸碱中和滴定和一次函数交点有什么关系？",
            {"chem_senior_acid_base_titration", "linear_function_intersection"},
        ),
        (
            "孟德尔遗传定律和二项分布有什么关系？",
            {"bio_senior_genetics_law", "senior_binomial_distribution"},
        ),
        (
            "性状遗传初步和列表法与树状图有什么关系？",
            {"bio_junior_inheritance_intro", "tree_diagram"},
        ),
        (
            "酶活性曲线分析和导数与极值最值有什么关系？",
            {"bio_senior_enzyme_curve", "senior_derivative_extrema"},
        ),
        (
            "光合作用曲线分析和函数图像信息读取有什么关系？",
            {"bio_senior_photosynthesis_curve", "function_graph_reading"},
        ),
        (
            "实验误差与结论评价和方差有什么关系？",
            {"bio_senior_experiment_error_analysis", "variance"},
        ),
        (
            "电势图像和函数图像分析有什么关系？",
            {"physics_senior_electric_potential_graph", "college_function_graph_analysis"},
        ),
        (
            "实验数据处理和方差有什么关系？",
            {"physics_senior_experiment_data", "variance"},
        ),
        (
            "几何光学和相似三角形有什么关系？",
            {"college_physics_geometric_optics", "similar_triangles"},
        ),
        (
            "反应速率图像和函数单调性有什么关系？",
            {"chem_senior_rate_factors", "senior_function_monotonicity"},
        ),
        (
            "平衡常数和反比例函数图像有什么关系？",
            {"chem_senior_equilibrium_constant", "inverse_function_graph"},
        ),
        (
            "滴定误差和一次函数交点有什么关系？",
            {"chem_senior_acid_base_titration", "linear_function_intersection"},
        ),
        (
            "氧化还原滴定和方程配平有什么关系？",
            {"chem_senior_redox_titration", "chem_senior_redox_balance"},
        ),
        (
            "有机同分异构和排列组合有什么关系？",
            {"chem_senior_organic_isomer", "college_combination"},
        ),
        (
            "PCR和电泳与指数增长有什么关系？",
            {"bio_senior_pcr_electrophoresis", "senior_exponential_function"},
        ),
        (
            "种群增长和指数函数有什么关系？",
            {"bio_senior_population_growth", "senior_exponential_function"},
        ),
        (
            "群落演替和地理可持续发展有什么关系？",
            {"bio_senior_community_succession", "geo_senior_regional_sustainability"},
        ),
        (
            "细胞工程和实验控制有什么关系？",
            {"bio_senior_cell_engineering", "bio_senior_experiment_control"},
        ),
        (
            "阅读主旨题和语文信息筛选有什么关系？",
            {"english_senior_reading_main_idea", "chinese_senior_practical_filter"},
        ),
        (
            "概要写作和语文压缩语段有什么关系？",
            {"english_senior_summary_writing", "chinese_senior_language_compression"},
        ),
        (
            "续写情节逻辑和小说情节作用有什么关系？",
            {"english_senior_continuation_plot_logic", "chinese_senior_literary_plot_effect"},
        ),
        (
            "英语应用文通知和语文信息筛选有什么关系？",
            {"english_senior_application_notice", "chinese_senior_practical_filter"},
        ),
        (
            "词汇语境复习和语文衔接连贯有什么关系？",
            {
                "english_senior_vocabulary_context_review",
                "chinese_senior_language_cohesion",
                "chinese_senior_exam_revision_strategy",
            },
        ),
        (
            "史料实证和语文材料概括有什么关系？",
            {"history_senior_material_analysis", "chinese_senior_language_compression"},
        ),
        (
            "制度变迁和政治参与有什么关系？",
            {"history_senior_institution_change", "pol_senior_political_participation"},
        ),
        (
            "文化交流和英语阅读主旨有什么关系？",
            {"history_senior_cultural_exchange", "english_senior_reading_main_idea"},
        ),
        (
            "经济史和经济增长模型有什么关系？",
            {"history_senior_economic_history", "college_econ_growth_productivity"},
        ),
        (
            "国际关系和博弈论囚徒困境有什么关系？",
            {"pol_senior_international_relations", "college_econ_game_prisoner"},
        ),
        (
            "逻辑思维和议论文论证有什么关系？",
            {"pol_senior_logical_thinking", "chinese_senior_composition_argument_layer"},
        ),
        (
            "地貌形成和函数图像读取有什么关系？",
            {"geo_senior_geomorphology", "function_graph_reading"},
        ),
        (
            "区域可持续发展和群落演替有什么关系？",
            {"geo_senior_regional_sustainability", "bio_senior_community_succession"},
        ),
        (
            "产业区位和经济成本收益有什么关系？",
            {"geo_senior_industrial_location", "college_econ_cost_benefit"},
        ),
    ]

    expected_subjects = {
        "biology",
        "chemistry",
        "chinese",
        "computer_science",
        "economics",
        "english",
        "geography",
        "history",
        "math",
        "physics",
        "politics",
    }
    minimum_subject_query_counts = {
        "biology": 8,
        "chemistry": 8,
        "chinese": 8,
        "computer_science": 10,
        "economics": 8,
        "english": 8,
        "geography": 8,
        "history": 8,
        "math": 30,
        "physics": 7,
        "politics": 8,
    }
    topic_subjects = {
        str(topic["id"]): str(topic["subject"])
        for topic in topics
        if "id" in topic and "subject" in topic
    }
    subject_query_counts = dict.fromkeys(expected_subjects, 0)

    cross_subject_queries = {
        "\u5bfc\u6570\u548c\u901f\u5ea6\u52a0\u901f\u5ea6\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u8fb9\u9645\u6210\u672c\u4e3a\u4ec0\u4e48\u7528\u5bfc\u6570\uff1f",
        "\u51fd\u6570\u56fe\u50cf\u600e\u4e48\u770b\u7269\u7406\u8fd0\u52a8\uff1f",
        "\u9700\u6c42\u4f9b\u7ed9\u66f2\u7ebf\u548c\u51fd\u6570\u56fe\u50cf\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u5f39\u6027\u5206\u6790\u548c\u659c\u7387\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u6781\u503c\u600e\u4e48\u7528\u6765\u5206\u6790\u5229\u6da6\u6700\u5927\u5316\uff1f",
        "\u79ef\u5206\u548c\u7269\u7406\u505a\u529f\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u4e09\u89d2\u51fd\u6570\u548c\u7b80\u8c10\u8fd0\u52a8\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u5411\u91cf\u548c\u529b\u7684\u5206\u89e3\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u9012\u5f52\u4e3a\u4ec0\u4e48\u548c\u8bc1\u660e\u601d\u8def\u6709\u5173\uff1f",
        "\u590d\u6742\u5ea6\u548c\u51fd\u6570\u589e\u957f\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u56fe\u8bba\u548c\u56fe\u904d\u5386\u7b97\u6cd5\u600e\u4e48\u8fde\u8d77\u6765\uff1f",
        "\u4e8c\u7ef4\u6570\u7ec4\u548c\u77e9\u9635\u8fd0\u7b97\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u8fb9\u9645\u6536\u76ca\u548c\u5bfc\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u6bd4\u7387\u53d8\u5316\u548c\u7ecf\u6d4e\u5f39\u6027\u600e\u4e48\u8fde\u63a5\uff1f",
        "\u4f4d\u79fb\u65f6\u95f4\u56fe\u50cf\u548c\u4e00\u6b21\u51fd\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "\u52a0\u901f\u5ea6\u4e3a\u4ec0\u4e48\u53ef\u4ee5\u7528\u5bfc\u6570\u7406\u89e3\uff1f",
        "\u53d8\u529b\u505a\u529f\u4e3a\u4ec0\u4e48\u8981\u7528\u79ef\u5206\uff1f",
        "\u659c\u9762\u529b\u5206\u89e3\u548c\u4e09\u89d2\u51fd\u6570\u6709\u4ec0\u4e48\u5173\u7cfb\uff1f",
        "矢量空间和物理矢量有什么关系？",
        "功和动能定理为什么要用被积函数和数量积？",
        "简谐运动为什么可以用三角函数描述？",
        "阻尼振动和指数函数增长衰减有什么关系？",
        "经济增长和指数函数有什么关系？",
        "经济数据图表为什么要懂期望方差和正态分布？",
        "生产可能性边界和数学建模怎么联系？",
        "短期成本曲线为什么要用导数和单调极值？",
        "弹性分析为什么既看斜率又看导数？",
        "利润最大化为什么既看二次函数最值又看导数？",
        "图的存储结构和邻接矩阵怎么联系？",
        "算法复杂度为什么要用函数思维和对数函数？",
            "递归程序和数列递推有什么关系？",
            "二维数组表示矩阵时行列维度容易混在哪里？",
            "图论里的遍历和算法里的BFS DFS有什么关系？",
            "地图三要素和比例尺有什么关系？",
            "世界气候类型和函数图像信息读取有什么关系？",
            "农业工业区位和需求供给模型有什么关系？",
            "区域可持续发展和政策权衡分析有什么关系？",
            "秦统一与中央集权和全面依法治国有什么关系？",
            "秦统一与中央集权和人民民主与政治参与有什么关系？",
            "鸦片战争与近代开端和国际关系与中国外交有什么关系？",
            "工业革命和产业区位与区域发展有什么关系？",
            "史料实证方法和实用类文本信息筛选有什么关系？",
            "权利与义务和阅读题规范分点有什么关系？",
            "市场机制与宏观调控和财政政策有什么关系？",
            "唯物辩证法和议论文论证层次有什么关系？",
            "国际关系与中国外交和世界市场与全球化有什么关系？",
            "阅读细节定位题和实用类文本信息筛选有什么关系？",
            "作者态度与观点题和逻辑与思维方法有什么关系？",
            "概要写作和压缩语段与概括有什么关系？",
            "化学方程式配平和一元一次方程有什么关系？",
            "弱电解质电离与 pH 和对数函数有什么关系？",
            "化学反应速率影响因素和函数单调性有什么关系？",
            "酸碱中和滴定和一次函数交点有什么关系？",
            "孟德尔遗传定律和二项分布有什么关系？",
            "性状遗传初步和列表法与树状图有什么关系？",
            "酶活性曲线分析和导数与极值最值有什么关系？",
            "光合作用曲线分析和函数图像信息读取有什么关系？",
            "实验误差与结论评价和方差有什么关系？",
        }

    cross_subject_queries.update(
        {
            "电势图像和函数图像分析有什么关系？",
            "实验数据处理和方差有什么关系？",
            "几何光学和相似三角形有什么关系？",
            "反应速率图像和函数单调性有什么关系？",
            "平衡常数和反比例函数图像有什么关系？",
            "滴定误差和一次函数交点有什么关系？",
            "有机同分异构和排列组合有什么关系？",
            "PCR和电泳与指数增长有什么关系？",
            "种群增长和指数函数有什么关系？",
            "群落演替和地理可持续发展有什么关系？",
            "阅读主旨题和语文信息筛选有什么关系？",
            "概要写作和语文压缩语段有什么关系？",
            "续写情节逻辑和小说情节作用有什么关系？",
            "英语应用文通知和语文信息筛选有什么关系？",
            "词汇语境复习和语文衔接连贯有什么关系？",
            "史料实证和语文材料概括有什么关系？",
            "制度变迁和政治参与有什么关系？",
            "文化交流和英语阅读主旨有什么关系？",
            "经济史和经济增长模型有什么关系？",
            "国际关系和博弈论囚徒困境有什么关系？",
            "逻辑思维和议论文论证有什么关系？",
            "地貌形成和函数图像读取有什么关系？",
            "区域可持续发展和群落演替有什么关系？",
            "产业区位和经济成本收益有什么关系？",
        }
    )

    assert len(cases) == 151
    assert len(cross_subject_queries) >= 83
    assert set(minimum_subject_query_counts) == expected_subjects
    for _query, expected_topic_ids in cases:
        covered_subjects = {topic_subjects[topic_id] for topic_id in expected_topic_ids}
        assert covered_subjects <= expected_subjects
        for subject in covered_subjects:
            subject_query_counts[subject] += 1

    assert {
        subject
        for subject, query_count in subject_query_counts.items()
        if query_count > 0
    } == expected_subjects
    for subject, minimum_query_count in minimum_subject_query_counts.items():
        assert subject_query_counts[subject] >= minimum_query_count

    for query, expected_topic_ids in cases:
        payload = build_knowledge_guidance_payload(topics=topics, query=query)

        assert payload["topic"]["id"] in expected_topic_ids, query
        subgraph = payload["relevant_subgraph"]
        model_context = payload["model_context"]
        assert model_context["mode"] == "guidance", query
        assert model_context["query"] == query, query
        assert model_context["focus"]["id"] == subgraph["focus_topics"][0]["id"], query
        assert model_context["summary"]["node_count"] == subgraph["summary"]["node_count"], query
        assert model_context["summary"]["edge_count"] == subgraph["summary"]["edge_count"], query
        assert model_context["summary"]["raw_seed_included"] is False, query
        for raw_key in ("topics", "nodes", "edges", "matches", "relation_groups"):
            assert raw_key not in model_context, query
        compact_fields = (
            "prerequisites",
            "procedure",
            "confusions",
            "applications",
            "extensions",
            "review_with",
            "practice_suggestions",
        )
        for field in compact_fields:
            assert isinstance(model_context[field], list), query
            assert all(isinstance(item, str) for item in model_context[field]), query
        assert len(model_context["practice_suggestions"]) <= 6, query
        assert sum(1 for field in compact_fields[:4] if model_context[field]) >= 2, query
        relation_groups = payload["relation_groups"]
        active_core_groups = {
            relation
            for relation in ("prerequisite", "confusable", "procedure_step", "application")
            if relation_groups[relation]["items"]
        }
        assert len(active_core_groups) >= 2, query
        if query in cross_subject_queries:
            node_subjects = {
                str(node["id"]): str(node.get("subject") or "")
                for node in subgraph.get("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            node_subjects.update(
                {
                    topic_id: subject
                    for topic_id, subject in topic_subjects.items()
                    if topic_id not in node_subjects
                }
            )
            node_labels = {
                str(node["id"]): str(node.get("label") or node.get("id") or "")
                for node in subgraph.get("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            cross_subject_edges = [
                edge
                for edge in subgraph.get("edges", [])
                if isinstance(edge, dict)
                and node_subjects.get(str(edge.get("from") or ""))
                and node_subjects.get(str(edge.get("to") or ""))
                and node_subjects[str(edge.get("from") or "")]
                != node_subjects[str(edge.get("to") or "")]
            ]
            assert cross_subject_edges, query
            compact_text = "\n".join(
                item
                for field in compact_fields
                for item in model_context[field]
            )
            assert any(
                node_labels.get(str(edge.get(endpoint) or ""))
                and node_labels[str(edge.get(endpoint) or "")] in compact_text
                for edge in cross_subject_edges
                for endpoint in ("from", "to")
            ), query


def test_study_knowledge_retrieval_eval_reports_focus_and_cross_subject_quality() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]
    cases = [
        {
            "query": "导数和速度加速度有什么关系？",
            "expected_topic_ids": [
                "physics_senior_experiment_acceleration",
                "college_physics_kinematics_calculus",
                "college_derivative_calculation",
            ],
            "expect_cross_subject": True,
        },
        {
            "query": "PCR和电泳与指数增长有什么关系？",
            "expected_topic_ids": ["bio_senior_pcr_electrophoresis", "senior_exponential_function"],
            "expect_cross_subject": True,
        },
        {
            "query": "概要写作和语文压缩语段有什么关系？",
            "expected_topic_ids": ["english_senior_summary_writing", "chinese_senior_language_compression"],
            "expect_cross_subject": True,
        },
        {
            "query": "驻点和极值点怎么区分？",
            "expected_topic_ids": ["college_stationary_points", "college_critical_points"],
            "expect_cross_subject": False,
        },
    ]

    report = evaluate_knowledge_retrieval_queries(topics=topics, cases=cases)

    assert report["summary"]["case_count"] == len(cases)
    assert report["summary"]["focus_hit_count"] == len(cases)
    assert report["summary"]["cross_subject_case_count"] == 3
    assert report["summary"]["cross_subject_edge_count"] >= 3
    assert report["summary"]["model_context_cross_subject_cue_count"] >= 3
    assert report["summary"]["thin_relation_group_count"] == 0
    assert report["summary"]["raw_seed_included_count"] == 0
    for result in report["results"]:
        assert result["query"]
        assert result["focus_topic_id"] in result["expected_topic_ids"]
        assert len(result["top_matches"]) <= 5
        assert result["subgraph_node_ids"]
        assert result["model_context"]["summary"]["raw_seed_included"] is False
        assert result["focus_hit"] is True
        assert result["has_raw_seed"] is False
        assert result["thin_relation_groups"] == []
        if result["expect_cross_subject"]:
            assert result["has_cross_subject_edge"] is True
            assert result["model_context_has_cross_subject_cue"] is True


def test_study_knowledge_retrieval_eval_static_cases_meet_quality_gate() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]
    cases_path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_eval_cases.json"
    )
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    assert isinstance(cases, list)
    assert 80 <= len(cases) <= 130

    report = evaluate_knowledge_retrieval_queries(topics=topics, cases=cases)
    summary = report["summary"]
    cross_subject_case_count = int(summary["cross_subject_case_count"])

    assert summary["case_count"] == len(cases)
    assert summary["focus_hit_count"] / len(cases) >= 0.95
    assert summary["raw_seed_included_count"] == 0
    assert summary["thin_relation_group_count"] == 0
    assert cross_subject_case_count >= 40
    assert summary["cross_subject_edge_count"] / cross_subject_case_count >= 0.95
    assert (
        summary["model_context_cross_subject_cue_count"] / cross_subject_case_count
        >= 0.95
    )
    assert summary["bad_hub_absorption_count"] <= 4

    bad_hub_cases = [
        result
        for result in report["results"]
        if result["bad_hub_absorbed"]
    ]
    for result in bad_hub_cases:
        assert result["top_matches"], result["query"]
        assert result["expected_topic_ids"], result["query"]


def test_study_knowledge_guidance_groups_edges_into_user_facing_sections() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]

    payload = build_knowledge_guidance_payload(
        topics=topics,
        query="\u5bfc\u6570\u600e\u4e48\u7528\u6765\u6c42\u6781\u503c\uff1f",
    )

    assert set(payload["relation_groups"]) >= {
        "prerequisite",
        "confusable",
        "procedure_step",
        "application",
        "extends",
        "co_occurs",
    }
    sections = {section["relation"]: section for section in payload["guidance_sections"]}
    assert sections["prerequisite"]["title"] == "\u5148\u8865\u4ec0\u4e48"
    assert sections["confusable"]["title"] == "\u5bb9\u6613\u6df7\u5728\u54ea\u91cc"
    assert sections["procedure_step"]["title"] == "\u89e3\u9898\u6d41\u7a0b\u4e0b\u4e00\u6b65"
    assert sections["application"]["title"] == "\u5178\u578b\u7528\u9014"
    assert sections["extends"]["title"] == "\u540e\u7eed\u62d3\u5c55"
    assert sections["co_occurs"]["title"] == "\u4e00\u8d77\u590d\u4e60"
    assert any(
        item["relation"] == "procedure_step"
        and {
            item["from"],
            item["to"],
        }
        & {"college_critical_points", "college_stationary_points"}
        for item in sections["procedure_step"]["items"]
    )
    for section in sections.values():
        for item in section["items"]:
            assert item["priority"] in {"core", "useful", "optional"}
            assert item["context"] in {"diagnosis", "explanation", "practice", "review"}
            assert 0.0 <= item["confidence"] <= 1.0
    assert payload["summary"]["active_relation_group_count"] >= 2


def test_study_knowledge_map_ui_groups_semantic_relation_layers() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    static_css = (plugin_dir / "static" / "style.css").read_text(encoding="utf-8")
    surface_source = (
        plugin_dir / "surfaces" / "knowledge_map.tsx"
    ).read_text(encoding="utf-8")
    surface_utils = (
        plugin_dir / "surfaces" / "study_surface_utils.ts"
    ).read_text(encoding="utf-8")
    en_i18n = json.loads((plugin_dir / "i18n" / "en.json").read_text(encoding="utf-8"))
    zh_i18n = json.loads((plugin_dir / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    relation_keys = {
        "application",
        "procedure_step",
        "confusable",
        "co_occurs",
        "supports",
        "analogy",
    }

    for source in (static_source, surface_source):
        for relation in relation_keys:
            assert f"ui.knowledge.edge_relation.{relation}" in source
        assert "data-relation" in source
        assert "data-priority" in source
        assert "data-context" in source
        assert "knowledge-edge-row__reason" in source
        assert "knowledge-edge-row__meta" in source
    assert "function renderKnowledgeEdgeGraph" in static_source
    assert "renderKnowledgeEdgeGraph(Array.from(groups.values()).slice(0, 12))" in static_source
    assert ".flatMap((group) => (group.items || []).slice(0, 6)" in static_source
    assert "knowledge-edge-graph__svg" in static_source
    assert "marker-end" in static_source
    assert "knowledge-edge-arrow" in static_source
    assert "function edgeGraph" in surface_source
    assert "markerEnd=\"url(#knowledge-edge-arrow-surface)\"" in surface_source
    assert "knowledge-edge-graph__svg" in surface_source
    for relation in relation_keys:
        assert en_i18n[f"ui.knowledge.edge_relation.{relation}"]
        assert zh_i18n[f"ui.knowledge.edge_relation.{relation}"]
    for source in (static_css, surface_utils):
        assert ".knowledge-edge-graph" in source
        assert ".knowledge-edge-graph__edge" in source
        assert ".knowledge-edge-graph__node rect" in source
        assert 'data-relation="confusable"' in source
        assert 'data-relation="application"' in source
        assert 'data-relation="procedure_step"' in source
        assert 'data-relation="extends"' in source
        assert 'data-relation="co_occurs"' in source
    assert "-webkit-line-clamp: 2" in static_css
    assert "function knowledgeEdgeMeta" in static_source
    assert "${relation} (${meta}): ${other}" in static_source
    assert "function renderKnowledgeNodeDetail" in static_source
    assert "function renderKnowledgeNodeDetailDialog" in static_source
    assert "knowledge-node-detail-dialog" in static_source
    assert "knowledge-node-detail-dialog" in static_css
    assert "knowledge-node-detail-dialog" in surface_utils
    assert "background: #ffffff" in static_css
    assert "background: #ffffff" in surface_utils
    assert ".knowledge-group-summary" in static_css
    assert ".knowledge-group-summary::-webkit-details-marker" in static_css
    assert ".knowledge-stage-group[open] > .knowledge-group-summary::after" in static_css
    assert "backdrop-filter: blur(3px)" in static_css
    assert "backdrop-filter: blur(3px)" in surface_utils
    assert "knowledge-node-detail" in surface_source
    assert "setSelectedNode(null)" in surface_source
    assert "ui.knowledge.node_detail.why" in surface_source
    assert "ui.knowledge.node_detail.why" in static_source
    assert "ui.knowledge.node_detail.next" in static_source
    assert "ui.knowledge.node_detail.practice" in static_source
    assert "ui.knowledge.node_detail.misconceptions" in static_source
    assert "ui.knowledge.node_detail.empty" in static_source
    assert en_i18n["ui.knowledge.node_detail.why"] == "Why connected"
    assert zh_i18n["ui.knowledge.node_detail.why"] == "关联原因"
    assert zh_i18n["ui.knowledge.node_detail.empty"]
    assert "knowledgeSubjectLabel(subjectValueFromNode(node))" in static_source
    assert "subjectLabel(props, currentNode.subject)" in surface_source


def test_study_knowledge_graph_index_builds_lookup_maps_and_edges() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]

    index = KnowledgeGraphIndex(topics)

    assert "college_derivative_calculation" in index.by_id
    assert "math" in index.subject_to_ids
    assert "college" in index.stage_to_ids
    assert index.relation_counts["procedure_step"] >= 90
    assert index.relation_counts["application"] >= 130
    matches = index.match(
        query="\u5bfc\u6570\u600e\u4e48\u7528\u6765\u6c42\u6781\u503c\uff1f",
        limit=3,
    )
    assert matches
    assert matches[0]["id"] in {
        "senior_derivative_extrema",
        "college_monotonic_extrema",
        "college_stationary_points",
        "college_extreme_value_points",
    }


def test_study_knowledge_relevant_subgraph_respects_budget_and_relation_limits() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]
    budget = SubgraphBudget(
        focus_topics=2,
        max_depth=2,
        max_nodes=12,
        max_edges=10,
        relation_limits={
            "prerequisite": 2,
            "procedure_step": 3,
            "confusable": 2,
            "application": 2,
            "extends": 1,
            "co_occurs": 1,
        },
    )

    subgraph = build_relevant_subgraph(
        topics,
        query="\u5bfc\u6570\u600e\u4e48\u7528\u6765\u6c42\u6781\u503c\uff1f",
        budget=budget,
    )

    assert subgraph["summary"]["matched"] is True
    assert subgraph["summary"]["raw_seed_included"] is False
    assert subgraph["summary"]["node_count"] <= 12
    assert subgraph["summary"]["edge_count"] <= 10
    assert len(subgraph["focus_topics"]) <= 2
    for relation, limit in budget.relation_limits.items():
        assert len(subgraph["relation_groups"][relation]["items"]) <= limit
    assert any(
        edge["relation"] in {"procedure_step", "application", "confusable", "prerequisite"}
        for edge in subgraph["edges"]
    )


def test_study_knowledge_compressed_subgraph_payload_omits_raw_seed_topics() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]
    subgraph = build_relevant_subgraph(
        KnowledgeGraphIndex(topics),
        query="\u5bfc\u6570\u600e\u4e48\u7528\u6765\u6c42\u6781\u503c\uff1f",
        budget=SubgraphBudget(max_nodes=20, max_edges=30),
    )

    compressed = compress_subgraph_payload(subgraph)

    assert compressed["summary"]["raw_seed_included"] is False
    assert "topics" not in compressed
    assert "nodes" not in compressed
    assert "edges" not in compressed
    assert compressed["focus"]["id"]
    assert compressed["procedure"] or compressed["applications"]
    assert compressed["confusions"] or compressed["prerequisites"]
    assert compressed["summary"]["node_count"] <= 20
    assert compressed["summary"]["edge_count"] <= 30


def test_study_knowledge_guidance_includes_budgeted_model_context() -> None:
    topics = _read_static_knowledge_seed_payload()["topics"]

    payload = build_knowledge_guidance_payload(
        topics=topics,
        topic_id="college_stationary_points",
        query="\u5bfc\u6570\u4e3a0\u4e3a\u4ec0\u4e48\u4e0d\u4e00\u5b9a\u662f\u6781\u503c\u70b9\uff1f",
    )

    subgraph = payload["relevant_subgraph"]
    model_context = payload["model_context"]
    assert payload["summary"]["raw_seed_included"] is False
    assert subgraph["summary"]["raw_seed_included"] is False
    assert subgraph["summary"]["node_count"] <= 20
    assert subgraph["summary"]["edge_count"] <= 30
    assert payload["summary"]["subgraph_node_count"] == subgraph["summary"]["node_count"]
    assert payload["summary"]["subgraph_edge_count"] == subgraph["summary"]["edge_count"]
    assert model_context["summary"]["raw_seed_included"] is False
    assert "topics" not in model_context
    assert "nodes" not in model_context
    assert "edges" not in model_context
    assert model_context["focus"]["id"] == "college_stationary_points"
    assert model_context["confusions"] or model_context["prerequisites"]
    assert model_context["procedure"] or model_context["applications"]


def test_study_knowledge_guidance_real_seed_explains_stationary_point_confusion() -> None:
    seed_root = Path("plugin/plugins/study_companion/static")
    manifest = json.loads((seed_root / "knowledge_graph_seed.json").read_text(encoding="utf-8"))
    topics = []
    for item in manifest["files"]:
        payload = json.loads((seed_root / item["path"]).read_text(encoding="utf-8"))
        topics.extend(payload.get("topics", []))

    payload = build_knowledge_guidance_payload(
        topics=topics,
        query="\u5bfc\u6570\u4e3a0\u4e3a\u4ec0\u4e48\u4e0d\u662f\u6781\u503c\u70b9",
    )

    assert payload["topic"]["id"] == "college_stationary_points"
    confusion_targets = {edge["to"] for edge in payload["confusions"]}
    next_targets = {edge["to"] for edge in payload["next_practice_topics"]}
    assert "college_monotonic_extrema" in confusion_targets
    assert {
        "college_monotonic_extrema",
        "college_second_derivative_test",
    }.issubset(next_targets)
    diagnosis = payload["diagnosis_questions"]
    assert any(
        item["kind"] == "procedure_probe"
        and item["topic_id"] == "college_monotonic_extrema"
        for item in diagnosis
    )


def test_study_knowledge_guidance_real_seed_covers_integral_application_and_confusions() -> None:
    seed_root = Path("plugin/plugins/study_companion/static")
    manifest = json.loads((seed_root / "knowledge_graph_seed.json").read_text(encoding="utf-8"))
    topics = []
    for item in manifest["files"]:
        payload = json.loads((seed_root / item["path"]).read_text(encoding="utf-8"))
        topics.extend(payload.get("topics", []))

    application = build_knowledge_guidance_payload(
        topics=topics,
        query="\u6211\u4e0d\u4f1a\u5b9a\u79ef\u5206\u5e94\u7528",
    )

    assert application["topic"]["id"] == "college_integral_application"
    prerequisite_targets = {edge["from"] for edge in application["learning_path"]}
    application_targets = {edge["to"] for edge in application["applications"]}
    assert "college_definite_integral" in prerequisite_targets
    assert {
        "college_integral_area_problem",
        "college_integral_volume_problem",
    }.issubset(application_targets)
    assert any(
        item["kind"] == "prerequisite_probe"
        and item["topic_id"] == "college_definite_integral"
        for item in application["diagnosis_questions"]
    )

    confusion = build_knowledge_guidance_payload(
        topics=topics,
        query="\u4e0d\u5b9a\u79ef\u5206\u548c\u5b9a\u79ef\u5206\u6709\u4ec0\u4e48\u533a\u522b",
    )

    assert confusion["topic"]["id"] in {
        "college_indefinite_integral",
        "college_definite_integral",
    }
    confusion_targets = {
        edge["to"] if edge["from"] == confusion["topic"]["id"] else edge["from"]
        for edge in confusion["confusions"]
    }
    assert {
        "college_indefinite_integral",
        "college_definite_integral",
    } - {confusion["topic"]["id"]} <= confusion_targets
    assert any(
        item["kind"] == "prerequisite_probe"
        and item["topic_id"]
        in {
            "college_indefinite_integral",
            "college_definite_integral",
        }
        - {confusion["topic"]["id"]}
        for item in confusion["diagnosis_questions"]
    )

    substitution = build_knowledge_guidance_payload(
        topics=topics,
        query="\u6362\u5143\u79ef\u5206\u4ec0\u4e48\u65f6\u5019\u7528",
    )

    assert substitution["topic"]["id"] == "college_substitution_integration"
    assert "college_indefinite_integral" in {
        edge["from"] for edge in substitution["learning_path"]
    }


def test_study_knowledge_guidance_real_seed_covers_eigenvalue_procedure() -> None:
    seed_root = Path("plugin/plugins/study_companion/static")
    manifest = json.loads((seed_root / "knowledge_graph_seed.json").read_text(encoding="utf-8"))
    topics = []
    for item in manifest["files"]:
        payload = json.loads((seed_root / item["path"]).read_text(encoding="utf-8"))
        topics.extend(payload.get("topics", []))

    payload = build_knowledge_guidance_payload(
        topics=topics,
        query="\u4e3a\u4ec0\u4e48\u8981\u6c42 det(A-\u03bbI)=0",
    )

    assert payload["topic"]["id"] == "college_characteristic_polynomial"
    prerequisite_targets = {edge["from"] for edge in payload["learning_path"]}
    application_targets = {edge["to"] for edge in payload["applications"]}
    assert "college_determinant" in prerequisite_targets
    assert "college_eigenvalue" in application_targets
    assert any(
        item["kind"] == "prerequisite_probe"
        and item["topic_id"] == "college_determinant"
        for item in payload["diagnosis_questions"]
    )

    overview = build_knowledge_guidance_payload(
        topics=topics,
        query="\u7279\u5f81\u503c\u600e\u4e48\u5b66",
    )

    assert overview["topic"]["id"] == "college_eigenvalue"
    assert "college_diagonalization" in {
        edge["to"] for edge in overview["applications"]
    }
    assert "college_characteristic_polynomial" in {
        edge["from"] for edge in overview["learning_path"]
    }

    diagonalization = build_knowledge_guidance_payload(
        topics=topics,
        query="\u4ec0\u4e48\u65f6\u5019\u77e9\u9635\u53ef\u4ee5\u5bf9\u89d2\u5316",
    )

    assert diagonalization["topic"]["id"] == "college_diagonalization"
    assert {
        "college_eigenvalue",
        "college_linear_independence",
    }.issubset({edge["from"] for edge in diagonalization["learning_path"]})
    assert any(
        item["kind"] == "prerequisite_probe"
        and item["topic_id"] == "college_eigenvalue"
        for item in diagonalization["diagnosis_questions"]
    )

    eigenvector = build_knowledge_guidance_payload(
        topics=topics,
        query="\u7279\u5f81\u503c\u548c\u7279\u5f81\u5411\u91cf\u6709\u4ec0\u4e48\u5173\u7cfb",
    )

    assert eigenvector["topic"]["id"] == "college_eigenvalue"

    singular_value = build_knowledge_guidance_payload(
        topics=topics,
        query="\u7279\u5f81\u503c\u548c\u5947\u5f02\u503c\u6709\u4ec0\u4e48\u533a\u522b",
    )

    assert singular_value["topic"]["id"] in {
        "college_eigenvalue",
        "college_singular_value",
    }
    assert {
        "college_eigenvalue",
        "college_singular_value",
    } - {singular_value["topic"]["id"]} <= {
        edge["to"] if edge["from"] == singular_value["topic"]["id"] else edge["from"]
        for edge in singular_value["confusions"]
    }

    vector = build_knowledge_guidance_payload(
        topics=topics,
        query="\u7279\u5f81\u5411\u91cf\u548c\u666e\u901a\u5411\u91cf\u6709\u4ec0\u4e48\u533a\u522b",
    )

    assert vector["topic"]["id"] in {
        "college_eigenvalue",
        "college_regular_vector",
    }
    assert {
        "college_eigenvalue",
        "college_regular_vector",
    } - {vector["topic"]["id"]} <= {
        edge["to"] if edge["from"] == vector["topic"]["id"] else edge["from"]
        for edge in vector["confusions"]
    }


def test_study_knowledge_guidance_real_seed_covers_probability_confusions() -> None:
    seed_root = Path("plugin/plugins/study_companion/static")
    manifest = json.loads((seed_root / "knowledge_graph_seed.json").read_text(encoding="utf-8"))
    topics = []
    for item in manifest["files"]:
        payload = json.loads((seed_root / item["path"]).read_text(encoding="utf-8"))
        topics.extend(payload.get("topics", []))

    independent = build_knowledge_guidance_payload(
        topics=topics,
        query="\u72ec\u7acb\u548c\u4e92\u65a5\u6709\u4ec0\u4e48\u533a\u522b",
    )

    assert independent["topic"]["id"] in {
        "college_conditional_probability",
        "college_mutually_exclusive_events",
    }
    independent_confusions = {
        edge["to"] if edge["from"] == independent["topic"]["id"] else edge["from"]
        for edge in independent["confusions"]
    }
    assert {
        "college_conditional_probability",
        "college_mutually_exclusive_events",
    } - {independent["topic"]["id"]} <= independent_confusions

    variance = build_knowledge_guidance_payload(
        topics=topics,
        query="\u65b9\u5dee\u4e3a\u4ec0\u4e48\u8981\u5e73\u65b9",
    )

    assert variance["topic"]["id"] == "college_expectation_variance"
    variance_confusions = {
        edge["to"] if edge["from"] == variance["topic"]["id"] else edge["from"]
        for edge in variance["confusions"]
    }
    assert "college_standard_deviation" in variance_confusions
    assert any(
        item["kind"] == "procedure_probe" and item["topic_id"] == "variance"
        for item in variance["diagnosis_questions"]
    )

    expectation = build_knowledge_guidance_payload(
        topics=topics,
        query="\u671f\u671b\u65b9\u5dee\u4e0d\u4f1a",
    )

    assert expectation["topic"]["id"] == "college_expectation_variance"

    mean = build_knowledge_guidance_payload(
        topics=topics,
        query="\u671f\u671b\u548c\u5e73\u5747\u503c\u6709\u4ec0\u4e48\u533a\u522b",
    )

    assert mean["topic"]["id"] in {
        "college_expectation_variance",
        "college_sample_mean",
    }
    assert {
        "college_expectation_variance",
        "college_sample_mean",
    } - {mean["topic"]["id"]} <= {
        edge["to"] if edge["from"] == mean["topic"]["id"] else edge["from"]
        for edge in mean["confusions"]
    }

    inference = build_knowledge_guidance_payload(
        topics=topics,
        query="\u7f6e\u4fe1\u533a\u95f4\u548c\u5047\u8bbe\u68c0\u9a8c\u6709\u4ec0\u4e48\u5173\u7cfb",
    )

    assert inference["topic"]["id"] in {
        "college_confidence_interval",
        "college_hypothesis_testing",
    }
    assert {
        "college_confidence_interval",
        "college_hypothesis_testing",
    } - {inference["topic"]["id"]} <= {
        edge["to"] if edge["from"] == inference["topic"]["id"] else edge["from"]
        for edge in [*inference["confusions"], *inference["next_practice_topics"]]
    }


def test_study_knowledge_seed_contains_college_graph_template_edges() -> None:
    seed_root = Path("plugin/plugins/study_companion/static")
    manifest = json.loads((seed_root / "knowledge_graph_seed.json").read_text(encoding="utf-8"))
    topics = []
    for item in manifest["files"]:
        payload = json.loads((seed_root / item["path"]).read_text(encoding="utf-8"))
        topics.extend(payload.get("topics", []))
    by_id = {topic["id"]: topic for topic in topics}

    required_topic_ids = {
        "college_derivative_existence",
        "college_derivative_rules",
        "college_chain_rule",
        "college_tangent_line_equation",
        "college_convavity_inflection",
        "college_antiderivative",
        "college_rational_function_integration",
        "college_definite_integral_properties",
        "college_variable_upper_limit_integral",
        "college_linear_transformation",
        "college_similar_matrix",
        "college_binomial_distribution",
        "college_normal_distribution",
    }
    assert required_topic_ids <= set(by_id)

    def has_edge(source_id: str, target_id: str, relation: str) -> bool:
        for topic in topics:
            topic_id = topic["id"]
            for edge in topic.get("prerequisites") or []:
                if not isinstance(edge, dict):
                    continue
                if (
                    edge.get("id") == source_id
                    and topic_id == target_id
                    and edge.get("relation") == relation
                ):
                    return True
            for edge in topic.get("related") or []:
                if not isinstance(edge, dict):
                    continue
                if (
                    topic_id == source_id
                    and edge.get("id") == target_id
                    and edge.get("relation") == relation
                ):
                    return True
        return False

    assert has_edge("college_continuity", "college_derivative_existence", "prerequisite")
    assert has_edge("college_derivative_definition", "college_derivative_rules", "prerequisite")
    assert has_edge("college_derivative_rules", "college_chain_rule", "prerequisite")
    assert has_edge("college_derivative_calculation", "college_tangent_line_equation", "application")
    assert has_edge("college_second_derivative_test", "college_convavity_inflection", "application")
    assert has_edge("college_antiderivative", "college_indefinite_integral", "confusable")
    assert has_edge("college_limit_concept", "college_function_value", "confusable")
    assert has_edge("college_continuity", "college_derivative_existence", "confusable")
    assert has_edge("college_stationary_points", "college_extreme_value_points", "confusable")
    assert has_edge("college_eigenvalue", "college_singular_value", "confusable")
    assert has_edge("college_rank", "college_basis_dimension", "confusable")
    assert has_edge("college_permutation", "college_combination", "confusable")
    assert has_edge("college_definite_integral", "college_definite_integral_properties", "supports")
    assert has_edge("college_newton_leibniz", "college_variable_upper_limit_integral", "procedure_step")
    assert has_edge("college_derivative_calculation", "college_critical_points", "procedure_step")
    assert has_edge("college_critical_points", "college_stationary_points", "procedure_step")
    assert has_edge("college_stationary_points", "college_second_derivative_test", "procedure_step")
    assert has_edge("college_integral_application_interval", "college_integral_application_integrand", "procedure_step")
    assert has_edge("college_characteristic_matrix", "college_determinant", "procedure_step")
    assert has_edge("college_determinant", "college_characteristic_polynomial", "procedure_step")
    assert has_edge("college_null_hypothesis", "college_test_statistic", "procedure_step")
    assert has_edge("college_matrix_operations", "college_linear_transformation", "supports")
    assert has_edge("college_similar_matrix", "college_diagonalization", "prerequisite")
    assert has_edge("college_eigenvalue", "college_diagonalization", "application")
    assert has_edge("college_eigenvalue", "college_quadratic_form", "application")
    assert has_edge("college_binomial_distribution", "college_expectation_variance", "application")
    assert has_edge("college_normal_distribution", "college_confidence_interval", "application")
    assert has_edge("college_graph_theory_intro", "college_graph_shortest_path", "application")
    assert has_edge("college_derivative_calculation", "college_physics_kinematics_calculus", "application")
    assert has_edge("college_definite_integral", "college_physics_work_energy_theorem", "application")
    assert has_edge("college_graph_theory_intro", "college_ds_shortest_path", "application")
    assert has_edge("college_matrix_operations", "college_c_2d_array_matrix", "application")
    assert has_edge("college_expectation_variance", "bio_senior_genetics_law", "application")
    assert has_edge("senior_exponential_function", "chem_senior_ph_ionization", "application")


@pytest.mark.asyncio
async def test_study_knowledge_guidance_entry_uses_store_topics(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._store = store

    try:
        store.upsert_topic(
            {
                "id": "derivative",
                "name": "导数",
                "subject": "math",
                "chapter": "微积分",
                "stage": "college",
                "unit": "导数",
                "course_family": "calculus",
                "related": [
                    {
                        "id": "extreme_points",
                        "relation": "application",
                        "reason": "导数用于定位极值候选点。",
                    }
                ],
            }
        )
        store.upsert_topic(
            {
                "id": "extreme_points",
                "name": "求极值点",
                "subject": "math",
                "chapter": "微积分",
                "stage": "college",
                "unit": "导数应用",
                "course_family": "calculus",
                "prerequisites": [
                    {
                        "id": "derivative",
                        "relation": "prerequisite",
                        "reason": "先会求导再判断极值。",
                    }
                ],
            }
        )
        store.upsert_topic(
            {
                "id": "physics_power_extreme",
                "name": "Power extreme value",
                "subject": "physics",
                "chapter": "Electricity",
                "stage": "college",
                "unit": "Circuits",
                "course_family": "physics",
            }
        )

        result = await plugin.study_knowledge_guidance(
            query="求极值点",
            stage="college",
            course_family="calculus",
        )

        assert isinstance(result, Ok)
        assert result.value["topic"]["id"] == "extreme_points"
        assert result.value["topic"]["course_family"] == "calculus"
        assert result.value["learning_path"][0]["from"] == "derivative"
        assert result.value["diagnosis_questions"][0]["kind"] == "prerequisite_probe"
        assert result.value["summary"]["topic_count"] == 2
        assert result.value["summary"]["learning_path_count"] == 1
    finally:
        store.close()


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
        "ui.quickstart.subtitle",
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
        assert "ui.profile.stage.cross_stage" in bundle

    en_bundle = json.loads(
        (plugin_dir / "i18n" / "en.json").read_text(encoding="utf-8")
    )
    for locale in locales:
        assert set(bundles[locale]) == set(en_bundle)
    for locale in [item for item in locales if item != "en"]:
        bundle = bundles[locale]
        assert any(bundle[key] != en_bundle[key] for key in phase3_keys)
    assert bundles["ja"]["entries.open_ui.name"] != en_bundle["entries.open_ui.name"]
    assert bundles["zh-CN"]["ui.profile.stage.cross_stage"] == "跨学段"
    assert bundles["en"]["ui.profile.stage.cross_stage"] == "Cross-stage"
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
    study_panel_surface = next(
        surface for surface in surfaces if surface["id"] == "study-panel"
    )
    assert "action:call" in study_panel_surface["permissions"]
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
    assert not any(surface["id"] == "quickstart" for surface in surfaces)

    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    assert "./i18n.js" in index_html
    assert 'data-i18n="ui.title"' in index_html
    assert "I18n.init" in main_js


def test_study_companion_ui7_surfaces_use_brand_css_and_quickstart_is_removed() -> (
    None
):
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"

    with (plugin_dir / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)
    panel_ids = {surface["id"] for surface in config["plugin"]["ui"]["panel"]}
    assert "quickstart" not in panel_ids
    assert not config["plugin"]["ui"].get("guide")
    assert "study-panel" in panel_ids
    assert "memory-deck-list" in panel_ids

    quickstart_path = plugin_dir / "surfaces" / "quickstart.tsx"
    assert quickstart_path.exists()

    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    assert 'http-equiv="Content-Security-Policy"' in index_html
    assert "style-src 'self'" in index_html
    assert "script-src 'self'" in index_html
    assert "style-src-attr 'unsafe-inline'" in index_html
    assert "connect-src 'self'" in index_html
    assert ":*" not in index_html
    assert "meta CSP cannot express dynamic localhost ports" in index_html

    surface_files = [
        "daily_goal_editor.tsx",
        "due_review_panel.tsx",
        "habit_dashboard.tsx",
        "knowledge_contribution_settings.tsx",
        "knowledge_map.tsx",
        "memory_deck_list.tsx",
        "memory_importer.tsx",
        "note_exporter.tsx",
        "passage_recitation.tsx",
        "pomodoro_panel.tsx",
        "session_summary.tsx",
        "study_panel.tsx",
        "word_review.tsx",
    ]
    for filename in surface_files:
        source = (plugin_dir / "surfaces" / filename).read_text(encoding="utf-8")
        assert "ensureBrandCSS" in source, filename
        assert "ensureBrandCSS();" in source, filename

    surface_utils = (plugin_dir / "surfaces" / "study_surface_utils.ts").read_text(
        encoding="utf-8"
    )
    assert "export const STUDY_SURFACE_MESSAGE_TYPES" in surface_utils
    assert "reviewCompleted: 'neko-study-review-completed'" in surface_utils
    assert "refreshSummary: 'neko-study-refresh-summary'" in surface_utils
    assert "memoryDeckUpdated: 'neko-study-memory-deck-updated'" in surface_utils
    assert "--mastery-mastered: #82d99e;" in surface_utils
    assert ".surface-shell" in surface_utils


def test_study_companion_ui_refactor_static_and_hosted_contracts() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    style_css = (plugin_dir / "static" / "style.css").read_text(encoding="utf-8")
    main_js = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    study_panel = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    surface_utils = (
        plugin_dir / "surfaces" / "study_surface_utils.ts"
    ).read_text(encoding="utf-8")

    assert 'class="page-shell"' in index_html
    assert 'id="mainView"' in index_html
    assert 'class="hero"' in index_html
    assert 'id="modeSwitch"' in index_html
    assert 'class="mode-switch"' in index_html
    assert "mode-strip" not in index_html

    assert "--brand: #2f7d57;" in style_css
    assert "--study-companion: #2f7d57;" in style_css
    assert "body::before" not in style_css
    assert "paw-pattern" not in style_css
    assert "paw-pattern.svg" not in index_html
    assert 'data-i18n-aria-label="ui.memory.ratings_aria" aria-label="Memory ratings"' in index_html
    assert ".mode-switch::before" in style_css
    assert "@keyframes pawBounce" not in style_css
    assert "@media (prefers-reduced-motion: reduce)" in style_css
    assert "clip-path: inset(50%);" in style_css

    assert "const modeSwitch = document.getElementById('modeSwitch');" in main_js
    assert "function updateModeIndicator()" in main_js
    assert "modeSwitch.dataset.active = currentMode" in main_js
    assert "modeSwitch.offsetParent === null" in main_js
    assert "getBoundingClientRect()" in main_js
    assert "modeSwitch.style.setProperty('--indicator-left'" in main_js
    assert "modeSwitch.style.setProperty('--indicator-width'" in main_js
    assert "modeSwitch.setAttribute('data-ready', 'true')" in main_js
    assert "return origin === window.location.origin;" in main_js
    assert "origin === 'null'" not in main_js
    assert "const STUDY_SURFACE_INCOMING_MESSAGE_TYPES = new Set" in main_js
    assert "function isTrustedStudySurfaceMessage(message)" in main_js
    assert "renderSurfaceDrawerBody(surfaceId)" in main_js
    assert "/ui/plugins" not in main_js
    assert "surfaceDrawerFrame" not in main_js
    assert "kind: 'guide'" not in main_js

    assert "export const BRAND_CSS" in surface_utils
    assert "export function ensureBrandCSS()" in surface_utils
    assert "study-companion-brand-css" in surface_utils
    assert "ensureBrandCSS();" in study_panel
    assert 'className="mode-switch study-panel__modes"' in study_panel
    assert 'style={{' not in study_panel

    plugin_ui_frame = (
        Path(__file__).resolve().parents[4]
        / "frontend"
        / "plugin-manager"
        / "src"
        / "components"
        / "plugin"
        / "PluginUIFrame.vue"
    ).read_text(encoding="utf-8")
    plugin_detail = (
        Path(__file__).resolve().parents[4]
        / "frontend"
        / "plugin-manager"
        / "src"
        / "views"
        / "PluginDetail.vue"
    ).read_text(encoding="utf-8")
    assert "neko-study-open-surface" in plugin_ui_frame
    assert "openSurface" in plugin_ui_frame
    assert "payload.pluginId.trim()" in plugin_ui_frame
    assert "payload.kind.trim()" in plugin_ui_frame
    assert "sendStudySurfaceMessage" in plugin_ui_frame
    assert '@open-surface="openHostedSurfaceFromStaticUi"' in plugin_detail
    assert '@message="relayHostedSurfaceMessageToStaticUi"' in plugin_detail
    assert "studySurfaceRelayMessageTypes" in plugin_detail
    assert "staticUiFrameRef.value?.sendStudySurfaceMessage(data)" in plugin_detail
    assert "const preferGuide = payload.kind === 'guide' || payload.kind === 'docs'" in plugin_detail
    assert "activeGuideSurfaceId.value = guide.id" in plugin_detail
    assert "surface: activeSurfaceId" in plugin_detail
    assert "route.query.surface" in plugin_detail


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
const surfacePanelsJs = fs.readFileSync(path.join(staticDir, 'surface-panels.js'), 'utf8');
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
window.eval(surfacePanelsJs);
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

const modeSelect = document.getElementById('modeSelect');
if (!modeSelect || modeSelect.className !== 'sr-only') {
  throw new Error(`screen-reader mode select missing: ${modeSelect && modeSelect.outerHTML}`);
}
if (modeSelect.value !== 'interactive') {
  throw new Error(`mode select not synced after click: ${modeSelect.value}`);
}
modeSelect.value = 'teaching';
modeSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
await waitFor(() => document.getElementById('statusLine').textContent.includes('Teaching'), 'select teaching mode');
if (!runEntries.get('run-mode') || runEntries.get('run-mode').args.mode !== 'teaching') {
  throw new Error(`select mode run args mismatch: ${JSON.stringify(runEntries.get('run-mode'))}`);
}
if (document.querySelector('[data-mode="teaching"]').getAttribute('aria-pressed') !== 'true') {
  throw new Error('teaching mode button not synced after select change');
}

window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '1', altKey: true, bubbles: true }));
await waitFor(() => document.getElementById('statusLine').textContent.includes('Companion'), 'shortcut companion mode');
if (!runEntries.get('run-mode') || runEntries.get('run-mode').args.mode !== 'companion') {
  throw new Error(`shortcut mode run args mismatch: ${JSON.stringify(runEntries.get('run-mode'))}`);
}
if (modeSelect.value !== 'companion' || document.querySelector('[data-mode="companion"]').getAttribute('aria-pressed') !== 'true') {
  throw new Error('mode select and buttons not synced after keyboard shortcut');
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


def test_study_companion_static_ui_loads_zh_cn_copy_at_runtime() -> None:
    if shutil.which("node") is None:
        pytest.skip(
            "node is not installed; frontend/plugin-manager happy-dom smoke test requires node"
        )

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

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const mainJs = fs.readFileSync(path.join(staticDir, 'main.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const zhBundle = JSON.parse(fs.readFileSync(path.join(i18nDir, 'zh-CN.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=zh-CN' });
const { document } = window;
document.write(html);
document.close();

const runEntries = [];
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/zh-CN.json') {
    return Response.json(zhBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    runEntries.push(body);
    return Response.json({ run_id: `run-${runEntries.length}`, status: 'queued' });
  }
  if (/^\/runs\/run-\d+$/.test(url)) {
    return Response.json({ status: 'succeeded' });
  }
  if (/^\/runs\/run-\d+\/export$/.test(url)) {
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data: {
            status: 'ready',
            active_mode: 'companion',
            is_first_run: true,
            dependencies: {
              rapidocr: { available: true },
              dxcam: { available: false },
            },
            knowledge_summary: { topic_count: 4, edge_count: 3 },
            habit: {
              checkin: { checked_in: false },
              pomodoro: { state: 'idle' },
              summary: { total_focus_minutes: 42, completed_goal_count: 2, goal_count: 5 },
            },
            memory_deck: { card_count: 12, due_count: 3, due_cards: [] },
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

await waitFor(() => document.title === zhBundle['ui.title'], 'localized title');
await waitFor(() => document.getElementById('firstRunGuide') && !document.getElementById('firstRunGuide').hidden, 'localized onboarding');

const expectedOcr = zhBundle['ui.settings.ocr.ready_summary']
  .replace('{ready}', '1')
  .replace('{total}', '2');
const expectedKnowledge = zhBundle['ui.settings.knowledge.loaded_summary']
  .replace('{topics}', '4')
  .replace('{edges}', '3');
const expectedMemory = zhBundle['ui.settings.memory.loaded_summary']
  .replace('{cards}', '12')
  .replace('{due}', '3');
const initialOcrSummary = document.getElementById('settingsOcrSummary').textContent.trim();
const initialKnowledgeSummary = document.getElementById('settingsKnowledgeSummary').textContent.trim();
const initialMemorySummary = document.getElementById('settingsMemorySummary').textContent.trim();
window.eval("updateStudySummaries({ habit: { available: true, checkin: { checked_in: true }, pomodoro: { state: 'idle' } } });");
const checkedInStatus = document.getElementById('quickCheckinStatus').textContent.trim();
window.eval("updateStudySummaries({ habit: { available: true, checkin: { checked_in: false }, pomodoro: { state: 'idle' } } });");
const pendingCheckinStatus = document.getElementById('quickCheckinStatus').textContent.trim();

const checks = [
  ['eyebrow', document.querySelector('.hero__eyebrow').textContent, zhBundle['ui.eyebrow']],
  ['advanced button', document.getElementById('advancedToggleBtn').textContent.trim(), zhBundle['ui.button.advanced_settings']],
  ['duration label', document.querySelector('[data-summary-duration-label]').textContent.trim(), zhBundle['ui.label.duration']],
  ['duration value', document.getElementById('summaryDuration').textContent.trim(), '42 min'],
  ['goal label', document.querySelector('[data-summary-goal-label]').textContent.trim(), zhBundle['ui.label.goal']],
  ['goal value', document.getElementById('summaryGoal').textContent.trim(), '2/5'],
  ['checked-in status', checkedInStatus, zhBundle['ui.status.checkin_done']],
  ['pending checkin status', pendingCheckinStatus, zhBundle['ui.status.checkin_pending']],
  ['knowledge tab', document.getElementById('tab-knowledge').textContent.trim(), zhBundle['ui.settings.tab.knowledge']],
  ['first run title', document.getElementById('firstRunTitle').textContent.trim(), zhBundle['ui.onboarding.title']],
  ['OCR summary', initialOcrSummary, expectedOcr],
  ['knowledge summary', initialKnowledgeSummary, expectedKnowledge],
  ['memory summary', initialMemorySummary, expectedMemory],
];
for (const [label, actual, expected] of checks) {
  if (actual !== expected) {
    throw new Error(`${label} mismatch: ${JSON.stringify({ actual, expected })}`);
  }
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


def test_study_companion_static_ui_scan_dom_updates_core_locales() -> None:
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

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const locales = ['zh-CN', 'en', 'ja'];
const bundles = Object.fromEntries(locales.map((locale) => [
  locale,
  JSON.parse(fs.readFileSync(path.join(i18nDir, `${locale}.json`), 'utf8')),
]));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/' });
const { document } = window;
document.write(html);
document.close();
window.eval(i18nJs);

function assertLocalizedAttribute(selector, attr, locale, bundle) {
  const failures = [];
  document.querySelectorAll(selector).forEach((el) => {
    const key = el.getAttribute(selector.slice(1, -1));
    const expected = bundle[key];
    if (typeof expected !== 'string' || !expected) {
      failures.push({ key, reason: 'missing bundle value' });
      return;
    }
    const actual = attr === 'textContent'
      ? el.textContent.trim()
      : el.getAttribute(attr);
    if (actual !== expected) {
      failures.push({ key, actual, expected });
    }
  });
  if (failures.length) {
    throw new Error(`${locale} ${selector} localization mismatches: ${JSON.stringify(failures)}`);
  }
}

for (const locale of locales) {
  const bundle = bundles[locale];
  window.I18n._bundle = bundle;
  window.I18n.setLang(locale);
  window.I18n.scanDOM(document);

  if (document.documentElement.lang !== locale) {
    throw new Error(`${locale} document lang mismatch: ${document.documentElement.lang}`);
  }
  assertLocalizedAttribute('[data-i18n]', 'textContent', locale, bundle);
  assertLocalizedAttribute('[data-i18n-placeholder]', 'placeholder', locale, bundle);
  assertLocalizedAttribute('[data-i18n-aria-label]', 'aria-label', locale, bundle);
  assertLocalizedAttribute('[data-i18n-title]', 'title', locale, bundle);
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


def test_study_companion_static_onboarding_and_advanced_settings_behaviour() -> None:
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

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const mainJs = fs.readFileSync(path.join(staticDir, 'main.js'), 'utf8');
const surfacePanelsJs = fs.readFileSync(path.join(staticDir, 'surface-panels.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(i18nDir, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
const parentMessages = [];
Object.defineProperty(window, 'parent', {
  value: { postMessage: (message) => parentMessages.push(message) },
});
document.write(html);
document.close();
const consoleErrors = [];
window.console.error = (...args) => {
  consoleErrors.push(args.map((arg) => String(arg)).join(' '));
};

const runEntries = [];
const runById = new Map();
let configPayload = {
  plugin_id: 'study_companion',
  config: {
    plugin: { id: 'study_companion', entry: 'plugin.plugins.study_companion:StudyCompanionPlugin' },
    study: { default_mode: 'interactive', language: 'en', history_limit: 50, auto_open_ui: false },
    ocr_reader: { enabled: false, backend_selection: 'rapidocr', languages: 'eng' },
    llm: { llm_call_timeout_seconds: 45, llm_vision_enabled: false, llm_vision_max_image_px: 1024 },
  },
};
let statusPayload = {
  status: 'ready',
  active_mode: 'companion',
  is_first_run: true,
  last_ocr_text: 'old OCR text',
  dependencies: {
    rapidocr: { available: true },
    tesseract: { available: true },
    dxcam: { available: true },
  },
  knowledge_summary: { topic_count: 4, edge_count: 3 },
  habit: { available: true, checkin: { checked_in: false }, pomodoro: { state: 'idle' } },
  memory_deck: { card_count: 12, due_count: 3, due_cards: [] },
};
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = `run-${runEntries.length + 1}`;
    runEntries.push({ runId, ...body });
    runById.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (/^\/runs\/run-\d+$/.test(url)) {
    return Response.json({ status: 'succeeded' });
  }
  if (/^\/runs\/run-\d+\/export$/.test(url)) {
    const runId = url.match(/^\/runs\/(run-\d+)\/export$/)[1];
    const run = runById.get(runId) || {};
    let data = statusPayload;
    if (run.entry_id === 'study_get_settings_config') {
      data = { config: configPayload.config };
    } else if (run.entry_id === 'study_update_settings_config') {
      configPayload = {
        ...configPayload,
        config: run.args.config,
      };
      data = { config: configPayload.config };
    }
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data,
        },
      }],
    });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(surfacePanelsJs);
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

await waitFor(() => document.getElementById('firstRunGuide') && !document.getElementById('firstRunGuide').hidden, 'first run guide');

const guide = document.getElementById('firstRunGuide');
if (document.getElementById('studyInput').value !== '') {
  throw new Error(`study input should not auto-load stale OCR text: ${document.getElementById('studyInput').value}`);
}
const explainRunCountBeforeEmptyInput = runEntries.filter((entry) => entry.entry_id === 'study_explain_text').length;
document.getElementById('explainBtn').click();
await waitFor(
  () => document.getElementById('replyText').textContent.includes('Please enter text or paste an image first.'),
  'empty study input validation',
);
if (runEntries.filter((entry) => entry.entry_id === 'study_explain_text').length !== explainRunCountBeforeEmptyInput) {
  throw new Error(`empty input should not create explain run: ${JSON.stringify(runEntries)}`);
}
if (guide.querySelectorAll('[data-first-run-step]').length !== 3) {
  throw new Error(`expected 3 onboarding steps: ${guide.outerHTML}`);
}
if (!document.getElementById('diagnosisTitle').textContent.trim().startsWith('✓')) {
  throw new Error(`ok diagnosis missing prefix: ${document.getElementById('diagnosisTitle').textContent}`);
}

statusPayload = {
  ...statusPayload,
  is_first_run: false,
  llm: { available: false, message: 'LLM unavailable' },
};
document.getElementById('refreshBtn').click();
await waitFor(
  () => document.getElementById('primaryDiagnosis').dataset.severity === 'error',
  'llm unavailable error diagnosis',
);
if (!document.getElementById('diagnosisTitle').textContent.trim().startsWith('⚠')) {
  throw new Error(`error diagnosis missing prefix: ${document.getElementById('diagnosisTitle').textContent}`);
}
if (!document.getElementById('diagnosisBody').textContent.includes('LLM unavailable')) {
  throw new Error(`llm diagnostic body missing: ${document.getElementById('diagnosisBody').textContent}`);
}
statusPayload = { ...statusPayload, is_first_run: true, llm: { available: true } };
document.getElementById('refreshBtn').click();
await waitFor(
  () => document.getElementById('primaryDiagnosis').dataset.severity === 'ok',
  'recovered ok diagnosis',
);

document.getElementById('firstRunSkipBtn').click();
if (!guide.hidden || guide.dataset.dismissed !== 'true') {
  throw new Error(`first run guide not dismissed: ${guide.outerHTML}`);
}
if (runEntries.some((entry) => String(entry.entry_id).includes('first_run'))) {
  throw new Error(`frontend should not mutate is_first_run flag: ${JSON.stringify(runEntries)}`);
}

document.getElementById('advancedToggleBtn').click();
const advanced = document.getElementById('advancedSettings');
if (advanced.hidden || document.getElementById('advancedToggleBtn').getAttribute('aria-expanded') !== 'true') {
  throw new Error('advanced settings did not expand');
}
if (!guide.hidden) {
  throw new Error('first run guide should remain hidden when advanced settings is expanded');
}
await waitFor(
  () => document.getElementById('settingsDefaultMode') && document.getElementById('settingsDefaultMode').value === 'interactive',
  'advanced settings config load',
);
if (document.getElementById('settingsOcrEnabled').checked !== false) {
  throw new Error('OCR enabled checkbox did not load from config');
}
if (document.getElementById('settingsOcrLanguages').value !== 'eng') {
  throw new Error(`OCR languages did not load from config: ${document.getElementById('settingsOcrLanguages').value}`);
}
if (document.getElementById('settingsLlmTimeout').value !== '45') {
  throw new Error(`LLM timeout did not load from config: ${document.getElementById('settingsLlmTimeout').value}`);
}
if (document.getElementById('settingsLlmVisionEnabled').checked !== false) {
  throw new Error('LLM vision checkbox did not load from config');
}
document.getElementById('settingsDefaultMode').value = 'teaching';
document.getElementById('settingsOcrEnabled').checked = true;
document.getElementById('settingsOcrLanguages').value = 'chi_sim+eng';
document.getElementById('settingsLlmTimeout').value = '90';
document.getElementById('settingsLlmVisionEnabled').checked = true;
document.getElementById('settingsSaveBtn').click();
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_update_settings_config'),
  'advanced settings config save',
);
const savedConfig = runEntries.find((entry) => entry.entry_id === 'study_update_settings_config').args.config;
if (
  savedConfig.study.default_mode !== 'teaching'
  || savedConfig.study.auto_open_ui !== false
  || savedConfig.ocr_reader.enabled !== true
  || savedConfig.ocr_reader.languages !== 'chi_sim+eng'
  || savedConfig.llm.llm_call_timeout_seconds !== 90
  || savedConfig.llm.llm_vision_enabled !== true
  || savedConfig.llm.llm_vision_max_image_px !== 1024
  || savedConfig.plugin.id !== 'study_companion'
) {
  throw new Error(`settings save payload mismatch: ${JSON.stringify(savedConfig)}`);
}
await waitFor(
  () => document.getElementById('settingsConfigStatus').textContent.includes('Saved'),
  'settings saved status',
);

const memoryTab = document.getElementById('tab-memory');
memoryTab.click();
if (memoryTab.getAttribute('aria-selected') !== 'true' || document.getElementById('panel-memory').hidden) {
  throw new Error('memory settings tab did not activate');
}
memoryTab.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
if (document.getElementById('tab-study').getAttribute('aria-selected') !== 'true') {
  throw new Error('Home key did not activate first settings tab');
}
document.getElementById('tab-study').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
if (document.getElementById('tab-data').getAttribute('aria-selected') !== 'true') {
  throw new Error('End key did not activate last settings tab');
}
document.getElementById('tab-data').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
if (document.activeElement !== document.getElementById('panel-data')) {
  throw new Error(`Tab key did not move focus into active panel: ${document.activeElement && document.activeElement.id}`);
}

function assertSurfaceDrawer(surfaceId) {
  const drawer = document.getElementById('surfaceDrawer');
  const body = document.getElementById('surfaceDrawerBody');
  if (drawer.dataset.open !== 'true') {
    throw new Error(`surface drawer did not open for ${surfaceId}`);
  }
  if (drawer.dataset.surfaceId !== surfaceId) {
    throw new Error(`surface drawer opened ${drawer.dataset.surfaceId} instead of ${surfaceId}`);
  }
  if (document.getElementById('surfaceDrawerFrame')) {
    throw new Error('surface drawer should not render an iframe');
  }
  if (!body || !body.textContent.trim()) {
    throw new Error(`surface drawer local body missing for ${surfaceId}`);
  }
  if (body.innerHTML.includes('/ui/plugins')) {
    throw new Error(`surface drawer leaked plugin manager URL for ${surfaceId}: ${body.innerHTML}`);
  }
}

document.querySelector('#panel-memory [data-open-surface="due-review-panel"]').click();
assertSurfaceDrawer('due-review-panel');

for (const surfaceId of ['memory-importer', 'habit-dashboard', 'pomodoro-panel', 'daily-goal-editor']) {
  document.querySelector(`#advancedSettings [data-open-surface="${surfaceId}"]`).click();
  assertSurfaceDrawer(surfaceId);
}

for (const surfaceId of ['pomodoro-panel', 'due-review-panel', 'habit-dashboard']) {
  document.querySelector(`.quick-panels [data-open-surface="${surfaceId}"]`).click();
  assertSurfaceDrawer(surfaceId);
}
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_memory_due_reviews'),
  'due review drawer load',
);
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_pomodoro_status'),
  'pomodoro drawer load',
);
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_checkin_status')
    && runEntries.some((entry) => entry.entry_id === 'study_goals'),
  'habit drawer load',
);

document.querySelector('[data-feature-action="knowledge"]').click();
assertSurfaceDrawer('knowledge-map');
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_knowledge_map'),
  'knowledge map drawer load',
);

document.querySelector('[data-feature-action="memory"]').click();
assertSurfaceDrawer('memory-deck-list');
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_memory_list_decks'),
  'memory deck drawer load',
);

document.querySelector('[data-feature-action="export"]').click();
assertSurfaceDrawer('note-exporter');
document.querySelector('#surfaceDrawerBody [data-surface-action="export-preview"]').click();
await waitFor(
  () => runEntries.some((entry) => entry.entry_id === 'study_export_notes' && entry.args.fmt),
  'export drawer preview',
);

const statusRunCountBeforeMessage = runEntries.filter((entry) => entry.entry_id === 'study_status').length;
window.dispatchEvent(new window.MessageEvent('message', {
  origin: window.location.origin,
  data: {
    type: 'neko-study-review-completed',
    payload: {
      deck_id: 'deck-1',
      remaining: 2,
      reviewed_count: 1,
      timestamp: Date.now(),
    },
  },
}));
await waitFor(
  () => runEntries.filter((entry) => entry.entry_id === 'study_status').length > statusRunCountBeforeMessage,
  'review completed status refresh',
);

if (consoleErrors.length) {
  throw new Error(`unexpected console errors: ${JSON.stringify(consoleErrors)}`);
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
    assert "study_explain_text: 310000" in source
    assert "study_generate_question: 310000" in source
    assert "study_evaluate_answer: 310000" in source
    assert "callPlugin as callHostedPlugin" in source
    assert (
        "return callHostedPlugin<T>(api, entryId, args, { signal, timeoutMs: timeoutForEntry(entryId) });"
        in source
    )
    assert "fetch('/runs'" not in source
    assert "fetch(`/runs/" not in source
    assert "for (let i = 0; i < 40; i += 1)" not in source
    assert (
        "async function refresh(signal?: AbortSignal, _options: { updateReply?: boolean } = {})"
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
    assert "status: textImage ? 'solving_problem' : 'explaining'" in source
    assert (
        "setReply(textImage ? t('ui.status.solving_problem', 'Solving problem...') : t('ui.status.explaining', 'Explaining...'));"
        in source
    )
    assert "const replySectionRef = useRef<HTMLDivElement | null>(null);" in source
    assert "function scrollReplyIntoView()" in source
    assert "replySectionRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });" in source
    assert "scrollReplyIntoView();" in source


def test_study_companion_hosted_surface_actions_are_bridge_authorized() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    entry_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(plugin_dir.glob("entry_*.py"))
    )
    assert re.search(
        r"@ui\.context\(id=[\"']study[\"']",
        entry_sources,
        re.MULTILINE,
    )

    with (plugin_dir / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)
    for surface in config["plugin"]["ui"]["panel"]:
        assert surface["context"] == "study", surface["id"]
        assert "action:call" in surface["permissions"], surface["id"]

    for action_id in HOSTED_SURFACE_ACTION_IDS:
        assert re.search(
            rf"@ui\.action\([^)]*\)\s+@plugin_entry\(\s+id=[\"']{re.escape(action_id)}[\"']",
            entry_sources,
            re.MULTILINE,
        ), action_id

    export_source = (plugin_dir / "entry_export_support.py").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"@ui\.action\([^)]*\)\s+async def _study_export_notes_entry",
        export_source,
        re.MULTILINE,
    )


def test_study_companion_hosted_panel_supports_image_paste_contract() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    css_source = (plugin_dir / "static" / "style.css").read_text(encoding="utf-8")

    assert "DEFAULT_VISION_MAX_IMAGE_PX = 768" in source
    assert "function normalizeVisionMaxImagePx(value: unknown)" in source
    assert "maxImagePx = DEFAULT_VISION_MAX_IMAGE_PX" in source
    assert "getMaxImagePx?: () => number;" in source
    assert "getMaxImagePx: getVisionMaxImagePx," in source
    assert (
        "visionMaxImagePxRef.current = normalizeVisionMaxImagePx(\n"
        "      data.config?.llm_vision_max_image_px,\n"
        "    );"
        in source
    )
    assert "if (data.config?.llm_vision_max_image_px !== undefined)" not in source
    assert "MAX_PASTE_IMAGE_LONG_SIDE" not in source
    assert "async function compressImageForStudy(" in source
    assert "const LOAD_IMAGE_TIMEOUT_MS = 30000;" in source
    assert "const TARGET_DATA_URL_LENGTH = 1_000_000;" in source
    assert "Promise.race" in source
    assert "Image load timeout" in source
    assert "图片加载超时" not in source
    assert "Canvas 2D context is unavailable" in source
    assert "readAsDataUrl" not in source
    assert "function createPasteHandler(" in source
    assert "if (getBusy()) return;" in source
    assert "item.type.startsWith('image/')" in source
    assert "SUPPORTED_PASTE_IMAGE_TYPES.has(item.type)" in source
    assert "item.type === 'text/plain'" in source
    assert "setPasteError" in source
    assert "setPastePending?: (value: boolean) => void;" in source
    assert "setters.setPastePending?.(true);" in source
    assert "setters.setPastePending?.(false);" in source
    assert "onImageAccepted?: () => void;" in source
    assert "setters.onImageAccepted?.();" in source
    assert "study-panel__paste-error" in source
    assert "beginPasteSignal" in source
    assert "signal.aborted" in source
    assert "onPaste={handleTextPaste}" in source
    assert "onPaste={handleAnswerPaste}" in source
    assert "const [pastePending, setPastePending] = useState(false);" in source
    assert "pastePendingRef.current = value;" in source
    assert "const interactionBusy = busy || pastePending;" in source
    assert "return busy || pastePendingRef.current;" in source
    assert "readOnly={interactionBusy}" in source
    assert "if (textImage) explainArgs.vision_image_base64 = textImage;" in source
    assert "status: textImage ? 'solving_problem' : 'explaining'" in source
    assert "study_generate_targeted_question" in source
    assert "selection_context_id: context.selection_context_id" in source
    assert "genArgs.vision_image_base64 = textImage" not in source
    assert "ui.error.missing_study_input" in source
    assert "if (!answer.trim() && !answerImage)" in source
    assert "if (answerImage) evalArgs.vision_image_base64 = answerImage;" in source
    assert "const textImageRef = useRef('');" in source
    assert "textImageRef.current = value;" in source
    assert "textAutoFilledFromOcrRef" not in source
    assert "data.last_ocr_text" in source
    assert "return data.last_ocr_text;" not in source
    assert "data.current_question" not in source
    assert "setQuestion(data.current_question?.question || '')" not in source
    assert "status.current_question?.question" not in source
    assert "setPastePending: setPastePendingState," in source
    assert "onImageAccepted: clearAutoFilledTextOnImagePaste," not in source
    assert "setTextImageValue('');" in source
    assert "setAnswerImage('');" in source
    assert 'data-busy={interactionBusy ? "true" : "false"}' in source
    assert "disabled={interactionBusy}" in source
    assert "study-panel__image-preview" in source
    assert "study-panel__image-remove" in source
    assert "warnInDev" in source
    assert '.study-panel[data-busy="true"] .study-panel__image-remove' in css_source
    assert ".study-panel__paste-error" in css_source


def test_study_companion_static_ui_supports_image_paste_contract() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    html_source = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    css_source = (plugin_dir / "static" / "style.css").read_text(encoding="utf-8")

    assert 'id="studyInputImagePreview"' in html_source
    assert 'id="answerInputImagePreview"' in html_source
    assert 'data-i18n-aria-label="ui.label.remove_pasted_image"' in html_source
    assert 'data-i18n-aria-label="ui.label.remove_pasted_answer_image"' in html_source
    assert 'aria-label="Remove pasted image"' not in html_source
    assert 'aria-label="Remove pasted answer image"' not in html_source
    assert 'id="studyInputPasteError"' in html_source
    assert 'id="answerInputPasteError"' in html_source
    assert "img-src 'self' data: blob:" in html_source
    assert "const SUPPORTED_PASTE_IMAGE_TYPES = new Set(['image/png', 'image/jpeg']);" in source
    assert "const LOAD_IMAGE_TIMEOUT_MS = 30000;" in source
    assert "const TARGET_DATA_URL_LENGTH = 1000000;" in source
    assert "const DEFAULT_VISION_MAX_IMAGE_PX = 768;" in source
    assert "let llmVisionMaxImagePx = DEFAULT_VISION_MAX_IMAGE_PX;" in source
    assert "function normalizeVisionMaxImagePx(value)" in source
    assert "function applyRuntimeConfig(data)" in source
    assert "applyVisionMaxImagePx(llm.llm_vision_max_image_px);" in source
    assert "llmVisionMaxImagePx / Math.max(sourceWidth, sourceHeight)" in source
    assert "768 / Math.max(sourceWidth, sourceHeight)" not in source
    assert "return url.length > TARGET_DATA_URL_LENGTH ? null : url;" in source
    assert "const pasteControllers = { study: null, answer: null };" in source
    assert "async function compressImageForStudy(blob, signal)" in source
    assert "function createImagePasteHandler(options)" in source
    assert "pasteControllers[kind]?.abort();" in source
    assert "pasteControllers[kind] = controller;" in source
    assert "if (controller.signal.aborted)" in source
    assert "if (pasteControllers[kind] === controller)" in source
    assert "event.clipboardData?.items" in source
    assert "item.type.startsWith('image/')" in source
    assert "SUPPORTED_PASTE_IMAGE_TYPES.has(item.type)" in source
    assert "item.type === 'text/plain'" in source
    assert "setImagePreview(kind, image);" in source
    assert "studyInput.addEventListener('paste', createImagePasteHandler({" in source
    assert "answerInput.addEventListener('paste', createImagePasteHandler({" in source
    assert "args.vision_image_base64 = studyInputImageValue;" in source
    assert "t('ui.status.solving_problem'" in source
    assert (
        "setReply(studyInputImageValue ? t('ui.status.solving_problem', 'Solving problem...') : t('ui.status.explaining', 'Explaining...'));"
        in source
    )
    assert "function scrollReplyIntoView()" in source
    assert "replyPanel.scrollIntoView({ block: 'start', behavior: 'smooth' });" in source
    assert "scrollReplyIntoView();" in source
    assert "args.vision_image_base64 = answerInputImageValue;" in source
    assert "if (!answer && !answerInputImageValue)" in source
    assert ".main-view[data-busy=\"true\"] .study-panel__image-remove" in css_source
    assert "studyInput.value = data.last_ocr_text;" not in source
    assert "data.current_question" not in source
    assert "questionText.textContent = currentQuestion.question || '';" not in source
    assert "ui.error.missing_study_input" in source


def test_study_companion_explain_timeouts_cover_vision_solving() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    hosted_source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    explain_source = (plugin_dir / "entry_tutor_explain_entries.py").read_text(encoding="utf-8")
    question_source = (plugin_dir / "entry_tutor_question_entries.py").read_text(encoding="utf-8")
    answer_source = (plugin_dir / "entry_tutor_answer_entries.py").read_text(encoding="utf-8")
    plugin_toml = (plugin_dir / "plugin.toml").read_text(encoding="utf-8")
    submit_meta = getattr(StudyCompanionPlugin.study_submit_image, EVENT_META_ATTR)
    meta = getattr(StudyCompanionPlugin.study_explain_text, EVENT_META_ATTR)
    question_meta = getattr(StudyCompanionPlugin.study_generate_question, EVENT_META_ATTR)
    answer_meta = getattr(StudyCompanionPlugin.study_evaluate_answer, EVENT_META_ATTR)
    with (plugin_dir / "plugin.toml").open("rb") as handle:
        plugin_config = tomllib.load(handle)
    llm_timeout = float(plugin_config["llm"]["llm_call_timeout_seconds"])

    assert "study_explain_text: 310000" in static_source
    assert "study_generate_question: 310000" in static_source
    assert "study_evaluate_answer: 310000" in static_source
    assert "study_explain_text: 310000" in hosted_source
    assert "study_generate_question: 310000" in hosted_source
    assert "study_evaluate_answer: 310000" in hosted_source
    assert "timeout=310.0" in explain_source
    assert "timeout=310.0" in question_source
    assert "timeout=310.0" in answer_source
    assert submit_meta.timeout == 310.0
    assert meta.timeout == 310.0
    assert question_meta.timeout == 310.0
    assert answer_meta.timeout == 310.0
    assert "llm_call_timeout_seconds = 300" in plugin_toml
    for entry_timeout in (
        submit_meta.timeout,
        meta.timeout,
        question_meta.timeout,
        answer_meta.timeout,
    ):
        assert entry_timeout > llm_timeout + 0.5


def test_study_companion_static_knowledge_map_groups_by_base_library_hierarchy() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    assert "knowledge-subject-group" in source
    assert "knowledgeMapSubject" in source
    assert "renderKnowledgeSubjectSelector" in source
    assert "KNOWLEDGE_SUBJECT_OPTIONS" in source
    assert "'math'" in source
    assert "'computer_science'" in source
    assert "ui.knowledge.subject.${normalized}" in source
    assert "study_knowledge_map', { limit: 1000, stage: normalizedStage }" in source
    assert "study_knowledge_map', { limit: 1000, subject:" not in source
    assert "stage: normalizedStage" in source
    assert "const knownSubjects = KNOWLEDGE_SUBJECT_OPTIONS.filter((subject) => counts.has(subject));" in source
    assert "knowledgeMapSubject = ''" in source
    assert "ui.knowledge.subject_label" in source
    assert "ui.knowledge.subject_all" in source
    assert "visibleKnowledgeNodes(nodes, activeStage, activeSubject)" in source
    assert "visibleKnowledgeEdges(edges, shownNodes, activeStage)" in source
    assert "knowledge-chapter-group" in source
    assert "knowledge-unit-group" in source
    assert "drawerElement('details', className)" in source
    assert "knowledge-group-summary" in source
    assert "knowledgeSubjectLabel(subject)} / ${subjectItems.length}" in source
    assert "chapterGroups.size === 1 && chapterItems.length <= 12" in source
    assert "unitGroups.size === 1 && unitItems.length <= 8" in source
    assert "subjectValueFromNode(node)" in source
    assert "valueLabel(node.chapter" in source
    assert "valueLabel(node.unit" in source


def test_study_companion_note_exporter_uses_backend_export_poll_budget() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "note_exporter.tsx").read_text(encoding="utf-8")

    assert "DEFAULT_EXPORT_TIMEOUT_MS = 80_000" in source
    assert "POLL_TIMEOUT_BUFFER_MS = 5_000" in source
    assert "const timeoutSeconds = Number(entry?.timeout);" in source
    assert "return timeoutSeconds * 1000 + POLL_TIMEOUT_BUFFER_MS;" in source
    assert "pollTimeoutMs = getEntryTimeoutMs(exportEntry)" in source
    assert "{ timeoutMs: pollTimeoutMs }" in source
    assert "fetch('/runs'" not in source
    assert "fetch(`/runs/" not in source
    assert "for (let attempt = 0; attempt < 40; attempt += 1)" not in source
    assert "for (let i = 0; i < 40; i += 1)" not in source


def test_study_companion_ui_export_failures_are_not_silent_successes() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted_source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    assert "callPlugin as callHostedPlugin" in hosted_source
    assert "RUN_EXPORT_RETRY_COUNT = 3" not in hosted_source
    assert "throw new Error(`Run export failed: HTTP ${lastStatus}`);" not in hosted_source
    assert "fetch('/runs'" not in hosted_source
    assert "fetch(`/runs/" not in hosted_source
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
    assert "answerInput.value = data.answer;" not in static_source
    assert "study_generate_targeted_question" in static_source
    assert "question_id: currentQuestion.question_id" in static_source
    assert "attempt_id: currentQuestion.attempt_id" in static_source


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
    assert "throw new Error(pluginErrorMessage(payload.error || payload.message))" in source


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
  process.exit(0);
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
        # Windows Actions runners can briefly starve plain Node subprocesses
        # while the full plugin suite is cleaning up browser-heavy tests.
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_companion_i18n_scan_dom_localizes_alt_attributes() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.env.STUDY_COMPANION_I18N_JS, 'utf8');

globalThis.window = globalThis;
globalThis.document = { documentElement: { lang: '' } };
globalThis.console = console;

eval(source);

function element(attrs) {
  return {
    attrs: { ...attrs },
    textContent: '',
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
  };
}

const image = element({
  'data-i18n-alt': 'ui.coach.sprite_alt',
  alt: 'Neko study companion',
});
const root = {
  querySelectorAll(selector) {
    return selector === '[data-i18n-alt]' ? [image] : [];
  },
};

window.I18n._bundle = {
  'ui.coach.sprite_alt': 'Localized companion alt',
};
window.I18n.scanDOM(root);

if (image.getAttribute('alt') !== 'Localized companion alt') {
  throw new Error(`unexpected alt: ${image.getAttribute('alt')}`);
}
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
        timeout=45,
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
            "study": {"language": "en", "auto_open_ui": False},
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
            "study": {"language": "en", "auto_open_ui": False},
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
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
async def test_targeted_question_context_generate_and_attempt_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _TargetedTutorAgent(_FakeTutorAgent):
        async def question_generate(
            self,
            text: str,
            *,
            mode: str = MODE_COMPANION,
            context: dict[str, object] | None = None,
        ) -> TutorReply:
            self.inputs.append((text, dict(context or {}), mode))
            return TutorReply(
                operation="question_generate",
                input_text=text,
                reply="practice question",
                payload={
                    "question": "What is the derivative of x^2?",
                    "answer": "2x",
                    "reference_answer": "2x",
                    "accepted_answers": ["2 x"],
                    "key_points": ["power rule", "coefficient"],
                    "rubric": {"power rule": 70, "coefficient": 30},
                    "solution_steps": ["Apply the power rule", "Reduce exponent"],
                    "hint": "Use the power rule.",
                    "difficulty": 2,
                    "topic": "power rule",
                    "question_type": "math_exact",
                },
                created_at="2026-05-11T00:00:00Z",
            )

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _TargetedTutorAgent()

    try:
        empty_context = await plugin.study_question_context()
        assert isinstance(empty_context, Ok)
        assert empty_context.value["selection_reason"] == "no_data"
        assert not empty_context.value["selection_context_id"]

        retry_selection = plugin._selection_from_question_params(
            {
                "retry_wrong_question": {
                    "id": 7,
                    "topic_id": "derivatives",
                    "error_type": "missing_power_rule",
                    "verdict": "partial",
                    "user_answer": "x",
                    "expected_answer": "2x",
                }
            }
        )
        wrong_question_summary = retry_selection["selection_reason_payload"][
            "wrong_question"
        ]
        assert wrong_question_summary == {
            "id": 7,
            "topic_id": "derivatives",
            "error_type": "missing_power_rule",
            "verdict": "partial",
        }
        assert "user_answer" not in wrong_question_summary
        assert "expected_answer" not in wrong_question_summary

        plugin._store.ensure_topic(topic_id="derivatives", name="Derivatives")
        plugin._store.append_mastery_snapshot(
            {
                "topic_id": "derivatives",
                "mastery": 0.2,
                "accuracy": 0.2,
                "recency": 1.0,
                "consistency": 0.5,
                "confidence": 0.8,
                "level": "weak",
                "attempts": 2,
                "flags": [],
            }
        )

        context_result = await plugin.study_question_context()
        assert isinstance(context_result, Ok)
        selection_context_id = context_result.value["selection_context_id"]
        assert selection_context_id
        assert context_result.value["selection_reason"] == "weak_topic"

        generated = await plugin.study_generate_targeted_question(
            selection_context_id=selection_context_id
        )
        assert isinstance(generated, Ok)
        for private_key in (
            "answer",
            "reference_answer",
            "accepted_answers",
            "key_points",
            "rubric",
            "solution_steps",
            "internal_private_payload",
        ):
            assert private_key not in generated.value
        assert generated.value["question_id"]
        assert generated.value["attempt_id"]
        assert generated.value["selected_topic_id"] == "derivatives"
        assert generated.value["topic"] == "derivatives"

        consumed_context = await plugin.study_generate_targeted_question(
            selection_context_id=selection_context_id
        )
        assert isinstance(consumed_context, Err)
        assert consumed_context.error.code == "SELECTION_CONTEXT_EXPIRED"
        assert len(plugin._agent.inputs) == 1

        async with plugin._lock:
            current_question = dict(plugin._state.current_question)
            public_question = public_current_question_payload(current_question)
        assert current_question["answer"] == "2x"
        assert current_question["accepted_answers"] == ["2 x"]
        assert current_question["topic"] == "derivatives"
        assert public_question["question"] == "What is the derivative of x^2?"
        assert "answer" not in public_question
        assert "accepted_answers" not in public_question
        persisted_question = plugin._store.load_state(
            build_initial_state()
        ).current_question
        assert persisted_question["answer"] == "2x"
        assert persisted_question["accepted_answers"] == ["2 x"]

        missing_identity = await plugin.study_evaluate_answer(answer="2x")
        assert isinstance(missing_identity, Err)
        assert missing_identity.error.code == "QUESTION_MISMATCH"

        supplied_same_question_without_identity = await plugin.study_evaluate_answer(
            answer="2x",
            question=generated.value["question"],
        )
        assert isinstance(supplied_same_question_without_identity, Err)
        assert supplied_same_question_without_identity.error.code == "QUESTION_MISMATCH"

        async with plugin._lock:
            plugin._state.current_question["attempt_evaluation_pending"] = True
        pending = await plugin.study_evaluate_answer(
            answer="2x",
            question_id=generated.value["question_id"],
            attempt_id=generated.value["attempt_id"],
            selected_topic_id=generated.value["selected_topic_id"],
        )
        assert isinstance(pending, Err)
        assert pending.error.code == "ATTEMPT_ALREADY_EVALUATED"
        async with plugin._lock:
            plugin._state.current_question.pop("attempt_evaluation_pending", None)

        evaluated = await plugin.study_evaluate_answer(
            answer="2x",
            question_id=generated.value["question_id"],
            attempt_id=generated.value["attempt_id"],
            selected_topic_id=generated.value["selected_topic_id"],
        )
        assert isinstance(evaluated, Ok)
        assert evaluated.value["question_id"] == generated.value["question_id"]

        duplicate = await plugin.study_evaluate_answer(
            answer="2x",
            question_id=generated.value["question_id"],
            attempt_id=generated.value["attempt_id"],
            selected_topic_id=generated.value["selected_topic_id"],
        )
        assert isinstance(duplicate, Err)
        assert duplicate.error.code == "ATTEMPT_ALREADY_EVALUATED"
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
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
async def test_learning_context_includes_knowledge_graph_guidance_for_explain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        plugin._store.upsert_topic(
            {
                "id": "derivative",
                "name": "\u5bfc\u6570",
                "subject": "math",
                "stage": "college",
                "course_family": "calculus",
                "related": [
                    {
                        "id": "extrema",
                        "relation": "application",
                        "reason": "\u5bfc\u6570\u7528\u4e8e\u5224\u65ad\u6781\u503c\u5019\u9009\u70b9\u3002",
                    }
                ],
            }
        )
        plugin._store.upsert_topic(
            {
                "id": "extrema",
                "name": "\u6c42\u6781\u503c\u70b9",
                "subject": "math",
                "stage": "college",
                "course_family": "calculus",
                "aliases": ["\u6781\u503c\u70b9\u5224\u65ad"],
                "prerequisites": [
                    {
                        "id": "derivative",
                        "relation": "prerequisite",
                        "reason": "\u6c42\u6781\u503c\u70b9\u524d\u8981\u5148\u80fd\u6c42\u5bfc\u3002",
                    }
                ],
            }
        )

        context = await plugin._build_learning_context(
            "concept_explain",
            input_text="\u6211\u4e0d\u4f1a\u6c42\u6781\u503c\u70b9",
            extra={
                "source_text": "\u6211\u4e0d\u4f1a\u6c42\u6781\u503c\u70b9",
                "selected_topic_id": "extrema",
            },
        )

        guidance = context["knowledge_guidance"]
        assert guidance["topic"]["id"] == "extrema"
        assert guidance["learning_path"][0]["from"] == "derivative"
        assert guidance["diagnosis_questions"][0]["kind"] == "prerequisite_probe"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_non_concept_learning_context_uses_compact_knowledge_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        context = await plugin._build_learning_context(
            "question_generate",
            input_text="\u5bfc\u6570\u4e3a0\u4e3a\u4ec0\u4e48\u4e0d\u662f\u6781\u503c\u70b9",
            extra={"selected_topic_id": "college_stationary_points"},
        )

        guidance = context["knowledge_guidance"]
        assert guidance["focus"]["id"] == "college_stationary_points"
        assert "relevant_subgraph" not in guidance
        assert "diagnosis_questions" not in guidance
        assert "learning_path" not in guidance

        public_guidance = getattr(context, "public_knowledge_guidance")
        assert public_guidance["topic"]["id"] == "college_stationary_points"
        assert public_guidance["diagnosis_questions"]
        assert "relevant_subgraph" in public_guidance
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_evaluate_answer_prompt_uses_compact_guidance_but_public_payload_is_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
        evaluated = await plugin.study_evaluate_answer(
            question="\u5bfc\u6570\u4e3a0\u4e3a\u4ec0\u4e48\u4e0d\u662f\u6781\u503c\u70b9\uff1f",
            expected_answer="\u8fd8\u8981\u68c0\u67e5\u5355\u8c03\u6027\u662f\u5426\u53d8\u5316\u3002",
            answer="\u9700\u8981\u770b\u5355\u8c03\u6027\u53d8\u5316\u3002",
            selected_topic_id="college_stationary_points",
        )

        assert isinstance(evaluated, Ok)
        prompt_context = fake_agent.evaluations[-1][3]
        prompt_guidance = prompt_context["knowledge_guidance"]
        assert prompt_guidance["focus"]["id"] == "college_stationary_points"
        assert "relevant_subgraph" not in prompt_guidance
        assert "diagnosis_questions" not in prompt_guidance
        assert "learning_path" not in prompt_guidance

        public_guidance = evaluated.value["knowledge_guidance"]
        assert public_guidance["topic"]["id"] == "college_stationary_points"
        assert public_guidance["diagnosis_questions"]
        assert "relevant_subgraph" in public_guidance
        assert evaluated.value["diagnosis_questions"] == public_guidance[
            "diagnosis_questions"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_passes_knowledge_guidance_to_tutor_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
        explained = await plugin.study_explain_text(
            "\u5bfc\u6570\u4e3a0\u4e3a\u4ec0\u4e48\u4e0d\u662f\u6781\u503c\u70b9"
        )

        assert isinstance(explained, Ok)
        context = fake_agent.inputs[-1][1]
        guidance = context["knowledge_guidance"]  # type: ignore[index]
        assert guidance["topic"]["id"] == "college_stationary_points"  # type: ignore[index]
        assert guidance["diagnosis_questions"]  # type: ignore[index]
        assert explained.value["knowledge_guidance"]["topic"]["id"] == "college_stationary_points"
        assert explained.value["diagnosis_questions"]
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
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "zh-CN", "default_mode": MODE_TEACHING, "auto_open_ui": False},
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
    guided_messages = build_concept_explain_messages(
        text="\u6211\u4e0d\u4f1a\u6c42\u6781\u503c\u70b9",
        language="zh-CN",
        mode=MODE_COMPANION,
        context={
            "source": "unit-test",
            "mode": MODE_COMPANION,
            "knowledge_guidance": {
                "topic": {"id": "extrema", "label": "\u6c42\u6781\u503c\u70b9"},
                "diagnosis_questions": [
                    {
                        "kind": "prerequisite_probe",
                        "question": "\u4f60\u662f\u4e0d\u4f1a\u6c42\u5bfc\u5417\uff1f",
                    }
                ],
            },
        },
    )
    assert "Knowledge graph guidance" in guided_messages[1]["content"]
    assert "\u6c42\u6781\u503c\u70b9" in guided_messages[1]["content"]
    assert "\u4f60\u662f\u4e0d\u4f1a\u6c42\u5bfc\u5417" in guided_messages[1]["content"]
    compact_guided_messages = build_concept_explain_messages(
        text="\u6211\u4e0d\u4f1a\u6c42\u6781\u503c\u70b9",
        language="zh-CN",
        mode=MODE_COMPANION,
        context={
            "source": "unit-test",
            "mode": MODE_COMPANION,
            "knowledge_guidance": {
                "topic": {"id": "extrema", "label": "\u6c42\u6781\u503c\u70b9"},
                "learning_path": [{"from": "derivative", "to": "extrema"}],
                "model_context": {
                    "focus": {"id": "extrema", "label": "\u6c42\u6781\u503c\u70b9"},
                    "prerequisites": ["\u5bfc\u6570"],
                    "procedure": ["\u6c42\u5bfc"],
                    "summary": {"raw_seed_included": False},
                },
            },
        },
    )
    compact_content = compact_guided_messages[1]["content"]
    assert "\u6c42\u6781\u503c\u70b9" in compact_content
    assert "\u5bfc\u6570" in compact_content
    assert "learning_path" not in compact_content
    assert "derivative" not in compact_content

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
            "study": {"language": "en", "auto_open_ui": False},
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
    assert "study_knowledge_guidance" in entries
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
            "study": {"language": "en", "auto_open_ui": False},
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
            "study": {"language": "en", "auto_open_ui": False},
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
async def test_shutdown_stops_event_bus_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "auto_open_ui": False},
            "study_companion": {"communication": {"enabled": True}},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    assert plugin._event_bus is not None
    bus = plugin._event_bus

    task = bus.schedule_emit(
        StudyEvent(
            name="session_summarized",
            payload={"duration_minutes": 1, "questions_attempted": 1},
        )
    )
    assert task is not None

    await plugin.shutdown()

    assert bus._worker_task is None
    assert task.done()


@pytest.mark.asyncio
async def test_shutdown_clears_ocr_pipeline_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))

    class _FailingClosePipeline:
        def close(self) -> None:
            raise RuntimeError("ocr close failed")

    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin.logger = ctx.logger
    plugin._ocr_pipeline = _FailingClosePipeline()  # type: ignore[assignment]

    shutdown_result = await plugin.shutdown()

    assert isinstance(shutdown_result, Ok)
    assert plugin._ocr_pipeline is None
    assert any(
        "study shutdown OCR pipeline cleanup failed" in str(item[0][0])
        for item in ctx.logger.warnings
    )
    assert any("ocr close failed" in str(item) for item in ctx.logger.warnings)


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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
            "study": {"language": "en", "auto_open_ui": False},
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
            "study": {"language": "en", "auto_open_ui": False},
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
        map_properties = entries["study_knowledge_map"].meta.input_schema["properties"]
        assert map_properties["subject"]["default"] == ""

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
    ctx = _Ctx(tmp_path, {"study": {"language": "en", "auto_open_ui": False}})
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
            "study": {"language": "en", "auto_open_ui": False},
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
            "study": {"language": "en", "default_mode": MODE_COMPANION, "auto_open_ui": False},
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
            "study": {"language": "en", "auto_open_ui": False},
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
