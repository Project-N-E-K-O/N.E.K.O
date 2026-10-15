"""Optional local target-speaker extraction; importing does not load ONNX."""

from .contracts import (
    TseAudioChunk,
    TseExtractionResult,
    TseFailureReason,
    TseModelError,
    TseOutputStatus,
    TseTargetAbsentError,
    TseTimeoutError,
    TseWorkerFailureError,
)
from .models import TseEncoder, TseModel
from .streaming import TseStream

__all__ = [
    "TseAudioChunk",
    "TseEncoder",
    "TseExtractionResult",
    "TseFailureReason",
    "TseModel",
    "TseModelError",
    "TseOutputStatus",
    "TseStream",
    "TseTargetAbsentError",
    "TseTimeoutError",
    "TseWorkerFailureError",
]
