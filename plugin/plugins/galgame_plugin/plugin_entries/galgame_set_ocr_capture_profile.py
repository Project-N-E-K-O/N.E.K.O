from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetOcrCaptureProfileMixin:
    @plugin_entry(
        id="galgame_set_ocr_capture_profile",
        name=tr("entries.galgame_set_ocr_capture_profile.name", default='设置 OCR 截图校准'),
        description=tr("entries.galgame_set_ocr_capture_profile.description", default='按进程名保存或清除 OCR Reader 的截图裁剪配置。'),
        input_schema={
            "type": "object",
            "properties": {
                "process_name": {"type": "string", "default": ""},
                "stage": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_STAGES),
                    "default": OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
                },
                "save_scope": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_SAVE_SCOPES),
                },
                "left_inset_ratio": {"type": "number", "default": 0.05},
                "right_inset_ratio": {"type": "number", "default": 0.05},
                "top_ratio": {"type": "number", "default": 0.3},
                "bottom_inset_ratio": {"type": "number", "default": 0.3},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_capture_profile(
        self,
        process_name: str = "",
        stage: str = OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        left_inset_ratio: float = 0.05,
        right_inset_ratio: float = 0.05,
        top_ratio: float = 0.3,
        bottom_inset_ratio: float = 0.3,
        save_scope: str | None = None,
        clear: bool = False,
        **_,
    ):
        def _parse_ratio(name: str, value: float) -> float:
            if isinstance(value, bool):
                raise ValueError(f"{name} must be a number")
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a number") from exc
            if parsed < 0.0 or parsed >= 1.0:
                raise ValueError(f"{name} must be >= 0.0 and < 1.0")
            return parsed

        with self._state_lock:
            runtime_process_name = str(
                (self._state.ocr_reader_runtime or {}).get("process_name") or ""
            ).strip()
        normalized_process_name = str(process_name or "").strip() or runtime_process_name
        if not normalized_process_name:
            return Err(SdkError("process_name is required"))

        if clear:
            normalized_profile: dict[str, float] | None = None
        else:
            try:
                normalized_profile = {
                    "left_inset_ratio": _parse_ratio("left_inset_ratio", left_inset_ratio),
                    "right_inset_ratio": _parse_ratio("right_inset_ratio", right_inset_ratio),
                    "top_ratio": _parse_ratio("top_ratio", top_ratio),
                    "bottom_inset_ratio": _parse_ratio(
                        "bottom_inset_ratio",
                        bottom_inset_ratio,
                    ),
                }
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            if (
                normalized_profile["left_inset_ratio"]
                + normalized_profile["right_inset_ratio"]
            ) >= 1.0:
                return Err(SdkError("left_inset_ratio + right_inset_ratio must be < 1.0"))
            if (
                normalized_profile["top_ratio"]
                + normalized_profile["bottom_inset_ratio"]
            ) >= 1.0:
                return Err(SdkError("top_ratio + bottom_inset_ratio must be < 1.0"))
        try:
            payload = await self._save_ocr_capture_profile_payload(
                process_name=normalized_process_name,
                stage=stage,
                capture_profile=normalized_profile,
                clear=bool(clear),
                save_scope=save_scope,
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:
            return Err(SdkError(f"persist OCR capture profile failed: {exc}"))
        return Ok(payload)
