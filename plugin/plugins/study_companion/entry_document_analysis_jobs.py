from __future__ import annotations

import time

from .constants import LLM_OPERATION_DOCUMENT_ANALYZE
from ._general_narration import prepare_general_narration_content
from .document_analysis import DocumentValidationError, validate_document
from .document_analysis_jobs import DocumentAnalysisJobError, DocumentAnalysisJobManager
from .document_chunking import (
    DOCUMENT_DIRECT_MAX_TOKENS,
    DocumentChunkingError,
    split_document,
)
from .entry_common import asyncio, Ok, SdkError, StudyEvent, plugin_entry, tr, ui
from .models import TutorReply, utc_now_iso
from .tutor_llm_agent_document import _DocumentModelResult
from .tutor_llm_agent_document_chunked import (
    _analyze_document_chunk_result,
    _merge_document_chunks_result,
)


_START_ENTRY_TIMEOUT_SECONDS = 30.0
_STATUS_ENTRY_TIMEOUT_SECONDS = 10.0
_DOCUMENT_CONCURRENCY = 2


def _failed_payload(diagnostic: str) -> dict[str, object]:
    return {
        "job_id": "",
        "status": "failed",
        "stage": "failed",
        "reply": "",
        "summary": "",
        "document": None,
        "degraded": True,
        "diagnostic": diagnostic,
    }


async def _analyze_chunk_with_result(
    agent,
    document,
    chunk,
    total_chunks,
    *,
    deadline_monotonic: float | None = None,
):
    if callable(getattr(agent, "_call_model_result", None)):
        return await _analyze_document_chunk_result(
            agent,
            document,
            chunk,
            total_chunks,
            deadline_monotonic=deadline_monotonic,
        )
    return _DocumentModelResult(
        text=await agent.analyze_document_chunk(document, chunk, total_chunks)
    )


async def _merge_chunks_with_result(
    agent,
    document,
    chunks,
    memos,
    *,
    messages,
    deadline_monotonic: float | None = None,
):
    if callable(getattr(agent, "_call_model_result", None)):
        return await _merge_document_chunks_result(
            agent,
            document,
            chunks,
            memos,
            messages=messages,
            deadline_monotonic=deadline_monotonic,
        )
    return _DocumentModelResult(
        text=await agent.merge_document_chunks(
            document, chunks, memos, messages=messages
        )
    )


class _DocumentAnalysisJobsEntriesMixin:
    def _document_job_manager(self) -> DocumentAnalysisJobManager:
        manager = getattr(self, "_document_jobs", None)
        if not isinstance(manager, DocumentAnalysisJobManager):
            manager = DocumentAnalysisJobManager()
            self._document_jobs = manager
        return manager

    @ui.action()
    @plugin_entry(
        id="study_start_document_analysis",
        name=tr("entries.analyze_document.name", default="Analyze Document"),
        description=tr(
            "entries.analyze_document.description",
            default="Start analysis of one TXT or Markdown document.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "document_name": {"type": "string", "maxLength": 255},
                "document_type": {
                    "type": "string",
                    "enum": ["text/plain", "text/markdown"],
                },
                "document_text": {
                    "type": "string",
                    "writeOnly": True,
                    "x-sensitive": True,
                },
                "analysis_instruction": {
                    "type": "string",
                    "maxLength": 1000,
                    "default": "",
                },
                "analysis_kind": {
                    "type": "string",
                    "enum": [
                        "auto",
                        "literary_book",
                        "nonfiction_book",
                        "design_document",
                        "academic_paper",
                        "exam",
                        "course_material",
                        "general_notes",
                    ],
                    "default": "auto",
                },
                "locale": {"type": "string", "maxLength": 16},
            },
            "required": ["document_name", "document_type", "document_text", "locale"],
        },
        timeout=_START_ENTRY_TIMEOUT_SECONDS,
        llm_result_fields=[
            "job_id",
            "status",
            "stage",
            "analysis_mode",
            "document",
            "chunks",
            "completed_chunks",
            "total_chunks",
            "progress",
            "reply",
            "summary",
            "degraded",
            "diagnostic",
        ],
    )
    async def study_start_document_analysis(
        self,
        document_name: str,
        document_type: str,
        document_text: str,
        analysis_instruction: str = "",
        analysis_kind: str = "auto",
        locale: str = "zh-CN",
        **_,
    ):
        if self._agent is None:
            return Ok(_failed_payload("model_unavailable"))
        try:
            document = await asyncio.to_thread(
                validate_document,
                document_name=document_name,
                document_type=document_type,
                document_text=document_text,
                analysis_instruction=analysis_instruction,
                analysis_kind=analysis_kind,
                locale=locale,
            )
            analysis_mode = (
                "direct" if document.tokens <= DOCUMENT_DIRECT_MAX_TOKENS else "chunked"
            )
            chunks = (
                ()
                if analysis_mode == "direct"
                else await asyncio.to_thread(
                    split_document, document.text, document.document_type
                )
            )
            total_chunks = 1 if analysis_mode == "direct" else len(chunks)
            runner_state = {"document": document, "chunks": chunks}

            async def runner(update, budget):
                job_document = runner_state["document"]
                job_chunks = runner_state["chunks"]
                memos: list[_DocumentModelResult | None] = []
                tasks: list[asyncio.Task[None]] = []
                try:
                    if analysis_mode == "direct":
                        await update("analyzing", 0, 1)
                        remaining = budget.merge_deadline_monotonic - time.monotonic()
                        if remaining <= 0:
                            error = SdkError("document analysis window exhausted")
                            error.diagnostic = "document_analysis_window_exhausted"
                            raise error
                        try:
                            reply = await asyncio.wait_for(
                                self._agent.document_analyze(job_document),
                                timeout=remaining,
                            )
                        except asyncio.TimeoutError as exc:
                            error = SdkError("document analysis window exhausted")
                            error.diagnostic = "document_analysis_window_exhausted"
                            raise error from exc
                        if reply.degraded:
                            error = SdkError("direct document analysis failed")
                            error.diagnostic = reply.diagnostic or "llm_call_failed"
                            raise error
                        await update("merging", 1, 1)
                    else:
                        semaphore = asyncio.Semaphore(_DOCUMENT_CONCURRENCY)
                        progress_lock = asyncio.Lock()
                        completed = 0
                        memos.extend([None] * len(job_chunks))

                        async def analyze_one(chunk):
                            nonlocal completed
                            async with semaphore:
                                memo = await _analyze_chunk_with_result(
                                    self._agent,
                                    job_document,
                                    chunk,
                                    len(job_chunks),
                                    deadline_monotonic=budget.chunk_deadline_monotonic,
                                )
                            memos[chunk.index] = memo
                            async with progress_lock:
                                completed += 1
                                await update(
                                    "analyzing_chunks", completed, len(job_chunks)
                                )

                        tasks.extend(
                            asyncio.create_task(analyze_one(chunk))
                            for chunk in job_chunks
                        )
                        try:
                            remaining = (
                                budget.chunk_deadline_monotonic - time.monotonic()
                            )
                            if remaining <= 0:
                                raise asyncio.TimeoutError
                            await asyncio.wait_for(
                                asyncio.gather(*tasks), timeout=remaining
                            )
                        except asyncio.TimeoutError as exc:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            error = SdkError("document chunk window exhausted")
                            error.diagnostic = "document_chunk_window_exhausted"
                            raise error from exc
                        except BaseException:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            raise
                        await update("merging", len(job_chunks), len(job_chunks))
                        ordered_memos = tuple(
                            memo.text if memo is not None else "" for memo in memos
                        )
                        merge_messages = await asyncio.to_thread(
                            self._agent.build_document_merge_messages,
                            job_document,
                            job_chunks,
                            ordered_memos,
                        )
                        merge_result = await _merge_chunks_with_result(
                            self._agent,
                            job_document,
                            job_chunks,
                            ordered_memos,
                            messages=merge_messages,
                            deadline_monotonic=budget.merge_deadline_monotonic,
                        )
                        truncated_chunk_count = sum(
                            1
                            for memo in memos
                            if memo is not None and memo.output_limit_reached
                        )
                        diagnostic = (
                            "output_truncated"
                            if truncated_chunk_count > 0
                            or merge_result.output_limit_reached
                            else ""
                        )
                        reply = TutorReply(
                            operation=LLM_OPERATION_DOCUMENT_ANALYZE,
                            input_text=(
                                f"[document] {job_document.name} · "
                                f"{job_document.tokens} tokens · {len(job_chunks)} chunks · "
                                f"sha256:{job_document.sha256[:12]}"
                            ),
                            reply=merge_result.text,
                            payload={"document": job_document.public_metadata()},
                            diagnostic=diagnostic,
                            created_at=utc_now_iso(),
                        )
                    if analysis_mode == "direct":
                        truncated_chunk_count = 0
                        merge_output_truncated = False
                    else:
                        merge_output_truncated = merge_result.output_limit_reached
                    metadata = job_document.public_metadata()
                    metadata["chunks"] = total_chunks
                    metadata["analysis_mode"] = analysis_mode
                    finalize_remaining = budget.deadline_monotonic - time.monotonic()
                    if finalize_remaining <= 0:
                        error = SdkError("document finalization window exhausted")
                        error.diagnostic = "document_finalize_timeout"
                        raise error
                    try:
                        payload = await asyncio.wait_for(
                            self._finalize_tutor_call(
                                LLM_OPERATION_DOCUMENT_ANALYZE,
                                reply,
                                history_kind=LLM_OPERATION_DOCUMENT_ANALYZE,
                                metadata={
                                    "degraded": reply.degraded,
                                    "diagnostic": reply.diagnostic,
                                    "document": metadata,
                                    "locale": job_document.locale,
                                    "source_retained": False,
                                    "truncated_chunk_count": truncated_chunk_count,
                                    "total_chunks": total_chunks,
                                    "merge_output_truncated": merge_output_truncated,
                                },
                                public_payload={
                                    "summary": reply.reply,
                                    "document": metadata,
                                },
                            ),
                            timeout=finalize_remaining,
                        )
                    except asyncio.TimeoutError as exc:
                        error = SdkError("document finalization window exhausted")
                        error.diagnostic = "document_finalize_timeout"
                        raise error from exc
                    payload.pop("input_text", None)
                    payload.pop("created_at", None)
                    payload["degraded"] = reply.degraded
                    payload["diagnostic"] = reply.diagnostic
                    return payload
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    memos.clear()
                    runner_state.clear()

            def on_completed(result):
                result["document_narration_scheduled"] = False
                communication = getattr(
                    getattr(self, "_cfg", None), "communication", None
                )
                if not bool(getattr(communication, "enabled", False)):
                    result["document_narration_status"] = "disabled"
                    result["document_narration_reason"] = "communication_disabled"
                    return
                if not bool(getattr(communication, "general_narration_enabled", True)):
                    result["document_narration_status"] = "disabled"
                    result["document_narration_reason"] = "general_narration_disabled"
                    return
                content = prepare_general_narration_content(
                    str(result.get("reply") or "")
                )
                if not content:
                    result["document_narration_status"] = "not_applicable"
                    result["document_narration_reason"] = "empty_reply"
                    return
                bus = getattr(self, "_event_bus", None)
                if bus is None:
                    result["document_narration_status"] = "runtime_unavailable"
                    result["document_narration_reason"] = "event_bus_unavailable"
                    return
                try:
                    scheduled = bus.schedule_emit(
                        StudyEvent(
                            name="general_response_completed",
                            payload={
                                "response_mode": "document_analysis",
                                "content": content,
                            },
                        )
                    )
                except Exception:
                    self.logger.warning("document narration event scheduling failed")
                    scheduled = None
                if scheduled is None:
                    result["document_narration_status"] = "delivery_failed"
                    result["document_narration_reason"] = "event_delivery_failed"
                    return
                result["document_narration_scheduled"] = True
                result["document_narration_status"] = "scheduled"
                result["document_narration_reason"] = ""

            payload = await self._document_job_manager().start(
                analysis_mode=analysis_mode,
                document=document.public_metadata(),
                total_chunks=total_chunks,
                runner=runner,
                on_completed=on_completed,
            )
            return Ok(payload)
        except (
            DocumentValidationError,
            DocumentChunkingError,
            DocumentAnalysisJobError,
        ) as exc:
            return Ok(
                _failed_payload(
                    str(getattr(exc, "diagnostic", "") or "llm_call_failed")
                )
            )
        finally:
            # The job closure owns its validated copy; release the Entry argument.
            document_text = ""

    @ui.action()
    @plugin_entry(
        id="study_document_analysis_status",
        name=tr("entries.analyze_document.name", default="Document Analysis Status"),
        description=tr(
            "entries.analyze_document.description",
            default="Read document analysis progress.",
        ),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string", "maxLength": 128}},
            "required": ["job_id"],
        },
        timeout=_STATUS_ENTRY_TIMEOUT_SECONDS,
    )
    async def study_document_analysis_status(self, job_id: str, **_):
        try:
            return Ok(await self._document_job_manager().status(job_id))
        except DocumentAnalysisJobError as exc:
            return Ok(_failed_payload(exc.diagnostic))

    @ui.action()
    @plugin_entry(
        id="study_active_document_analysis",
        name=tr("entries.analyze_document.name", default="Active Document Analysis"),
        description=tr(
            "entries.analyze_document.description",
            default="Read the active document analysis job.",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=_STATUS_ENTRY_TIMEOUT_SECONDS,
    )
    async def study_active_document_analysis(self, **_):
        return Ok(await self._document_job_manager().active())

    @ui.action()
    @plugin_entry(
        id="study_cancel_document_analysis",
        name=tr("entries.analyze_document.name", default="Cancel Document Analysis"),
        description=tr(
            "entries.analyze_document.description", default="Cancel document analysis."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "maxLength": 128},
                "cancellation_source": {
                    "type": "string",
                    "enum": ["user"],
                    "default": "user",
                },
            },
            "required": ["job_id"],
        },
        timeout=_STATUS_ENTRY_TIMEOUT_SECONDS,
    )
    async def study_cancel_document_analysis(
        self, job_id: str, cancellation_source: str = "user", **_
    ):
        try:
            return Ok(
                await self._document_job_manager().cancel(
                    job_id,
                    source="user",
                )
            )
        except DocumentAnalysisJobError as exc:
            return Ok(_failed_payload(exc.diagnostic))


__all__ = ["_DocumentAnalysisJobsEntriesMixin"]
