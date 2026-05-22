from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameTrainOcrScreenAwarenessModelMixin:
    @plugin_entry(
        id="galgame_train_ocr_screen_awareness_model",
        name=tr("entries.galgame_train_ocr_screen_awareness_model.name", default='训练 OCR 屏幕感知模型'),
        description=tr("entries.galgame_train_ocr_screen_awareness_model.description", default='从已标注 JSONL 样本训练轻量原型分类器，并导出可部署 JSON 模型。'),
        input_schema={
            "type": "object",
            "properties": {
                "sample_path": {"type": "string", "default": ""},
                "output_path": {"type": "string", "default": "screen_awareness_model.json"},
                "allow_rule_labels": {"type": "boolean", "default": False},
                "validation_ratio": {"type": "number", "default": 0.2},
                "min_samples_per_stage": {"type": "integer", "default": 2},
                "min_confidence": {"type": "number", "default": 0.55},
            },
        },
        timeout=120.0,
        llm_result_fields=["summary"],
    )
    async def galgame_train_ocr_screen_awareness_model(
        self,
        sample_path: str = "",
        output_path: str = "screen_awareness_model.json",
        allow_rule_labels: bool = False,
        validation_ratio: float = 0.2,
        min_samples_per_stage: int = 2,
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
            resolved_output = self._resolve_screen_awareness_data_path(
                output_path,
                default_filename="screen_awareness_model.json",
            )
            result = await asyncio.to_thread(
                train_screen_awareness_model,
                resolved_samples,
                resolved_output,
                allow_rule_labels=bool(allow_rule_labels),
                validation_ratio=float(validation_ratio),
                min_samples_per_stage=int(min_samples_per_stage),
                min_confidence=float(min_confidence),
            )
        except Exception as exc:
            return Err(SdkError(f"train OCR screen awareness model failed: {exc}"))
        payload = {
            "output_path": str(result.get("output_path") or resolved_output),
            "evaluation": json_copy(result.get("evaluation") or {}),
            "model": json_copy(result.get("model") or {}),
            "summary": str(result.get("summary") or "OCR screen awareness model trained"),
        }
        return Ok(payload)
