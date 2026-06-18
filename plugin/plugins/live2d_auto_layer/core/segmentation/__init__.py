"""Segmentation strategies for Live2D auto layer extraction."""

from .segment import ColorSegmenter, SkinDetector, segment_image

__all__ = ["ColorSegmenter", "SkinDetector", "segment_image"]
