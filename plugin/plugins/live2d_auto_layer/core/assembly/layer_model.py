"""Shared layer models for imported and assembled Live2D assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class RawLayer:
    """A layer produced by an internal segmenter or external worker."""

    name: str
    image: Image.Image
    source: str = "unknown"
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def bbox(self) -> BBox:
        alpha = self.rgba.getchannel("A")
        box = alpha.getbbox()
        if box is None:
            return (0, 0, 0, 0)
        left, top, right, bottom = box
        return (left, top, right - left, bottom - top)

    @property
    def area(self) -> int:
        alpha = np.array(self.rgba.getchannel("A"))
        return int(np.sum(alpha > 10))

    @property
    def rgba(self) -> Image.Image:
        return self.image if self.image.mode == "RGBA" else self.image.convert("RGBA")


@dataclass(frozen=True)
class RawLayerSet:
    """A normalized collection of layers from one external or internal source."""

    layers: list[RawLayer]
    source: str = "unknown"
    source_path: str = ""
    canvas_size: tuple[int, int] | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_images(
        cls,
        layers: dict[str, Image.Image],
        *,
        source: str = "internal",
        source_path: str | Path = "",
    ) -> "RawLayerSet":
        raw_layers = [
            RawLayer(name=name, image=image.convert("RGBA"), source=source)
            for name, image in layers.items()
        ]
        canvas_size = _largest_canvas(raw_layers)
        return cls(
            layers=raw_layers,
            source=source,
            source_path=str(source_path),
            canvas_size=canvas_size,
        )


@dataclass(frozen=True)
class Live2DLayer:
    """A segmented image with a Live2D-oriented part name and draw order."""

    source_name: str
    part_name: str
    image: Image.Image
    z_index: int
    bbox: BBox
    area: int
    source: str = "unknown"
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _largest_canvas(layers: list[RawLayer]) -> tuple[int, int] | None:
    if not layers:
        return None
    return (
        max(layer.rgba.width for layer in layers),
        max(layer.rgba.height for layer in layers),
    )
