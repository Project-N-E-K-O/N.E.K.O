from __future__ import annotations

from typing import Any
import asyncio

from .constants import LLM_OPERATION_DOCUMENT_ANALYZE
from .document_analysis import (
    ValidatedDocument,
    build_document_analysis_messages,
    contains_full_document_source,
)
from .models import TutorReply, utc_now_iso
from .tutor_llm_agent_common import SdkError, diagnostic_code_for_exception


_DOCUMENT_FALLBACKS = {
    "en": "Document analysis failed. Please try again later.",
    "zh-CN": "文档分析失败，请稍后重试。",
    "zh-TW": "文件分析失敗，請稍後重試。",
    "ja": "ドキュメントの分析に失敗しました。しばらくしてから再試行してください。",
    "ko": "문서 분석에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    "es": "No se pudo analizar el documento. Inténtalo de nuevo más tarde.",
    "pt": "Não foi possível analisar o documento. Tente novamente mais tarde.",
    "ru": "Не удалось проанализировать документ. Повторите попытку позже.",
}


async def document_analyze(self: Any, document: ValidatedDocument) -> TutorReply:
    messages = build_document_analysis_messages(document)
    try:
        deadline = self._new_operation_deadline(
            LLM_OPERATION_DOCUMENT_ANALYZE, messages
        )
        content = await self._call_model(
            messages,
            operation=LLM_OPERATION_DOCUMENT_ANALYZE,
            deadline=deadline,
        )
        reply = str(content or "").strip()
        if not reply:
            raise SdkError("empty model response")
        if contains_full_document_source(reply, document.text):
            error = SdkError("model response repeated the complete document source")
            error.diagnostic = "unsafe_model_output"
            raise error
        return TutorReply(
            operation=LLM_OPERATION_DOCUMENT_ANALYZE,
            input_text=document.descriptor,
            reply=reply,
            payload={"document": document.public_metadata()},
            degraded=False,
            created_at=utc_now_iso(),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self._logger.warning("study document_analyze degraded: {}", exc)
        diagnostic = diagnostic_code_for_exception(exc)
        fallback = _DOCUMENT_FALLBACKS.get(document.locale, _DOCUMENT_FALLBACKS["en"])
        return TutorReply(
            operation=LLM_OPERATION_DOCUMENT_ANALYZE,
            input_text=document.descriptor,
            reply=fallback,
            payload={"document": document.public_metadata()},
            degraded=True,
            diagnostic=diagnostic,
            created_at=utc_now_iso(),
        )


__all__ = ["document_analyze"]
