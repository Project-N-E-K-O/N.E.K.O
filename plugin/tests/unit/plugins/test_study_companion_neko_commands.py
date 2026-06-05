from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import (
    MODE_COMPANION,
    MODE_INTERACTIVE,
)
from plugin.plugins.study_companion.entry_common import _detect_mastery_threshold_crossed
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


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


async def _wait_for_text(ctx: _Ctx, needle: str, *, timeout: float = 2.0) -> str:
    found = ""

    def _has_text() -> bool:
        nonlocal found
        for text in _texts(ctx):
            if needle in text:
                found = text
                return True
        return False

    await _wait_until(_has_text, timeout=timeout)
    return found


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


def test_detect_mastery_threshold_crossed_returns_highest_crossed() -> None:
    assert _detect_mastery_threshold_crossed(0.2, 0.9) == "0.85"
    assert _detect_mastery_threshold_crossed(0.9, 0.2) == "0.85"


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
        await _wait_for_text(ctx, "[伴学·概念解释]")
        await _wait_for_text(ctx, "Derivative rules")
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
        await _wait_for_text(ctx, "当前屏幕无可识别的文字内容")
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
        await _wait_for_text(ctx, "[伴学·随堂测验]")
        await _wait_for_text(ctx, "What is derivatives?")
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
        await _wait_until(lambda: bool(agent.question_inputs))
        assert agent.question_inputs[0][0] == "Cached Newton law"
        await _wait_for_text(ctx, "[伴学·随堂测验]")
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
        await _wait_for_text(ctx, "请指定题目主题")
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
        await _wait_for_text(ctx, "[伴学·学习进度]")
        await _wait_for_text(ctx, "Derivatives")
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
        await _wait_for_text(ctx, "Derivatives")
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
        await _wait_for_text(ctx, "Limits: 0%")
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
        await _wait_for_text(ctx, "暂无掌握度数据")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_show_progress_empty_mastery_includes_due_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _MemoryDeckStore:
        def count_due_reviews(self) -> int:
            return 7

    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._memory_deck_store = _MemoryDeckStore()  # type: ignore[assignment]
    try:
        result = await plugin._on_neko_command({"command": "show_progress"})

        assert isinstance(result, Ok)
        text = await _wait_for_text(ctx, "待复习卡片: 7 张")
        assert "暂无掌握度数据" in text
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
        await _wait_for_text(ctx, "[伴学·复习提醒]")
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
        await _wait_for_text(ctx, "25 张卡片待复习")
        await _wait_for_text(ctx, "先展示前 20 张")
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
        await _wait_for_text(ctx, "当前没有到期卡片")
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
        await _wait_until(lambda: plugin._state.active_mode == MODE_INTERACTIVE)
        assert plugin._state.active_mode == MODE_INTERACTIVE
        await _wait_for_text(ctx, "[伴学·模式切换]")
        assert ctx.pushed_messages[-1]["ai_behavior"] == "read"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_neko_change_mode_pushes_set_mode_failure(
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

        assert isinstance(result, Ok)
        await _wait_for_text(ctx, "模式切换失败")
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
        await _wait_for_text(ctx, "不支持的模式")
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
async def test_subscribe_neko_commands_uses_messages_bus_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Record:
        metadata = {
            "topic": "neko.study_command",
            "payload": {"command": "show_progress"},
        }
        description = "neko.study_command"
        raw = {}

    class _Delta:
        added = (_Record(),)

    class _Watcher:
        def __init__(self) -> None:
            self.callback = None
            self.started = False
            self.stopped = False

        def subscribe(self, *, on: str = "add"):
            def _decorator(callback):
                self.callback = callback
                return callback

            return _decorator

        def start(self):
            self.started = True
            return self

        def stop(self) -> None:
            self.stopped = True

    class _MessageList:
        def __init__(self, watcher: _Watcher) -> None:
            self.watcher = watcher

        def watch(self, *_args, **_kwargs):
            return self.watcher

    class _Messages:
        def __init__(self, watcher: _Watcher) -> None:
            self.watcher = watcher

        async def get(self, **_kwargs):
            return _MessageList(self.watcher)

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    watcher = _Watcher()
    ctx = _Ctx(tmp_path)
    delattr(ctx, "transport")
    ctx.bus = SimpleNamespace(messages=_Messages(watcher))
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    try:
        assert isinstance(result, Ok)
        assert watcher.started
        assert plugin._neko_command_watcher is not None

        assert watcher.callback is not None
        watcher.callback(_Delta())

        await _wait_for_text(ctx, "暂无掌握度数据")
    finally:
        await plugin.shutdown()
    assert watcher.stopped


@pytest.mark.asyncio
async def test_shutdown_unsubscribes_neko_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    closed = False
    try:
        handlers = ctx.transport._handlers.get("neko.study_command", [])
        assert handlers == [plugin._neko_command_handler]

        await plugin.shutdown()
        closed = True

        assert "neko.study_command" not in ctx.transport._handlers
        assert plugin._neko_command_transport is None
        assert plugin._neko_command_handler is None
    finally:
        if not closed:
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

        assert isinstance(result, Ok)
        await _wait_until(
            lambda: any(
                "command task failed" in str(args[0])
                for args, _ in ctx.logger.exceptions
            )
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
        await _wait_for_text(ctx, "Limits and continuity")
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
        await _wait_for_text(ctx, "What is limits?")
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
        await _wait_for_text(ctx, "[伴学·学习进度]")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_interrupt_command_cancels_current_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def _first(_payload: dict[str, object]) -> None:
        first_started.set()
        try:
            await release_first.wait()
        except asyncio.CancelledError:
            first_cancelled.set()
            raise

    async def _second(_payload: dict[str, object]) -> None:
        second_started.set()

    plugin._handle_neko_explain_current = _first  # type: ignore[method-assign]
    plugin._handle_neko_start_review = _second  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "explain_current"})
        assert isinstance(result, Ok)
        await _wait_until(first_started.is_set)

        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        await _wait_until(first_cancelled.is_set)
        await _wait_until(second_started.is_set)
    finally:
        release_first.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_interrupt_command_bypasses_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    interrupt_started = asyncio.Event()
    release_queue = asyncio.Event()

    async def _queue(_payload: dict[str, object]) -> None:
        queue_started.set()
        await release_queue.wait()

    async def _interrupt(_payload: dict[str, object]) -> None:
        interrupt_started.set()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    plugin._handle_neko_start_review = _interrupt  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "quiz_me"})
        assert isinstance(result, Ok)
        await _wait_until(queue_started.is_set)

        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        await _wait_until(interrupt_started.is_set)
        assert not release_queue.is_set()
    finally:
        release_queue.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_interrupt_during_queue_command_cancels_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    queue_cancelled = asyncio.Event()
    interrupt_started = asyncio.Event()
    release_queue = asyncio.Event()

    async def _queue(_payload: dict[str, object]) -> None:
        queue_started.set()
        try:
            await release_queue.wait()
        except asyncio.CancelledError:
            queue_cancelled.set()
            raise

    async def _interrupt(_payload: dict[str, object]) -> None:
        interrupt_started.set()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    plugin._handle_neko_change_mode = _interrupt  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "quiz_me"})
        assert isinstance(result, Ok)
        await _wait_until(queue_started.is_set)

        result = await plugin._on_neko_command(
            {"command": "change_mode", "mode": MODE_INTERACTIVE}
        )

        assert isinstance(result, Ok)
        await _wait_until(queue_cancelled.is_set)
        await _wait_until(interrupt_started.is_set)
    finally:
        release_queue.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_queue_command_does_not_cancel_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    interrupt_started = asyncio.Event()
    interrupt_cancelled = asyncio.Event()
    queue_started = asyncio.Event()
    release_interrupt = asyncio.Event()

    async def _interrupt(_payload: dict[str, object]) -> None:
        interrupt_started.set()
        try:
            await release_interrupt.wait()
        except asyncio.CancelledError:
            interrupt_cancelled.set()
            raise

    async def _queue(_payload: dict[str, object]) -> None:
        queue_started.set()

    plugin._handle_neko_explain_current = _interrupt  # type: ignore[method-assign]
    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "explain_current"})
        assert isinstance(result, Ok)
        await _wait_until(interrupt_started.is_set)

        result = await plugin._on_neko_command({"command": "quiz_me"})
        assert isinstance(result, Ok)
        await _wait_until(plugin._command_queue.empty)
        assert not interrupt_cancelled.is_set()
        assert not queue_started.is_set()

        release_interrupt.set()
        await _wait_until(queue_started.is_set)
    finally:
        release_interrupt.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_queue_commands_execute_sequentially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    calls: list[str] = []

    async def _quiz(_payload: dict[str, object]) -> None:
        calls.append("quiz")

    async def _progress(_payload: dict[str, object]) -> None:
        calls.append("progress")

    plugin._handle_neko_quiz_me = _quiz  # type: ignore[method-assign]
    plugin._handle_neko_show_progress = _progress  # type: ignore[method-assign]
    try:
        assert isinstance(await plugin._on_neko_command({"command": "quiz_me"}), Ok)
        assert isinstance(
            await plugin._on_neko_command({"command": "show_progress"}), Ok
        )

        await _wait_until(lambda: calls == ["quiz", "progress"])
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_two_interrupt_commands_sequentially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _interrupt(_payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise
        else:
            second_started.set()

    plugin._handle_neko_explain_current = _interrupt  # type: ignore[method-assign]
    try:
        assert isinstance(
            await plugin._on_neko_command({"command": "explain_current"}), Ok
        )
        await _wait_until(first_started.is_set)
        assert isinstance(
            await plugin._on_neko_command({"command": "explain_current"}), Ok
        )

        await _wait_until(first_cancelled.is_set)
        await _wait_until(second_started.is_set)
    finally:
        release.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_worker_first_then_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    queue_cancelled = asyncio.Event()
    release = asyncio.Event()

    async def _queue(_payload: dict[str, object]) -> None:
        queue_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            queue_cancelled.set()
            raise

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    result = await plugin._on_neko_command({"command": "quiz_me"})
    assert isinstance(result, Ok)
    await _wait_until(queue_started.is_set)

    await plugin.shutdown()

    assert queue_cancelled.is_set()
    assert plugin._command_worker_task is None
    assert plugin._interruptible_task is None


@pytest.mark.asyncio
async def test_shutdown_no_orphan_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _queue(_payload: dict[str, object]) -> None:
        started.set()
        await release.wait()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    assert isinstance(await plugin._on_neko_command({"command": "quiz_me"}), Ok)
    assert isinstance(await plugin._on_neko_command({"command": "show_progress"}), Ok)
    await _wait_until(started.is_set)

    await plugin.shutdown()

    assert plugin._command_worker_task is None
    assert plugin._interruptible_task is None
    assert plugin._command_queue.empty()
    release.set()


@pytest.mark.asyncio
async def test_worker_restarts_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    original_worker = plugin._run_command_worker
    await plugin._cancel_command_worker()

    async def _crash() -> None:
        raise RuntimeError("worker crashed")

    plugin._run_command_worker = _crash  # type: ignore[method-assign]
    plugin._start_command_worker()
    await _wait_until(
        lambda: plugin._command_worker_task is None
        and plugin._worker_crash_count == 1
    )

    plugin._run_command_worker = original_worker  # type: ignore[method-assign]
    result = await plugin._on_neko_command({"command": "show_progress"})

    assert isinstance(result, Ok)
    await _wait_until(
        lambda: plugin._command_worker_task is not None
        and not plugin._command_worker_task.done()
    )
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_worker_poison_protection_stops_after_3_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    await plugin._cancel_command_worker()

    async def _crash() -> None:
        raise RuntimeError("worker crashed")

    plugin._run_command_worker = _crash  # type: ignore[method-assign]
    try:
        for expected_count in (1, 2, 3):
            plugin._start_command_worker()
            await _wait_until(
                lambda: plugin._command_worker_task is None
                and plugin._worker_crash_count == expected_count
            )

        result = await plugin._on_neko_command({"command": "show_progress"})

        assert isinstance(result, Ok)
        await _wait_until(lambda: plugin._command_queue.qsize() == 1)
        assert plugin._command_worker_task is None
        assert plugin._command_queue.qsize() == 1
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_command_task_done_callback_clears_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    started = asyncio.Event()

    async def _interrupt(_payload: dict[str, object]) -> None:
        started.set()

    plugin._handle_neko_start_review = _interrupt  # type: ignore[method-assign]
    try:
        result = await plugin._on_neko_command({"command": "start_review"})

        assert isinstance(result, Ok)
        await _wait_until(started.is_set)
        await _wait_until(lambda: plugin._interruptible_task is None)
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_cancelled_error_not_logged_as_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin.logger = ctx.logger
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def _first(_payload: dict[str, object]) -> None:
        first_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            first_cancelled.set()
            raise

    async def _second(_payload: dict[str, object]) -> None:
        second_started.set()

    plugin._handle_neko_explain_current = _first  # type: ignore[method-assign]
    plugin._handle_neko_start_review = _second  # type: ignore[method-assign]
    try:
        assert isinstance(
            await plugin._on_neko_command({"command": "explain_current"}), Ok
        )
        await _wait_until(first_started.is_set)
        assert isinstance(await plugin._on_neko_command({"command": "start_review"}), Ok)

        await _wait_until(first_cancelled.is_set)
        await _wait_until(second_started.is_set)
        assert ctx.logger.exceptions == []
    finally:
        release.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_handler_cancelled_error_propagates_to_task_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    queue_cancelled = asyncio.Event()
    interrupt_started = asyncio.Event()
    release = asyncio.Event()

    async def _queue(_payload: dict[str, object]) -> None:
        queue_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            queue_cancelled.set()
            raise

    async def _interrupt(_payload: dict[str, object]) -> None:
        interrupt_started.set()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    plugin._handle_neko_start_review = _interrupt  # type: ignore[method-assign]
    try:
        assert isinstance(await plugin._on_neko_command({"command": "quiz_me"}), Ok)
        await _wait_until(queue_started.is_set)
        assert isinstance(await plugin._on_neko_command({"command": "start_review"}), Ok)

        await _wait_until(queue_cancelled.is_set)
        await _wait_until(interrupt_started.is_set)
    finally:
        release.set()
        await plugin.shutdown()
