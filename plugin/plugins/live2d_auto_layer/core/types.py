from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

SegmentMethod = Literal["anime_face", "grounded_sam", "color"]


@dataclass(slots=True)
class LayerArtifact:
    name: str
    path: str
    width: int
    height: int
    area: int

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LayerArtifact":
        return cls(
            name=str(data.get("name") or ""),
            path=str(data.get("path") or ""),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            area=int(data.get("area") or 0),
        )


@dataclass(slots=True)
class ProcessResult:
    session_id: str
    status: str
    message: str
    output_dir: str
    preview_path: str
    zip_path: str
    manifest_path: str
    layers: list[LayerArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "message": self.message,
            "output_dir": self.output_dir,
            "preview_path": self.preview_path,
            "zip_path": self.zip_path,
            "manifest_path": self.manifest_path,
            "layers": [asdict(artifact) for artifact in self.layers],
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ProcessResult":
        raw_layers = data.get("layers")
        layers = [
            LayerArtifact.from_dict(item)
            for item in raw_layers
            if isinstance(item, dict)
        ] if isinstance(raw_layers, list) else []
        raw_warnings = data.get("warnings")
        raw_metrics = data.get("metrics")
        return cls(
            session_id=str(data.get("session_id") or ""),
            status=str(data.get("status") or ""),
            message=str(data.get("message") or ""),
            output_dir=str(data.get("output_dir") or ""),
            preview_path=str(data.get("preview_path") or ""),
            zip_path=str(data.get("zip_path") or ""),
            manifest_path=str(data.get("manifest_path") or ""),
            layers=layers,
            warnings=[
                str(item)
                for item in raw_warnings
                if isinstance(item, str)
            ] if isinstance(raw_warnings, list) else [],
            metrics=dict(raw_metrics) if isinstance(raw_metrics, dict) else {},
        )
