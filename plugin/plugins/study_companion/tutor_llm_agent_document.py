from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _DocumentModelResult:
    text: str
    output_limit_reached: bool = False


async def _call_document_model_result(
    self: Any,
    messages: list[dict[str, Any]],
    *,
    operation: str,
    deadline: float,
) -> _DocumentModelResult:
    # Preserve compatibility with instance-level test doubles and private callers
    # that replace the legacy string helper directly.
    if "_call_model" in getattr(self, "__dict__", {}):
        content = await self._call_model(
            messages,
            operation=operation,
            deadline=deadline,
        )
        return _DocumentModelResult(text=str(content or ""))
    result = await self._call_model_result(
        messages,
        operation=operation,
        deadline=deadline,
    )
    return _DocumentModelResult(
        text=str(result.text or ""),
        output_limit_reached=bool(result.output_limit_reached),
    )


async def document_analyze(self: Any, document: ValidatedDocument) -> TutorReply:
    messages = build_document_analysis_messages(document)
    try:
        deadline = self._new_operation_deadline(
            LLM_OPERATION_DOCUMENT_ANALYZE, messages
        )
        model_result = await _call_document_model_result(
            self,
            messages,
            operation=LLM_OPERATION_DOCUMENT_ANALYZE,
            deadline=deadline,
        )
        reply = model_result.text.strip()
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
            diagnostic=(
                "output_truncated" if model_result.output_limit_reached else ""
            ),
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
