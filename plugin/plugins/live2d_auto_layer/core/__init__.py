from .constants import DEFAULT_PARTS
from .types import LayerArtifact, ProcessResult, SegmentMethod

__all__ = [
    "DEFAULT_PARTS",
    "LayerArtifact",
    "ProcessResult",
    "SegmentMethod",
    "process_image",
    "process_layer_set",
    "process_layer_source",
]


def __getattr__(name: str):
    if name in {"process_image", "process_layer_set", "process_layer_source"}:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
