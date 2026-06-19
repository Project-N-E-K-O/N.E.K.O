from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameEvaluateOcrScreenAwarenessModelMixin:
    @plugin_entry(
        id="galgame_evaluate_ocr_screen_awareness_model",
        name=tr("entries.galgame_evaluate_ocr_screen_awareness_model.name", default='评估 OCR 屏幕感知模型'),
        description=tr("entries.galgame_evaluate_ocr_screen_awareness_model.description", default='用已标注 JSONL 样本评估轻量屏幕感知模型，并可输出评估报告。'),
        input_schema={
            "type": "object",
            "properties": {
                "sample_path": {"type": "string", "default": ""},
                "model_path": {"type": "string", "default": "screen_awareness_model.json"},
                "report_path": {"type": "string", "default": ""},
                "allow_rule_labels": {"type": "boolean", "default": False},
                "min_confidence": {"type": "number", "default": 0.55},
            },
        },
        timeout=120.0,
        llm_result_fields=["summary"],
    )
    async def galgame_evaluate_ocr_screen_awareness_model(
        self,
        sample_path: str = "",
        model_path: str = "screen_awareness_model.json",
        report_path: str = "",
        allow_rule_labels: bool = False,
        min_confidence: float = 0.55,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        try:
            resolved_samples = self._resolve_screen_awareness_data_path(
                sample_path,
                default_filename="samples.jsonl",
            )
            resolved_model = self._resolve_screen_awareness_data_path(
                model_path,
                default_filename="screen_awareness_model.json",
            )
            resolved_report = (
                self._resolve_screen_awareness_data_path(
                    report_path,
                    default_filename="screen_awareness_evaluation.json",
                )
                if str(report_path or "").strip()
                else None
            )
            result = await asyncio.to_thread(
                evaluate_screen_awareness_model,
                resolved_samples,
                resolved_model,
                allow_rule_labels=bool(allow_rule_labels),
                min_confidence=float(min_confidence),
                report_path=resolved_report,
            )
        except Exception as exc:
            return Err(SdkError(f"evaluate OCR screen awareness model failed: {exc}"))
        return Ok(json_copy(result))
