from .constants import DEFAULT_PARTS
from .types import LayerArtifact, ProcessResult, SegmentMethod

__all__ = [
    "DEFAULT_PARTS",
    "LayerArtifact",
    "ProcessResult",
    "SegmentMethod",
    "process_image",
]


def __getattr__(name: str):
    if name == "process_image":
        from .pipeline import process_image

        return process_image
    raise AttributeError(name)
