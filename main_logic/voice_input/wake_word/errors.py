"""Shared, safe reason vocabulary across workers, diagnostics and activation."""

from enum import Enum


class WakeWordFailureReason(str, Enum):
    RUNTIME_MISSING = "WAKE_WORD_RUNTIME_MISSING"
    RUNTIME_FIX_REQUIRED = "WAKE_WORD_RUNTIME_FIX_REQUIRED"
    MODEL_MISSING = "WAKE_WORD_MODEL_MISSING"
    MODEL_INVALID = "WAKE_WORD_MODEL_INVALID"
    PLATFORM_UNSUPPORTED = "WAKE_WORD_PLATFORM_UNSUPPORTED"
    TOKEN_UNKNOWN = "WAKE_WORD_TOKEN_UNKNOWN"
    PREFERENCE_UNAVAILABLE = "WAKE_WORD_PREFERENCE_UNAVAILABLE"
    TIMEOUT = "WAKE_WORD_TIMEOUT"
    CLOSED = "WAKE_WORD_CLOSED"
    WORKER_FAILED = "WAKE_WORD_WORKER_FAILED"
    CONCURRENT_CALL = "WAKE_WORD_CONCURRENT_CALL"


def safe_wake_word_reason(exc: BaseException | str) -> str:
    if isinstance(exc, ModuleNotFoundError) and (exc.name or "").split(".")[0] == "sherpa_onnx":
        return WakeWordFailureReason.RUNTIME_MISSING.value
    value = str(exc)
    return value if value in WakeWordFailureReason._value2member_map_ else WakeWordFailureReason.WORKER_FAILED.value
