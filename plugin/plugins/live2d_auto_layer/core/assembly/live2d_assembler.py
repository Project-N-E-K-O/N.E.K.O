"""Thin assembly layer for ordering segmented images as Live2D parts."""

from __future__ import annotations

from PIL import Image

from .layer_mapping import DEFAULT_LAYER_ORDER, classify_layer
from .layer_model import Live2DLayer, RawLayer, RawLayerSet


class Live2DAssembler:
    """Normalize extracted layers into a deterministic Live2D part order."""

    def __init__(self, layer_order: list[str] | None = None):
        self.layer_order = list(layer_order or DEFAULT_LAYER_ORDER)
        self._order_index = {name: index for index, name in enumerate(self.layer_order)}

    def assemble(self, layers: RawLayerSet | dict[str, Image.Image]) -> list[Live2DLayer]:
        """Return sorted Live2DLayer entries without changing image pixels."""
        raw_layers = _coerce_raw_layers(layers)
        assembled = [
            Live2DLayer(
                source_name=raw_layer.name,
                part_name=classify_layer(raw_layer.name),
                image=raw_layer.rgba,
                z_index=self._z_index(raw_layer.name),
                bbox=raw_layer.bbox,
                area=raw_layer.area,
                source=raw_layer.source,
                confidence=raw_layer.confidence,
                metadata=dict(raw_layer.metadata),
            )
            for raw_layer in raw_layers
        ]
        return sorted(assembled, key=lambda layer: (layer.z_index, layer.source_name))

    def _z_index(self, source_name: str) -> int:
        part_name = classify_layer(source_name)
        return self._order_index.get(part_name, len(self.layer_order))


def _coerce_raw_layers(layers: RawLayerSet | dict[str, Image.Image]) -> list[RawLayer]:
    if isinstance(layers, RawLayerSet):
        return list(layers.layers)
    return [
        RawLayer(name=name, image=image.convert("RGBA"), source="internal")
        for name, image in layers.items()
    ]
