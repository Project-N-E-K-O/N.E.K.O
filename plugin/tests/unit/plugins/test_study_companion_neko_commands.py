from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import (
    MODE_COMPANION,
    MODE_INTERACTIVE,
)
from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply
from plugin.sdk.plugin import Err, Ok
from plugin.sdk.shared.transport.message_plane import MessagePlaneTransport


pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.errors: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.exceptions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))
        return None

    def error(self, *args, **kwargs):
        self.errors.append((args, kwargs))
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

    def __init__(
        self,
        plugin_dir: Path,
        config: dict[str, object] | None = None,
        *,
        transport: MessagePlaneTransport | None = None,
    ) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text(
            "[plugin]\nid='study_companion'\n", encoding="utf-8"
        )
        self._config = config or {"study": {"language": "en"}}
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self.transport = transport or MessagePlaneTransport(plugin_ctx=None)
        self.pushed_messages: list[dict[str, object]] = []
        self.status_updates: list[dict[str, object]] = []
        self.run_updates: list[dict[str, object]] = []

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


class _FakeStudyOcrPipeline:
    def __init__(self, text: str) -> None:
        self.text = text

    def capture_snapshot(self) -> OcrSnapshot:
        return OcrSnapshot(text=self.text, status="ok", backend="fake")


class _FakeTutorAgent:
    def __init__(self) -> None:
        self.explain_inputs: list[str] = []
        self.question_inputs: list[tuple[str, str]] = []

    def update_config(self, config: StudyConfig) -> None:
        self._config = config

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.explain_inputs.append(text)
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=f"Explained: {text}",
            created_at="2026-05-11T00:00:00Z",
        )

    async def question_generate(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        topic = str((context or {}).get("topic_hint") or "general")
        self.question_inputs.append((text, topic))
        return TutorReply(
            operation="question_generate",
            input_text=text,
            reply=f"Question about {topic}",
            payload={"question": f"What is {topic}?", "topic": topic},
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

    async def shutdown(self) -> None:
        return None


def _texts(ctx: _Ctx) -> list[str]:
    texts: list[str] = []
    for message in ctx.pushed_messages:
        if message.get("source") != "study_companion":
            continue
        parts = message.get("parts") or []
        if parts:
            texts.append(str(parts[0].get("text") or ""))
    return texts


def _last_push(ctx: _Ctx) -> dict[str, object]:
    assert ctx.pushed_messages
    return ctx.pushed_messages[-1]


async def _started_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, object] | None = None,
) -> tuple[StudyCompanionPlugin, _Ctx]:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path, config)
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    return plugin, ctx


def _seed_mastery(plugin: StudyCompanionPlugin) -> None:
    plugin._store.ensure_topic(
        topic_id="derivatives",
        name="Derivatives",
        subject="math",
        chapter="calculus",
    )
    plugin._knowledge_tracker.on_answer(
        topic_id="derivatives",
        question={"question": "What is d/dx x^2?", "answer": "2x"},
        user_answer="2x",
        eval_result={"verdict": "correct", "score": 1.0},
        mode=MODE_COMPANION,
        session_id="unit-test",
    )


@pytest.mark.asyncio
async def test_neko_explain_current_pushes_with_ocr_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._agent = _FakeTutorAgent()
    plugin._ocr_pipeline = _FakeStudyOcrPipeline("Derivative rules")
    try:
        result = await plugin._on_neko_command({"command": "explain_current"})

        assert isinstance(result, Ok)
        assert any("[伴学·概念解释]" in text for text in _texts(ctx))
        assert any("Derivative rules" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_explain_current_no_ocr_pushes_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._ocr_pipeline = _FakeStudyOcrPipeline("")
    try:
        result = await plugin._on_neko_command({"command": "explain_current"})

        assert isinstance(result, Ok)
        assert any("当前屏幕无可识别的文字内容" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_quiz_me_with_topic_generates_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._agent = _FakeTutorAgent()
    try:
        result = await plugin._on_neko_command(
            {"command": "quiz_me", "topic": "derivatives"}
        )

        assert isinstance(result, Ok)
        assert any("[伴学·随堂测验]" in text for text in _texts(ctx))
        assert any("What is derivatives?" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_quiz_me_without_input_uses_cached_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    agent = _FakeTutorAgent()
    plugin._agent = agent
    async with plugin._lock:
        plugin._state.last_ocr_text = "Cached Newton law"
    try:
        result = await plugin._on_neko_command({"command": "quiz_me"})

        assert isinstance(result, Ok)
        assert agent.question_inputs[0][0] == "Cached Newton law"
        assert any("[伴学·随堂测验]" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_quiz_me_no_input_pushes_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin._on_neko_command({"command": "quiz_me"})

        assert isinstance(result, Ok)
        assert any("请指定题目主题" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_show_progress_returns_mastery_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        _seed_mastery(plugin)
        async with plugin._lock:
            plugin._state.session_summary_seed = {"answer_count": 2}

        result = await plugin._on_neko_command({"command": "show_progress"})

        assert isinstance(result, Ok)
        assert any("[伴学·学习进度]" in text for text in _texts(ctx))
        assert any("Derivatives" in text for text in _texts(ctx))
        assert ctx.pushed_messages[-1]["ai_behavior"] == "read"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_show_progress_filtered_by_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        _seed_mastery(plugin)

        result = await plugin._on_neko_command(
            {"command": "show_progress", "topic": "Derivatives"}
        )

        assert isinstance(result, Ok)
        assert any("Derivatives" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_show_progress_keeps_zero_mastery_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        plugin._store.ensure_topic(
            topic_id="limits",
            name="Limits",
            subject="math",
            chapter="calculus",
        )

        result = await plugin._on_neko_command(
            {"command": "show_progress", "topic": "Limits"}
        )

        assert isinstance(result, Ok)
        assert any("Limits: 0%" in text for text in _texts(ctx))
        assert not any("暂无掌握度数据" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_show_progress_empty_when_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin._on_neko_command({"command": "show_progress"})

        assert isinstance(result, Ok)
        assert any("暂无掌握度数据" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_start_review_returns_due_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        deck = plugin._memory_deck_store.create_deck(
            name="Exam Words", deck_type="word", language="en"
        )
        plugin._memory_deck_store.add_word(
            deck_id=deck["id"],
            word="abandon",
            meaning="give up",
        )

        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        assert any("[伴学·复习提醒]" in text for text in _texts(ctx))
        assert ctx.pushed_messages[-1]["priority"] == 3
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_start_review_reports_total_due_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _MemoryDeckStore:
        def count_due_reviews(self, *, deck_id: str = "") -> int:
            return 25

        def due_reviews(self, *, deck_id: str = "", limit: int = 20):
            return [
                {"deck": {"name": f"Deck {index}"}}
                for index in range(min(20, limit))
            ]

    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._memory_deck_store = _MemoryDeckStore()  # type: ignore[assignment]
    try:
        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        assert any("25 张卡片待复习" in text for text in _texts(ctx))
        assert any("先展示前 20 张" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_start_review_no_due_pushes_congrats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        assert any("当前没有到期卡片" in text for text in _texts(ctx))
        assert _last_push(ctx)["visibility"] == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_change_mode_switches_and_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin._on_neko_command(
            {"command": "change_mode", "mode": MODE_INTERACTIVE}
        )

        assert isinstance(result, Ok)
        assert plugin._state.active_mode == MODE_INTERACTIVE
        assert any("[伴学·模式切换]" in text for text in _texts(ctx))
        assert ctx.pushed_messages[-1]["ai_behavior"] == "read"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_change_mode_propagates_set_mode_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)

    async def _fail_set_mode(**_kwargs):
        return Err(RuntimeError("mode store failed"))

    plugin.study_set_mode = _fail_set_mode  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command(
            {"command": "change_mode", "mode": MODE_INTERACTIVE}
        )

        assert isinstance(result, Err)
        assert any("模式切换失败" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_change_mode_rejects_invalid_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        result = await plugin._on_neko_command(
            {"command": "change_mode", "mode": "invalid"}
        )

        assert isinstance(result, Ok)
        assert any("不支持的模式" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_on_neko_command_unknown_silently_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin.logger = ctx.logger
    try:
        result = await plugin._on_neko_command({"command": "unknown"})

        assert isinstance(result, Err)
        assert not _texts(ctx)
        assert any("unknown command" in str(args[0]) for args, _ in ctx.logger.warnings)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_on_neko_command_empty_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin.logger = ctx.logger
    try:
        result = await plugin._on_neko_command({"command": ""})

        assert isinstance(result, Err)
        assert not _texts(ctx)
        assert any("empty command" in str(args[0]) for args, _ in ctx.logger.warnings)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_subscribe_not_called_when_communication_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = MessagePlaneTransport(plugin_ctx=None)
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {"study_companion": {"communication": {"enabled": False}}},
        transport=transport,
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        assert "neko.study_command" not in transport._handlers
        command_result = await plugin._on_neko_command({"command": "show_progress"})
        assert isinstance(command_result, Err)
        assert ctx.pushed_messages == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_subscribe_neko_commands_handles_missing_host_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    host_ctx = getattr(plugin, "_host_ctx", None)
    if hasattr(plugin, "_host_ctx"):
        delattr(plugin, "_host_ctx")
    try:
        ctx.transport._handlers.pop("neko.study_command", None)
        await plugin._subscribe_neko_commands()

        assert "neko.study_command" in ctx.transport._handlers
    finally:
        plugin._host_ctx = host_ctx
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_handler_exception_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin.logger = ctx.logger

    async def _fail(_payload: dict[str, object]) -> None:
        raise RuntimeError("boom")

    plugin._handle_neko_quiz_me = _fail  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "quiz_me", "topic": "math"})

        assert isinstance(result, Err)
        assert any(
            "handler failed" in str(args[0]) for args, _ in ctx.logger.exceptions
        )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_push_neko_command_message_raises_on_err_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)

    def _fail_push(**_kwargs):
        return Err(RuntimeError("push failed"))

    ctx.push_message = _fail_push  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="push_message failed"):
            await plugin._push_neko_command_message(
                visibility=["chat"],
                ai_behavior="respond",
                priority=5,
                text="hello",
            )
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_command_roundtrip_explain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._agent = _FakeTutorAgent()
    plugin._ocr_pipeline = _FakeStudyOcrPipeline("Limits and continuity")
    try:
        result = await ctx.transport.publish(
            "neko.study_command", {"command": "explain_current"}
        )

        assert isinstance(result, Ok)
        assert any("Limits and continuity" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_command_roundtrip_quiz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._agent = _FakeTutorAgent()
    try:
        result = await ctx.transport.publish(
            "neko.study_command", {"command": "quiz_me", "topic": "limits"}
        )

        assert isinstance(result, Ok)
        assert any("What is limits?" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_command_roundtrip_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    try:
        _seed_mastery(plugin)

        result = await ctx.transport.publish(
            "neko.study_command", {"command": "show_progress"}
        )

        assert isinstance(result, Ok)
        assert any("[伴学·学习进度]" in text for text in _texts(ctx))
    finally:
        await plugin.shutdown()
