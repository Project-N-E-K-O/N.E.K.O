"""Headless theater workshop SDK. Explicitly open it with host capabilities."""
from .workshop import TheaterWorkshop, WorkshopError
from .model import ModelCall, ModelReply, LLMCallFailure
from .packages import PackageError, PublishCandidate
from .generation.numeric_v2 import NumericV2GenerationError
from .generation.quality import QualityAssessmentError
from .numeric_v2_branch import NumericV2BranchError
from .numeric_v2_project_store import (
    NumericV2ProjectError, NumericV2ProjectNotFoundError, NumericV2RevisionConflictError,
)

__version__ = "0.1.0"
__all__ = [
    "TheaterWorkshop", "WorkshopError", "ModelCall", "ModelReply", "LLMCallFailure",
    "PackageError", "PublishCandidate", "NumericV2ProjectError",
    "NumericV2ProjectNotFoundError", "NumericV2RevisionConflictError",
    "NumericV2GenerationError", "QualityAssessmentError", "NumericV2BranchError",
]
