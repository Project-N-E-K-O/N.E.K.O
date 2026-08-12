from __future__ import annotations

from .entry_common import asyncio, Err, Ok, SdkError, plugin_entry, tr, ui
from .constants import LLM_OPERATION_DOCUMENT_ANALYZE
from .document_analysis import (
    DOCUMENT_ANALYSIS_KINDS,
    DOCUMENT_ENTRY_TIMEOUT_SECONDS,
    DocumentValidationError,
    validate_document,
)
from .document_chunking import DOCUMENT_DIRECT_MAX_TOKENS


class _DocumentAnalysisEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_analyze_document",
        name=tr("entries.analyze_document.name", default="Analyze Document"),
        description=tr(
            "entries.analyze_document.description",
            default="Analyze one small TXT or Markdown document with the study agent.",
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
                    "enum": list(DOCUMENT_ANALYSIS_KINDS),
                    "default": "auto",
                },
                "locale": {"type": "string", "maxLength": 16},
            },
            "required": [
                "document_name",
                "document_type",
                "document_text",
                "locale",
            ],
        },
        timeout=DOCUMENT_ENTRY_TIMEOUT_SECONDS,
        llm_result_fields=["reply", "summary", "document", "degraded", "diagnostic"],
    )
    async def study_analyze_document(
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
            return Err(SdkError("study tutor agent is not initialized"))
        try:
            document = await asyncio.to_thread(
                validate_document,
                document_name=document_name,
                document_type=document_type,
                document_text=document_text,
                analysis_instruction=analysis_instruction,
                analysis_kind=analysis_kind,
                locale=locale,
                max_tokens=DOCUMENT_DIRECT_MAX_TOKENS,
            )
        except DocumentValidationError as exc:
            return Ok(
                {
                    "operation": LLM_OPERATION_DOCUMENT_ANALYZE,
                    "reply": "",
                    "summary": "",
                    "document": None,
                    "degraded": True,
                    "diagnostic": exc.diagnostic,
                }
            )

        reply = await self._agent.document_analyze(document)
        public_payload = {
            "summary": reply.reply,
            "document": document.public_metadata(),
        }
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_DOCUMENT_ANALYZE,
            reply,
            history_kind=LLM_OPERATION_DOCUMENT_ANALYZE,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "document": document.public_metadata(),
                "locale": document.locale,
                "source_retained": False,
            },
            public_payload=public_payload,
        )
        payload.pop("input_text", None)
        payload.pop("created_at", None)
        return Ok(payload)


__all__ = ["_DocumentAnalysisEntriesMixin"]
