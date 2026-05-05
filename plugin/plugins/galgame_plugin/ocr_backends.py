from __future__ import annotations

from typing import Any

from . import ocr_reader as _ocr_reader

_CJK_CHAR_RE_PATTERN = _ocr_reader._CJK_CHAR_RE.pattern
_KANA_CHAR_RE_PATTERN = _ocr_reader._KANA_CHAR_RE.pattern
_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE = _ocr_reader._OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE
_OCR_PREPARE_TARGET_LONG_EDGE = _ocr_reader._OCR_PREPARE_TARGET_LONG_EDGE
_OCR_PREPARE_MAX_LONG_EDGE = _ocr_reader._OCR_PREPARE_MAX_LONG_EDGE
_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS = _ocr_reader._RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS
_LOCAL_RAPIDOCR_INFERENCE_LOCK = _ocr_reader._RAPIDOCR_INFERENCE_LOCK

OcrBackend = _ocr_reader.OcrBackend
OcrTextBox = _ocr_reader.OcrTextBox
_RapidOcrToken = _ocr_reader._RapidOcrToken
TesseractOcrBackend = _ocr_reader.TesseractOcrBackend
RapidOcrBackend = _ocr_reader.RapidOcrBackend

_score_ocr_text = _ocr_reader._score_ocr_text
_significant_char_count = _ocr_reader._significant_char_count
_prepare_ocr_image = _ocr_reader._prepare_ocr_image
_rapidocr_points = _ocr_reader._rapidocr_points
_should_insert_ascii_space = _ocr_reader._should_insert_ascii_space
_join_ocr_segments = _ocr_reader._join_ocr_segments
_rapidocr_tokens_from_output = _ocr_reader._rapidocr_tokens_from_output
_rapidocr_lines_from_output = _ocr_reader._rapidocr_lines_from_output
_rapidocr_text_from_output = _ocr_reader._rapidocr_text_from_output
_rapidocr_runtime_cache_key = _ocr_reader._rapidocr_runtime_cache_key


def _ocr_reader_compat_symbol(name: str, fallback: Any) -> Any:
    return getattr(_ocr_reader, name, fallback)


def _shared_rapidocr_runtime(
    key: tuple[str, str, str, str, str],
    *,
    now: float,
) -> Any | None:
    with _ocr_reader._RAPIDOCR_RUNTIME_CACHE_LOCK:
        return _ocr_reader._get_rapidocr_runtime_cache(key, now=now)


def _store_shared_rapidocr_runtime(
    key: tuple[str, str, str, str, str],
    runtime: Any,
    *,
    now: float,
) -> None:
    with _ocr_reader._RAPIDOCR_RUNTIME_CACHE_LOCK:
        _ocr_reader._store_rapidocr_runtime_cache(key, runtime, now=now)


def _rapidocr_inference_lock() -> Any:
    return _ocr_reader._RAPIDOCR_INFERENCE_LOCK


def __getattr__(name: str) -> Any:
    try:
        return getattr(_ocr_reader, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


__all__ = [
    "OcrBackend",
    "OcrTextBox",
    "RapidOcrBackend",
    "TesseractOcrBackend",
    "_CJK_CHAR_RE_PATTERN",
    "_KANA_CHAR_RE_PATTERN",
    "_LOCAL_RAPIDOCR_INFERENCE_LOCK",
    "_OCR_PREPARE_MAX_LONG_EDGE",
    "_OCR_PREPARE_TARGET_LONG_EDGE",
    "_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE",
    "_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS",
    "_RapidOcrToken",
    "_join_ocr_segments",
    "_ocr_reader_compat_symbol",
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
