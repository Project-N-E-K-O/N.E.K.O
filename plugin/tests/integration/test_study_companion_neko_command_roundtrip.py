from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import MODE_COMPANION
from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply
from plugin.sdk.plugin import Ok
from plugin.sdk.shared.transport.message_plane import MessagePlaneTransport


pytestmark = pytest.mark.plugin_integration


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

    def __init__(self, plugin_dir: Path) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text(
            "[plugin]\nid='study_companion'\n", encoding="utf-8"
        )
        self._config = {"study": {"language": "en"}}
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self.transport = MessagePlaneTransport(plugin_ctx=None)
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
    def update_config(self, config: StudyConfig) -> None:
        self._config = config

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
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
        return TutorReply(
            operation="question_generate",
            input_text=text,
            reply=f"Question about {topic}",
            payload={"question": f"What is {topic}?", "topic": topic},
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[StudyCompanionPlugin, _Ctx]:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(tmp_path)
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    return plugin, ctx


@pytest.mark.asyncio
async def test_roundtrip_interrupt_then_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    plugin._agent = _FakeTutorAgent()
    plugin._ocr_pipeline = _FakeStudyOcrPipeline("Differentiation")
    try:
        assert isinstance(
            await ctx.transport.publish(
                "neko.study_command", {"command": "explain_current"}
            ),
            Ok,
        )
        assert isinstance(
            await ctx.transport.publish(
                "neko.study_command", {"command": "quiz_me", "topic": "chain rule"}
            ),
            Ok,
        )

        await _wait_for_text(ctx, "Differentiation")
        await _wait_for_text(ctx, "What is chain rule?")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_roundtrip_queue_interrupted_by_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    queue_cancelled = asyncio.Event()
    interrupt_started = asyncio.Event()
    release = asyncio.Event()

    async def _queue(_payload: dict[str, Any]) -> None:
        queue_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            queue_cancelled.set()
            raise

    async def _interrupt(_payload: dict[str, Any]) -> None:
        interrupt_started.set()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    plugin._handle_neko_start_review = _interrupt  # type: ignore[method-assign]
    try:
        assert isinstance(
            await ctx.transport.publish("neko.study_command", {"command": "quiz_me"}),
            Ok,
        )
        await _wait_until(queue_started.is_set)

        assert isinstance(
            await ctx.transport.publish(
                "neko.study_command", {"command": "start_review"}
            ),
            Ok,
        )

        await _wait_until(queue_cancelled.is_set)
        await _wait_until(interrupt_started.is_set)
        assert not _texts(ctx)
    finally:
        release.set()
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_roundtrip_shutdown_with_pending_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, ctx = await _started_plugin(tmp_path, monkeypatch)
    queue_started = asyncio.Event()
    release = asyncio.Event()

    async def _queue(_payload: dict[str, Any]) -> None:
        queue_started.set()
        await release.wait()

    plugin._handle_neko_quiz_me = _queue  # type: ignore[method-assign]
    assert isinstance(
        await ctx.transport.publish("neko.study_command", {"command": "quiz_me"}),
        Ok,
    )
    assert isinstance(
        await ctx.transport.publish("neko.study_command", {"command": "show_progress"}),
        Ok,
    )
    await _wait_until(queue_started.is_set)

    await plugin.shutdown()

    assert plugin._command_worker_task is None
    assert plugin._interruptible_task is None
    assert plugin._command_queue.empty()
    release.set()
