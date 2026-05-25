from __future__ import annotations

from typing import Any, Protocol

from .ocr_rapidocr_backend import RapidOcrBackend
from .ocr_runtime_types import (
    OcrTextBox,
    _CJK_CHAR_RE,
    _KANA_CHAR_RE,
    _LOCAL_RAPIDOCR_INFERENCE_LOCK,
    _OCR_PREPARE_MAX_LONG_EDGE,
    _OCR_PREPARE_TARGET_LONG_EDGE,
    _OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE,
    _RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS,
    _RapidOcrToken,
    _join_ocr_segments,
    _prepare_ocr_image,
    _rapidocr_lines_from_output,
    _rapidocr_points,
    _rapidocr_runtime_cache_key,
    _rapidocr_text_from_output,
    _rapidocr_tokens_from_output,
    _score_ocr_text,
    _should_insert_ascii_space,
    _significant_char_count,
)


class OcrBackend(Protocol):
    def is_available(self) -> bool: ...

    def extract_text(self, image: Any) -> str: ...


def _shared_rapidocr_runtime(
    _key: tuple[str, str, str, str, str, str],
    *,
    now: float,
) -> Any | None:
    from .ocr_runtime_types import _get_rapidocr_runtime_cache

    return _get_rapidocr_runtime_cache(_key, now=now)


def _store_shared_rapidocr_runtime(
    _key: tuple[str, str, str, str, str, str],
    runtime: Any,
    *,
    now: float,
) -> None:
    from .ocr_runtime_types import _store_rapidocr_runtime_cache

    _store_rapidocr_runtime_cache(_key, runtime, now=now)


def _rapidocr_inference_lock() -> Any:
    from .ocr_runtime_types import _RAPIDOCR_INFERENCE_LOCK

    return _RAPIDOCR_INFERENCE_LOCK


__all__ = [
    "OcrBackend",
    "OcrTextBox",
    "RapidOcrBackend",
    "_CJK_CHAR_RE",
    "_KANA_CHAR_RE",
    "_LOCAL_RAPIDOCR_INFERENCE_LOCK",
    "_OCR_PREPARE_MAX_LONG_EDGE",
    "_OCR_PREPARE_TARGET_LONG_EDGE",
    "_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE",
    "_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS",
    "_RapidOcrToken",
    "_join_ocr_segments",
    "_prepare_ocr_image",
    "_rapidocr_inference_lock",
    "_rapidocr_lines_from_output",
    "_rapidocr_points",
    "_rapidocr_runtime_cache_key",
    "_rapidocr_text_from_output",
    "_rapidocr_tokens_from_output",
    "_score_ocr_text",
    "_shared_rapidocr_runtime",
    "_should_insert_ascii_space",
    "_significant_char_count",
    "_store_shared_rapidocr_runtime",
]
