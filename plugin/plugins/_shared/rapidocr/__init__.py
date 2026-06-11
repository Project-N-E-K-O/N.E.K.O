"""Shared RapidOCR runtime, model download, and OCR backend helpers."""

from .ocr_backends import RapidOcrBackend
from .rapidocr_support import (
    DEFAULT_RAPIDOCR_ENGINE_TYPE,
    DEFAULT_RAPIDOCR_LANG_TYPE,
    DEFAULT_RAPIDOCR_MODEL_TYPE,
    DEFAULT_RAPIDOCR_OCR_VERSION,
    RAPIDOCR_PACKAGE_NAME,
    download_rapidocr_models,
    inspect_rapidocr_installation,
    load_rapidocr_runtime,
)

__all__ = [
    "DEFAULT_RAPIDOCR_ENGINE_TYPE",
    "DEFAULT_RAPIDOCR_LANG_TYPE",
    "DEFAULT_RAPIDOCR_MODEL_TYPE",
    "DEFAULT_RAPIDOCR_OCR_VERSION",
    "RAPIDOCR_PACKAGE_NAME",
    "RapidOcrBackend",
    "download_rapidocr_models",
    "inspect_rapidocr_installation",
    "load_rapidocr_runtime",
]
