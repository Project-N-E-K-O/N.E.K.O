from __future__ import annotations

import asyncio
import importlib

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import (
    LLM_OPERATION_DOCUMENT_ANALYZE,
    LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE,
    LLM_OPERATION_DOCUMENT_MERGE,
)
from plugin.plugins.study_companion.document_analysis import (
    DOCUMENT_ANALYSIS_KINDS,
    DOCUMENT_MAX_TOKENS,
    ValidatedDocument,
)
from plugin.plugins.study_companion import document_analysis as document_module
from plugin.plugins.study_companion import document_analysis_jobs as document_jobs_module
from plugin.plugins.study_companion import entry_document_analysis_jobs as document_entries_module
from plugin.plugins.study_companion.document_analysis import (
    DocumentValidationError,
    validate_document,
)
from plugin.plugins.study_companion.document_analysis_jobs import (
    DocumentAnalysisJobError,
    DocumentAnalysisJobManager,
)
from plugin.plugins.study_companion.document_chunking import (
    DOCUMENT_DIRECT_MAX_TOKENS,
    DocumentChunk,
)
from plugin.plugins.study_companion.models import TutorReply
from plugin.plugins.study_companion.qwen_native_client import (
    _OUTPUT_TOKEN_BUDGETS,
    operation_timeout_seconds,
)
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent
from plugin.plugins.study_companion import (
    tutor_llm_agent_document as document_agent_module,
)
from plugin.plugins.study_companion.tutor_llm_agent_document import (
    DocumentChunkAnalysisError,
    build_document_chunk_messages,
    build_document_merge_messages,
)
from plugin.sdk.plugin import Ok
from plugin.sdk.shared.constants import EVENT_META_ATTR


pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        return None


def test_long_document_operation_budgets_and_timeouts_are_isolated() -> None:
    assert DOCUMENT_DIRECT_MAX_TOKENS == 48_000
    assert DOCUMENT_MAX_TOKENS == 160_000
    assert _OUTPUT_TOKEN_BUDGETS[LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE] == 1_200
    assert _OUTPUT_TOKEN_BUDGETS[LLM_OPERATION_DOCUMENT_MERGE] == 4_096
    assert (
        operation_timeout_seconds(
            LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE,
            has_image=False,
            configured_timeout_seconds=3600,
        )
        == 110.0
    )
    assert (
        operation_timeout_seconds(
            LLM_OPERATION_DOCUMENT_MERGE,
            has_image=False,
            configured_timeout_seconds=3600,
        )
        == 120.0
    )


def test_start_entry_marks_source_sensitive() -> None:
    meta = getattr(StudyCompanionPlugin.study_start_document_analysis, EVENT_META_ATTR)
    assert meta.input_schema["properties"]["document_text"] == {
        "type": "string",
        "writeOnly": True,
        "x-sensitive": True,
    }


def test_start_entry_uses_canonical_analysis_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extended_kinds = (*DOCUMENT_ANALYSIS_KINDS, "new_contract_kind")
    with monkeypatch.context() as patch:
        patch.setattr(document_module, "DOCUMENT_ANALYSIS_KINDS", extended_kinds)
        reloaded = importlib.reload(document_entries_module)
        meta = getattr(
            reloaded._DocumentAnalysisJobsEntriesMixin.study_start_document_analysis,
            EVENT_META_ATTR,
        )
        assert meta.input_schema["properties"]["analysis_kind"]["enum"] == list(
            extended_kinds
        )
    importlib.reload(document_entries_module)


def test_superseded_direct_document_entry_is_not_registered() -> None:
    assert not hasattr(StudyCompanionPlugin, "study_analyze_document")


def test_direct_and_chunked_token_boundaries_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def validate_at(tokens: int, *, max_tokens: int = DOCUMENT_MAX_TOKENS):
        monkeypatch.setattr(document_module, "count_tokens", lambda _text: tokens)
        monkeypatch.setattr(
            document_module,
            "count_tokens",
            lambda text: tokens if text == "valid source" else 0,
        )
        return validate_document(
            document_name="book.txt",
            document_type="text/plain",
            document_text="valid source",
            locale="en",
            max_tokens=max_tokens,
        )

    assert validate_at(48_000, max_tokens=DOCUMENT_DIRECT_MAX_TOKENS).tokens == 48_000
    with pytest.raises(DocumentValidationError) as direct_overflow:
        validate_at(48_001, max_tokens=DOCUMENT_DIRECT_MAX_TOKENS)
    assert direct_overflow.value.diagnostic == "document_too_long"
    assert validate_at(160_000).tokens == 160_000
    with pytest.raises(DocumentValidationError) as chunked_overflow:
        validate_at(160_001)
    assert chunked_overflow.value.diagnostic == "document_too_long"


@pytest.mark.asyncio
async def test_job_manager_allows_one_active_job_and_cancel_propagates() -> None:
    manager = DocumentAnalysisJobManager()
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def runner(_update):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            canceled.set()
            raise

    first = await manager.start(
        analysis_mode="chunked",
        document={"name": "book.txt", "source_retained": False},
        total_chunks=2,
        runner=runner,
    )
    await started.wait()
    with pytest.raises(DocumentAnalysisJobError) as raised:
        await manager.start(
            analysis_mode="direct", document={}, total_chunks=1, runner=runner
        )
    assert raised.value.diagnostic == "document_job_busy"
    result = await manager.cancel(first["job_id"])
    assert result["status"] == "canceled"
    assert result["diagnostic"] == "document_canceled"
    assert canceled.is_set()


@pytest.mark.asyncio
async def test_cancel_keeps_active_slot_until_runner_finishes_unwinding() -> None:
    manager = DocumentAnalysisJobManager()
    running = asyncio.Event()
    cancellation_started = asyncio.Event()
    allow_cancellation_to_finish = asyncio.Event()
    replacement_started = asyncio.Event()

    async def running_job(_update):
        running.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancellation_started.set()
            await allow_cancellation_to_finish.wait()
            raise

    async def replacement_job(_update):
        replacement_started.set()
        await asyncio.Future()

    first = await manager.start(
        analysis_mode="direct",
        document={"name": "running.txt"},
        total_chunks=1,
        runner=running_job,
    )
    await running.wait()
    cancel_task = asyncio.create_task(manager.cancel(first["job_id"]))
    await cancellation_started.wait()

    try:
        with pytest.raises(DocumentAnalysisJobError) as raised:
            await manager.start(
                analysis_mode="direct",
                document={"name": "replacement.txt"},
                total_chunks=1,
                runner=replacement_job,
            )
        assert raised.value.diagnostic == "document_job_busy"
        assert replacement_started.is_set() is False
    finally:
        allow_cancellation_to_finish.set()
        await cancel_task

    replacement = await manager.start(
        analysis_mode="direct",
        document={"name": "replacement.txt"},
        total_chunks=1,
        runner=replacement_job,
    )
    await replacement_started.wait()
    await manager.cancel(replacement["job_id"])


@pytest.mark.asyncio
async def test_shutdown_rejects_a_new_start_while_job_cancellation_unwinds() -> None:
    manager = DocumentAnalysisJobManager()
    cancellation_started = asyncio.Event()
    allow_cancellation_to_finish = asyncio.Event()
    replacement_started = asyncio.Event()

    async def running_job(_update):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancellation_started.set()
            await allow_cancellation_to_finish.wait()
            raise

    async def replacement_job(_update):
        replacement_started.set()
        await asyncio.Future()

    await manager.start(
        analysis_mode="direct",
        document={"name": "running.txt"},
        total_chunks=1,
        runner=running_job,
    )
    shutdown_task = asyncio.create_task(manager.shutdown())
    await cancellation_started.wait()

    try:
        with pytest.raises(DocumentAnalysisJobError) as raised:
            await manager.start(
                analysis_mode="direct",
                document={"name": "replacement.txt"},
                total_chunks=1,
                runner=replacement_job,
            )
        assert raised.value.diagnostic == "document_job_busy"
        assert replacement_started.is_set() is False
    finally:
        allow_cancellation_to_finish.set()
        await shutdown_task


@pytest.mark.asyncio
async def test_document_job_entries_hide_jobs_from_other_roles() -> None:
    manager = DocumentAnalysisJobManager()
    started = asyncio.Event()

    async def runner(_update):
        started.set()
        await asyncio.Future()

    job = await manager.start(
        owner_id="alice",
        analysis_mode="direct",
        document={"name": "private.txt", "source_retained": False},
        total_chunks=1,
        runner=runner,
    )
    await started.wait()

    class Owner:
        _document_jobs = manager
        _document_job_manager = StudyCompanionPlugin._document_job_manager

    owner = Owner()
    other_context = {"lanlan_name": "bob"}
    other_active = await StudyCompanionPlugin.study_active_document_analysis(
        owner, _ctx=other_context
    )
    other_status = await StudyCompanionPlugin.study_document_analysis_status(
        owner, job_id=job["job_id"], _ctx=other_context
    )
    other_cancel = await StudyCompanionPlugin.study_cancel_document_analysis(
        owner, job_id=job["job_id"], _ctx=other_context
    )

    assert other_active.value["status"] == "idle"
    assert other_status.value["diagnostic"] == "document_job_not_found"
    assert other_cancel.value["diagnostic"] == "document_job_not_found"

    owner_context = {"lanlan_name": "alice"}
    owner_active = await StudyCompanionPlugin.study_active_document_analysis(
        owner, _ctx=owner_context
    )
    assert owner_active.value["job_id"] == job["job_id"]
    canceled = await StudyCompanionPlugin.study_cancel_document_analysis(
        owner, job_id=job["job_id"], _ctx=owner_context
    )
    assert canceled.value["status"] == "canceled"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_ui_document_jobs_ignore_mutable_cached_character_context() -> None:
    manager = DocumentAnalysisJobManager()
    started = asyncio.Event()

    async def runner(_update):
        started.set()
        await asyncio.Future()

    class Context:
        _current_lanlan = "alice"

    class Owner:
        ctx = Context()
        _document_jobs = manager
        _document_job_manager = StudyCompanionPlugin._document_job_manager
        _resolve_study_target_lanlan = (
            StudyCompanionPlugin._resolve_study_target_lanlan
        )

    owner = Owner()
    job = await manager.start(
        owner_id=document_entries_module._document_job_owner(owner, {}),
        analysis_mode="direct",
        document={"name": "stable.txt", "source_retained": False},
        total_chunks=1,
        runner=runner,
    )
    await started.wait()
    owner.ctx._current_lanlan = "bob"

    active = await StudyCompanionPlugin.study_active_document_analysis(owner)
    status = await StudyCompanionPlugin.study_document_analysis_status(
        owner, job_id=job["job_id"]
    )
    canceled = await StudyCompanionPlugin.study_cancel_document_analysis(
        owner, job_id=job["job_id"]
    )

    assert active.value["job_id"] == job["job_id"]
    assert status.value["job_id"] == job["job_id"]
    assert canceled.value["status"] == "canceled"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_job_status_never_exposes_runner_source() -> None:
    manager = DocumentAnalysisJobManager()
    source = "PRIVATE LONG DOCUMENT SOURCE"

    async def runner(update):
        await update("merging", 1, 1)
        return {"reply": "safe", "summary": "safe", "degraded": False}

    started = await manager.start(
        analysis_mode="direct",
        document={"name": "book.txt", "source_retained": False},
        total_chunks=1,
        runner=runner,
    )
    for _ in range(20):
        status = await manager.status(started["job_id"])
        if status["status"] == "completed":
            break
        await asyncio.sleep(0)
    assert status["status"] == "completed"
    assert source not in repr(status)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_active_recovers_owner_most_recent_retained_terminal_job() -> None:
    manager = DocumentAnalysisJobManager()

    async def completed_runner(_update):
        return {"reply": "completed summary"}

    first = await manager.start(
        owner_id="alice",
        analysis_mode="direct",
        document={"name": "first.txt"},
        total_chunks=1,
        runner=completed_runner,
    )
    for _ in range(20):
        first_status = await manager.status(first["job_id"], owner_id="alice")
        if first_status["status"] == "completed":
            break
        await asyncio.sleep(0)

    second = await manager.start(
        owner_id="alice",
        analysis_mode="direct",
        document={"name": "second.txt"},
        total_chunks=1,
        runner=completed_runner,
    )
    for _ in range(20):
        second_status = await manager.status(second["job_id"], owner_id="alice")
        if second_status["status"] == "completed":
            break
        await asyncio.sleep(0)

    recovered = await manager.active(owner_id="alice")
    other_owner = await manager.active(owner_id="bob")

    assert recovered["job_id"] == second["job_id"]
    assert recovered["status"] == "completed"
    assert recovered["reply"] == "completed summary"
    assert other_owner["status"] == "idle"

    acknowledged = await manager.status(
        second["job_id"], owner_id="alice", acknowledge=True
    )
    assert acknowledged["job_id"] == second["job_id"]
    assert (await manager.active(owner_id="alice"))["status"] == "idle"
    assert (
        await manager.status(second["job_id"], owner_id="alice")
    )["status"] == "completed"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_pending_start_recovery_never_returns_an_unrelated_terminal_job() -> None:
    manager = DocumentAnalysisJobManager()

    async def completed_runner(_update):
        return {"reply": "completed summary"}

    old = await manager.start(
        owner_id="alice",
        start_token="old-start",
        analysis_mode="direct",
        document={"name": "old.txt"},
        total_chunks=1,
        runner=completed_runner,
    )
    for _ in range(20):
        old_status = await manager.status(old["job_id"], owner_id="alice")
        if old_status["status"] == "completed":
            break
        await asyncio.sleep(0)

    unrelated = await manager.active(
        owner_id="alice", start_token="new-start", pending_start=True
    )
    legacy_pending = await manager.active(owner_id="alice", pending_start=True)
    matched = await manager.active(
        owner_id="alice", start_token="old-start", pending_start=True
    )

    assert unrelated["status"] == "idle"
    assert legacy_pending["status"] == "idle"
    assert matched["job_id"] == old["job_id"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_terminal_job_is_removed_after_result_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import document_analysis_jobs as jobs_module

    monkeypatch.setattr(jobs_module, "DOCUMENT_JOB_RESULT_TTL_SECONDS", 0.01)
    manager = DocumentAnalysisJobManager()

    async def runner(_update):
        return {"reply": "safe", "summary": "safe", "degraded": False}

    started = await manager.start(
        analysis_mode="direct",
        document={"name": "notes.txt"},
        total_chunks=1,
        runner=runner,
    )
    for _ in range(20):
        status = await manager.status(started["job_id"])
        if status["status"] == "completed":
            break
        await asyncio.sleep(0)
    assert status["status"] == "completed"
    await asyncio.sleep(0.03)
    with pytest.raises(DocumentAnalysisJobError) as raised:
        await manager.status(started["job_id"])
    assert raised.value.diagnostic == "document_job_not_found"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_chunk_transient_diagnostic_is_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunk = split_document(document.text, document.document_type)[0]
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )
    calls = 0

    async def fake(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = RuntimeError("limited")
            error.diagnostic = "rate_limited"
            raise error
        return "A concise evidence memo."

    monkeypatch.setattr(agent, "_call_model", fake)
    result = await agent.analyze_document_chunk(document, chunk, 1)
    assert result == "A concise evidence memo."
    assert calls == 2


@pytest.mark.asyncio
async def test_chunk_deterministic_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunk = split_document(document.text, document.document_type)[0]
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )
    calls = 0

    async def fake(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        error = RuntimeError("bad credentials")
        error.diagnostic = "authentication_failed"
        raise error

    monkeypatch.setattr(agent, "_call_model", fake)
    with pytest.raises(DocumentChunkAnalysisError) as raised:
        await agent.analyze_document_chunk(document, chunk, 1)
    assert raised.value.diagnostic == "authentication_failed"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic", ["invalid_endpoint", "invalid_request"])
async def test_chunk_preserves_transport_diagnostic(
    monkeypatch: pytest.MonkeyPatch, diagnostic: str
) -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunk = split_document(document.text, document.document_type)[0]
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )

    async def fake(*_args, **_kwargs):
        error = RuntimeError("safe transport failure")
        error.diagnostic = diagnostic
        raise error

    monkeypatch.setattr(agent, "_call_model", fake)
    with pytest.raises(DocumentChunkAnalysisError) as raised:
        await agent.analyze_document_chunk(document, chunk, 1)
    assert raised.value.diagnostic == diagnostic


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic", ["invalid_endpoint", "invalid_request"])
async def test_merge_preserves_transport_diagnostic(
    monkeypatch: pytest.MonkeyPatch, diagnostic: str
) -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunks = split_document(document.text, document.document_type)
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )

    async def fake(*_args, **_kwargs):
        error = RuntimeError("safe transport failure")
        error.diagnostic = diagnostic
        raise error

    monkeypatch.setattr(agent, "_call_model", fake)
    with pytest.raises(DocumentChunkAnalysisError) as raised:
        await agent.merge_document_chunks(document, chunks, ("memo",))
    assert raised.value.diagnostic == diagnostic


@pytest.mark.asyncio
async def test_merge_builds_default_messages_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunks = (
        DocumentChunk(
            index=0,
            text=document.text,
            tokens=document.tokens,
            start_char=0,
            end_char=len(document.text),
            heading_paths=(),
        ),
    )
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )
    thread_calls: list[object] = []

    async def fake_to_thread(function, /, *args, **kwargs):
        thread_calls.append(function)
        return function(*args, **kwargs)

    async def fake_model(*_args, **_kwargs):
        return "A concise whole-document analysis."

    monkeypatch.setattr(document_agent_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(agent, "_call_model", fake_model)

    result = await agent.merge_document_chunks(document, chunks, ("Part memo.",))

    assert result == "A concise whole-document analysis."
    assert thread_calls[0] is build_document_merge_messages


@pytest.mark.asyncio
async def test_chunk_unknown_failure_uses_chunk_diagnostic_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunk = split_document(document.text, document.document_type)[0]
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )
    calls = 0

    async def fake(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("unexpected provider response")

    monkeypatch.setattr(agent, "_call_model", fake)
    with pytest.raises(DocumentChunkAnalysisError) as raised:
        await agent.analyze_document_chunk(document, chunk, 1)
    assert raised.value.diagnostic == "document_chunk_failed"
    assert calls == 1


def test_auto_kind_is_deferred_to_merge_prompt() -> None:
    from plugin.plugins.study_companion.document_analysis import validate_document
    from plugin.plugins.study_companion.document_chunking import split_document

    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="Chapter 1\n\nEvidence and events.",
        analysis_kind="auto",
        locale="en",
    )
    chunks = split_document(document.text, document.document_type)
    chunk_prompt = build_document_chunk_messages(document, chunks[0], len(chunks))
    merge_prompt = build_document_merge_messages(
        document, chunks, tuple("Evidence memo." for _ in chunks)
    )

    assert "Do not classify the whole document" in chunk_prompt[0]["content"]
    assert "Infer one overall content kind" in merge_prompt[0]["content"]


@pytest.mark.asyncio
async def test_start_direct_mode_finalizes_once_without_returning_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "private source"
    calls: list[str] = []

    class Agent:
        async def document_analyze(self, document):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=document.descriptor,
                reply="safe analysis",
            )

    class Owner:
        _agent = Agent()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, reply, **kwargs):
            calls.append(operation)
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": False,
                "diagnostic": "",
            }

    owner = Owner()
    result = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="notes.txt",
        document_type="text/plain",
        document_text=source,
        document_truncated=True,
        locale="en",
    )
    assert isinstance(result, Ok)
    assert result.value["document"]["truncated"] is True
    assert source not in repr(result.value)
    job_id = result.value["job_id"]
    for _ in range(20):
        status = await owner._document_jobs.status(job_id)
        if status["status"] != "running":
            break
        await asyncio.sleep(0)
    assert status["status"] == "completed"
    assert status["document"]["truncated"] is True
    assert calls == [LLM_OPERATION_DOCUMENT_ANALYZE]
    assert source not in repr(status)


@pytest.mark.asyncio
async def test_document_finalization_completes_after_the_absolute_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_jobs_module, "DOCUMENT_JOB_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(
        document_jobs_module, "DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS", 0.15
    )
    finalization_started = asyncio.Event()
    finalization_completed = asyncio.Event()

    class Agent:
        async def document_analyze(self, document):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=document.descriptor,
                reply="preserved analysis",
            )

    class Owner:
        _agent = Agent()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, reply, **kwargs):
            finalization_started.set()
            await asyncio.sleep(0.25)
            finalization_completed.set()
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": False,
                "diagnostic": "",
            }

    owner = Owner()
    started = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="notes.txt",
        document_type="text/plain",
        document_text="deadline-safe source",
        locale="en",
    )
    assert isinstance(started, Ok)
    await asyncio.wait_for(finalization_started.wait(), timeout=1.0)

    for _ in range(100):
        status = await owner._document_jobs.status(started.value["job_id"])
        if status["status"] != "running":
            break
        await asyncio.sleep(0.01)

    assert finalization_completed.is_set()
    assert status["status"] == "completed"
    assert status["reply"] == "preserved analysis"


@pytest.mark.asyncio
async def test_user_cancel_preserves_result_after_document_finalization_starts() -> None:
    finalization_started = asyncio.Event()
    release_finalization = asyncio.Event()
    finalized = 0

    class Agent:
        async def document_analyze(self, document):
            return TutorReply(
                operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                input_text=document.descriptor,
                reply="cancel-safe analysis",
            )

    class Owner:
        _agent = Agent()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, reply, **kwargs):
            nonlocal finalized
            finalization_started.set()
            await release_finalization.wait()
            finalized += 1
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": False,
                "diagnostic": "",
            }

    owner = Owner()
    try:
        started = await StudyCompanionPlugin.study_start_document_analysis(
            owner,
            document_name="notes.txt",
            document_type="text/plain",
            document_text="cancel-safe source",
            locale="en",
        )
        assert isinstance(started, Ok)
        job_id = started.value["job_id"]
        await asyncio.wait_for(finalization_started.wait(), timeout=1.0)

        canceling = asyncio.create_task(owner._document_jobs.cancel(job_id))
        await asyncio.sleep(0)
        release_finalization.set()
        canceled_payload = await asyncio.wait_for(canceling, timeout=1.0)
        status = await owner._document_jobs.status(job_id)

        assert finalized == 1
        assert canceled_payload["status"] == "completed"
        assert canceled_payload["reply"] == "cancel-safe analysis"
        assert status["status"] == "completed"
        assert status["cancellation_source"] == ""
    finally:
        await owner._document_jobs.shutdown()


@pytest.mark.asyncio
async def test_chunked_entry_limits_concurrency_preserves_order_and_finalizes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import (
        entry_document_analysis_jobs as entry_module,
    )

    source_parts = tuple(f"part-{index}\n" for index in range(5))
    source = "".join(source_parts)
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
    offset = 0
    chunks = []
    for index, text in enumerate(source_parts):
        chunks.append(
            DocumentChunk(
                index=index,
                text=text,
                tokens=10_000,
                start_char=offset,
                end_char=offset + len(text),
                heading_paths=((f"Part {index}",),),
            )
        )
        offset += len(text)
    chunk_tuple = tuple(chunks)
    monkeypatch.setattr(entry_module, "validate_document", lambda **_kwargs: document)
    monkeypatch.setattr(entry_module, "split_document", lambda *_args: chunk_tuple)

    active = 0
    maximum_active = 0
    merged_memos: tuple[str, ...] = ()
    finalized = 0

    class Agent:
        build_document_merge_messages = staticmethod(
            lambda _document, _chunks, _memos: [{"role": "user", "content": "merge"}]
        )

        async def analyze_document_chunk(self, _document, chunk, _total):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return f"memo-{chunk.index}"

        async def merge_document_chunks(self, _document, _chunks, memos, *, messages):
            nonlocal merged_memos
            assert messages[0]["content"] == "merge"
            merged_memos = memos
            return "safe whole-document analysis"

    class Owner:
        _agent = Agent()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(self, operation, reply, **kwargs):
            nonlocal finalized
            finalized += 1
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": False,
                "diagnostic": "",
            }

    owner = Owner()
    result = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="book.txt",
        document_type="text/plain",
        document_text=source,
        locale="en",
    )
    assert isinstance(result, Ok)
    assert result.value["analysis_mode"] == "chunked"
    assert result.value["chunks"] == 5
    for _ in range(100):
        status = await owner._document_jobs.status(result.value["job_id"])
        if status["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert status["status"] == "completed"
    assert maximum_active == 2
    assert merged_memos == tuple(f"memo-{index}" for index in range(5))
    assert finalized == 1
    assert source not in repr(status)
    await owner._document_jobs.shutdown()
