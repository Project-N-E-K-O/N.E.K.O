from __future__ import annotations

from typing import Any

from .pipeline_models import QQModelResult, QQPipelineStageTrace, QQReplyContext


class QQReplyModelNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def generate(self, context: QQReplyContext) -> QQModelResult:
        if not self._should_generate(context):
            return QQModelResult(
                reply_text=None,
                source="none",
                traces=[
                    QQPipelineStageTrace(
                        stage="model_primary",
                        status="skipped",
                        metadata={"reason": "permission_blocked"},
                    )
                ],
            )

        primary_result = await self._run_primary(context)
        if not primary_result.allow_fallback:
            return primary_result

        fallback_reply = await self._run_fallback(context)
        primary_result.traces.append(
            QQPipelineStageTrace(
                stage="model_fallback",
                status="success" if fallback_reply else "empty",
                metadata={
                    "reply_length": len(fallback_reply or ""),
                },
            )
        )
        if fallback_reply:
            return QQModelResult(
                reply_text=fallback_reply,
                source="direct_llm_fallback",
                used_fallback=True,
                traces=primary_result.traces,
            )
        return QQModelResult(reply_text=None, source="none", used_fallback=True, traces=primary_result.traces)

    def _should_generate(self, context: QQReplyContext) -> bool:
        # 开放平台：全部生成（不限制权限）
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            return True
        return context.is_group or context.permission_level in ["admin", "trusted"]

    async def _run_primary(self, context: QQReplyContext) -> QQModelResult:
        return await self.plugin.reply_generation_service.run_primary_session_call(context)

    async def _run_fallback(self, context: QQReplyContext) -> str | None:
        return await self.plugin.reply_generation_service.generate_fallback_from_context(context)
