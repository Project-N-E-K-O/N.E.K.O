"""JSON models for NEKO auto-rig packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class AutoRigLayer:
    id: str
    name: str
    texture: str
    draw_order: int
    width: int
    height: int
    bbox: tuple[int, int, int, int]
    mesh: dict[str, object]
    bindings: list[dict[str, object]]
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AutoRigModel:
    format: str
    version: int
    session_id: str
    canvas_size: tuple[int, int]
    coordinate_space: str
    preview: str
    parameters: list[dict[str, object]]
    layers: list[AutoRigLayer]
    quality: dict[str, object]
    source_manifest: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["layers"] = [layer.to_dict() for layer in self.layers]
        return data
