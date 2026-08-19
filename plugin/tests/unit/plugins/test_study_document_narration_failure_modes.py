from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import (
    LLM_OPERATION_DOCUMENT_ANALYZE,
    LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE,
    LLM_OPERATION_DOCUMENT_MERGE,
)
from plugin.plugins.study_companion.document_analysis import ValidatedDocument
from plugin.plugins.study_companion.document_analysis_jobs import (
    DocumentAnalysisJobManager,
)
from plugin.plugins.study_companion.document_chunking import DocumentChunk
from plugin.plugins.study_companion.models import StudyConfig, TutorReply
from plugin.plugins.study_companion.qwen_native_client import QwenNativeResult
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent
from plugin.sdk.plugin import Ok


pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        return None


class _RecordingBus:
    def __init__(self, *, raises: bool = False, hanging: bool = False) -> None:
        self.events = []
        self.raises = raises
        self.hanging = hanging
        self.tasks: list[asyncio.Task[None]] = []

    def schedule_emit(self, event):
        if self.raises:
            raise RuntimeError("queue unavailable")
        self.events.append(event)
        if self.hanging:
            async def wait_forever() -> None:
                await asyncio.Future()

            task = asyncio.create_task(wait_forever())
            self.tasks.append(task)
            return task
        return asyncio.create_task(asyncio.sleep(0))


def _qwen_result(text: str, *, truncated: bool = False) -> QwenNativeResult:
    return QwenNativeResult(
        text=text,
        model="test-model",
        model_group="agent",
        request_id="request-id",
        input_tokens=10,
        output_tokens=10,
        output_limit_reached=truncated,
    )


def _chunked_document() -> tuple[ValidatedDocument, tuple[DocumentChunk, ...]]:
    texts = ("part zero evidence", "part one evidence")
    source = "\n".join(texts)
    document = ValidatedDocument(
        name="book.txt",
        document_type="text/plain",
        text=source,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=len(source),
        tokens=50_000,
        sha256="a" * 64,
    )
    chunks = tuple(
        DocumentChunk(
            index=index,
            text=text,
            tokens=10_000,
            start_char=0,
            end_char=len(text),
            heading_paths=((f"Part {index}",),),
        )
        for index, text in enumerate(texts)
    )
    return document, chunks


async def _wait_for_terminal(manager: DocumentAnalysisJobManager, job_id: str):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        status = await manager.status(job_id)
        if status["status"] != "running":
            return status
        await asyncio.sleep(0.001)
    raise AssertionError("document job did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_direct_model_result_preserves_output_limit(
    monkeypatch: pytest.MonkeyPatch, truncated: bool
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig())

    async def call_result(*_args, **_kwargs):
        return _qwen_result("safe direct analysis", truncated=truncated)

    monkeypatch.setattr(agent, "_call_model_result", call_result)
    document, _ = _chunked_document()
    reply = await agent.document_analyze(document)

    assert reply.reply == "safe direct analysis"
    assert reply.degraded is False
    assert reply.diagnostic == ("output_truncated" if truncated else "")


@pytest.mark.asyncio
async def test_direct_job_public_payload_preserves_output_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, _ = _chunked_document()
    direct_document = ValidatedDocument(
        name=document.name,
        document_type=document.document_type,
        text=document.text,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=document.chars,
        tokens=10,
        sha256=document.sha256,
    )
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: direct_document)

    class Agent:
        async def document_analyze(self, current):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=current.descriptor,
                reply="existing truncated analysis",
                degraded=False,
                diagnostic="output_truncated",
            )

    class Owner:
        _agent = Agent()
        _event_bus = None
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=False, general_narration_enabled=True
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, result, **kwargs):
            return {
                "operation": operation,
                "reply": result.reply,
                "summary": result.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": result.degraded,
                "diagnostic": result.diagnostic,
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
    )
    status = await _wait_for_terminal(owner._document_jobs, started.value["job_id"])

    assert status["status"] == "completed"
    assert status["reply"] == "existing truncated analysis"
    assert status["degraded"] is False
    assert status["diagnostic"] == "output_truncated"
    await owner._document_jobs.shutdown()


@pytest.mark.asyncio
async def test_document_narration_targets_the_requesting_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, _ = _chunked_document()
    direct_document = ValidatedDocument(
        name=document.name,
        document_type=document.document_type,
        text=document.text,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=document.chars,
        tokens=10,
        sha256=document.sha256,
    )
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: direct_document)

    class Agent:
        async def document_analyze(self, current):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=current.descriptor,
                reply="private summary",
            )

    bus = _RecordingBus()

    class Owner:
        _agent = Agent()
        _event_bus = bus
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=True, general_narration_enabled=True
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, result, **kwargs):
            return {
                "operation": operation,
                "reply": result.reply,
                "summary": result.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": result.degraded,
                "diagnostic": result.diagnostic,
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
        _ctx={"lanlan_name": "alice"},
    )
    for _ in range(100):
        status = await owner._document_jobs.status(
            started.value["job_id"], owner_id="alice"
        )
        if status["status"] != "running":
            break
        await asyncio.sleep(0.001)

    assert status["status"] == "completed"
    assert len(bus.events) == 1
    assert bus.events[0].payload == {
        "response_mode": "document_analysis",
        "content": "private summary",
        "target_lanlan": "alice",
    }
    await owner._document_jobs.shutdown()


@pytest.mark.asyncio
async def test_legacy_chunk_and_merge_methods_still_return_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig())
    document, chunks = _chunked_document()

    async def call_result(_messages, *, operation, deadline):
        assert deadline > 0
        if operation == LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE:
            return _qwen_result("safe memo", truncated=True)
        return _qwen_result("safe merged analysis", truncated=True)

    monkeypatch.setattr(agent, "_call_model_result", call_result)
    memo = await agent.analyze_document_chunk(document, chunks[0], len(chunks))
    merged = await agent.merge_document_chunks(
        document,
        chunks,
        (memo, "second memo"),
        messages=[{"role": "user", "content": "merge"}],
    )

    assert memo == "safe memo"
    assert merged == "safe merged analysis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("truncate_chunk", "truncate_merge", "expected_count"),
    [(True, False, 1), (False, True, 0), (False, False, 0)],
)
async def test_chunked_job_aggregates_truncation_and_schedules_once(
    monkeypatch: pytest.MonkeyPatch,
    truncate_chunk: bool,
    truncate_merge: bool,
    expected_count: int,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, chunks = _chunked_document()
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: document)
    monkeypatch.setattr(entry, "split_document", lambda *_args: chunks)
    captured_metadata = {}

    class Agent:
        build_document_merge_messages = staticmethod(
            lambda *_args: [{"role": "user", "content": "merge"}]
        )

        def _new_operation_deadline(self, _operation, _messages):
            return asyncio.get_running_loop().time() + 30

        async def _call_model_result(self, messages, *, operation, deadline):
            assert deadline > 0
            if operation == LLM_OPERATION_DOCUMENT_MERGE:
                return _qwen_result(
                    "# Final analysis\n\nUseful summary.", truncated=truncate_merge
                )
            first_chunk = "Part: 1/2" in messages[1]["content"]
            return _qwen_result(
                "safe evidence memo",
                truncated=truncate_chunk and first_chunk,
            )

    bus = _RecordingBus()

    class Owner:
        _agent = Agent()
        _event_bus = bus
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=True, general_narration_enabled=True
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, reply, **kwargs):
            captured_metadata.update(kwargs["metadata"])
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
    )
    assert isinstance(started, Ok)
    status = await _wait_for_terminal(owner._document_jobs, started.value["job_id"])

    truncated = truncate_chunk or truncate_merge
    assert status["status"] == "completed"
    assert status["reply"] == "# Final analysis\n\nUseful summary."
    assert status["degraded"] is False
    assert status["diagnostic"] == ("output_truncated" if truncated else "")
    assert captured_metadata["truncated_chunk_count"] == expected_count
    assert captured_metadata["total_chunks"] == 2
    assert captured_metadata["merge_output_truncated"] is truncate_merge
    assert status["document_narration_scheduled"] is True
    assert status["document_narration_status"] == "scheduled"
    assert len(bus.events) == 1
    assert bus.events[0].name == "general_response_completed"
    assert bus.events[0].payload == {
        "response_mode": "document_analysis",
        "content": "Final analysis\n\nUseful summary.",
    }
    for _ in range(3):
        await owner._document_jobs.status(started.value["job_id"])
    assert len(bus.events) == 1
    await owner._document_jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "narration_enabled", "reply", "expected_status", "expected_reason"),
    [
        (False, True, "safe", "disabled", "communication_disabled"),
        (True, False, "safe", "disabled", "general_narration_disabled"),
        (True, True, "", "not_applicable", "empty_reply"),
    ],
)
async def test_document_narration_respects_disable_and_empty_contracts(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    narration_enabled: bool,
    reply: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, _ = _chunked_document()
    direct_document = ValidatedDocument(
        name=document.name,
        document_type=document.document_type,
        text=document.text,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=document.chars,
        tokens=10,
        sha256=document.sha256,
    )
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: direct_document)
    bus = _RecordingBus()

    class Agent:
        async def document_analyze(self, current):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=current.descriptor,
                reply=reply,
            )

    class Owner:
        _agent = Agent()
        _event_bus = bus
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=enabled,
                general_narration_enabled=narration_enabled,
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, result, **kwargs):
            return {
                "operation": operation,
                "reply": result.reply,
                "summary": result.reply,
                "document": kwargs["public_payload"]["document"],
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
    )
    status = await _wait_for_terminal(owner._document_jobs, started.value["job_id"])

    assert status["status"] == "completed"
    assert status["document_narration_scheduled"] is False
    assert status["document_narration_status"] == expected_status
    assert status["document_narration_reason"] == expected_reason
    assert bus.events == []
    await owner._document_jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["failed", "canceled"])
async def test_completion_hook_does_not_run_for_non_completed_jobs(terminal: str) -> None:
    manager = DocumentAnalysisJobManager()
    completed = []

    if terminal == "failed":

        async def runner(_update):
            raise RuntimeError("failed")

    else:
        started = asyncio.Event()

        async def runner(_update):
            started.set()
            await asyncio.Future()

    job = await manager.start(
        analysis_mode="direct",
        document={"name": "book.txt"},
        total_chunks=1,
        runner=runner,
        on_completed=completed.append,
    )
    if terminal == "canceled":
        await started.wait()
        status = await manager.cancel(job["job_id"])
    else:
        status = await _wait_for_terminal(manager, job["job_id"])

    assert status["status"] == terminal
    assert completed == []
    await manager.shutdown()


@pytest.mark.asyncio
async def test_completion_hook_exception_does_not_change_completed_job() -> None:
    manager = DocumentAnalysisJobManager()

    async def runner(_update):
        return {"reply": "safe", "summary": "safe", "degraded": False}

    def on_completed(_result):
        raise RuntimeError("callback failed")

    job = await manager.start(
        analysis_mode="direct",
        document={"name": "book.txt"},
        total_chunks=1,
        runner=runner,
        on_completed=on_completed,
    )
    status = await _wait_for_terminal(manager, job["job_id"])

    assert status["status"] == "completed"
    assert status["reply"] == "safe"
    assert status["degraded"] is False
    await manager.shutdown()


@pytest.mark.asyncio
async def test_hanging_delivery_does_not_block_completed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, _ = _chunked_document()
    direct_document = ValidatedDocument(
        name=document.name,
        document_type=document.document_type,
        text=document.text,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=document.chars,
        tokens=10,
        sha256=document.sha256,
    )
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: direct_document)
    bus = _RecordingBus(hanging=True)

    class Agent:
        async def document_analyze(self, current):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=current.descriptor,
                reply="safe analysis",
            )

    class Owner:
        _agent = Agent()
        _event_bus = bus
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=True, general_narration_enabled=True
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, result, **kwargs):
            return {
                "operation": operation,
                "reply": result.reply,
                "summary": result.reply,
                "document": kwargs["public_payload"]["document"],
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
    )
    status = await asyncio.wait_for(
        _wait_for_terminal(owner._document_jobs, started.value["job_id"]), timeout=1
    )

    assert status["status"] == "completed"
    assert status["document_narration_scheduled"] is True
    assert bus.tasks and not bus.tasks[0].done()
    for task in bus.tasks:
        task.cancel()
    await asyncio.gather(*bus.tasks, return_exceptions=True)
    await owner._document_jobs.shutdown()


@pytest.mark.asyncio
async def test_scheduling_exception_does_not_overwrite_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import entry_document_analysis_jobs as entry

    document, _ = _chunked_document()
    direct_document = ValidatedDocument(
        name=document.name,
        document_type=document.document_type,
        text=document.text,
        instruction="",
        locale="en",
        analysis_kind="auto",
        chars=document.chars,
        tokens=10,
        sha256=document.sha256,
    )
    monkeypatch.setattr(entry, "validate_document", lambda **_kwargs: direct_document)

    class Agent:
        async def document_analyze(self, current):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=current.descriptor,
                reply="safe analysis",
            )

    class Owner:
        _agent = Agent()
        _event_bus = _RecordingBus(raises=True)
        _cfg = SimpleNamespace(
            communication=SimpleNamespace(
                enabled=True, general_narration_enabled=True
            )
        )
        logger = _Logger()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, result, **kwargs):
            return {
                "operation": operation,
                "reply": result.reply,
                "summary": result.reply,
                "document": kwargs["public_payload"]["document"],
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=document.text,
        locale="en",
    )
    status = await _wait_for_terminal(owner._document_jobs, started.value["job_id"])

    assert status["status"] == "completed"
    assert status["reply"] == "safe analysis"
    assert status["document_narration_scheduled"] is False
    assert status["document_narration_status"] == "delivery_failed"
    assert status["document_narration_reason"] == "event_delivery_failed"
    await owner._document_jobs.shutdown()
