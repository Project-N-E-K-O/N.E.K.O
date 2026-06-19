"""
Core Live2D layer extraction pipeline.

This module is the Gradio-free entry point extracted from upstream app.py. It
keeps the image processing flow callable from N.E.K.O plugin entries, tests, or
future Hosted TSX actions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from .assembly import Live2DAssembler, RawLayerSet
from .constants import DEFAULT_PARTS
from .config import OUTPUT_DIR, ensure_dirs
from .exporting import LayerExporter, export_preview_image
from .image import AlphaRefiner, BackgroundRemover, Preprocessor
from .importing import import_layer_source
from .session_id import validate_session_id
from .segmentation import segment_image
from .types import LayerArtifact, ProcessResult, SegmentMethod

ProgressCallback = Callable[[float, str], None]

def process_image(
    image: Image.Image | str | Path,
    *,
    output_dir: str | Path = OUTPUT_DIR,
    session_id: str | None = None,
    parts: list[str] | None = None,
    method: SegmentMethod = "anime_face",
    feather_radius: int = 2,
    gpt_api_key: str = "",
    progress: ProgressCallback | None = None,
) -> ProcessResult:
    """Run the upstream layer extraction flow without Gradio."""

    ensure_dirs()
    started = time.perf_counter()
    if session_id is None:
        session_id = f"session_{int(time.time())}"
    session_id = validate_session_id(session_id)

    selected_parts = list(parts or DEFAULT_PARTS)
    base_output = Path(output_dir)
    session_dir = (base_output / session_id).resolve()
    if session_dir.parent != base_output.resolve():
        raise ValueError("session_id resolves outside output_dir")
    layers_dir = session_dir / "layers"
    session_dir.mkdir(parents=True, exist_ok=True)
    layers_dir.mkdir(parents=True, exist_ok=True)

    pil_image = _load_image(image)
    _emit(progress, 0.05, "preprocess")
    preprocessor = Preprocessor()
    processed = preprocessor.preprocess(pil_image)
    processed.save(session_dir / "input.png", format="PNG")

    _emit(progress, 0.15, "remove_background")
    foreground = BackgroundRemover().remove(processed)

    _emit(progress, 0.25, "refine_alpha")
    alpha_refiner = AlphaRefiner(feather_radius=feather_radius)
    foreground = alpha_refiner.refine(foreground)

    _emit(progress, 0.35, "segment")
    if method == "grounded_sam":
        layers = segment_image(foreground, method="grounded_sam")
    elif method == "color":
        layers = segment_image(foreground, method="color")
    else:
        layers = segment_image(
            foreground,
            method="anime_face",
            parts=selected_parts,
            gpt_api_key=gpt_api_key,
        )

    warnings: list[str] = []
    if not layers:
        layers = {"Foreground": foreground}
        warnings.append("No semantic layers detected; exported foreground only.")

    layer_set = RawLayerSet.from_images(
        layers,
        source="internal",
        source_path=str(session_dir / "input.png"),
    )
    if warnings:
        layer_set = RawLayerSet(
            layers=layer_set.layers,
            source=layer_set.source,
            source_path=layer_set.source_path,
            canvas_size=layer_set.canvas_size,
            warnings=warnings,
            metadata=layer_set.metadata,
        )
    return _export_assembled_layer_set(
        layer_set,
        session_id=session_id,
        session_dir=session_dir,
        layers_dir=layers_dir,
        started=started,
        message_template="Exported {count} layer(s)",
        metrics_base={
            "method": method,
            "parts": selected_parts,
        },
        progress=progress,
        assemble_progress=0.72,
        export_progress=0.78,
        preview_progress=0.9,
    )


def process_layer_source(
    layer_source: str | Path,
    *,
    output_dir: str | Path = OUTPUT_DIR,
    session_id: str | None = None,
    source: str = "see_through",
    progress: ProgressCallback | None = None,
) -> ProcessResult:
    """Import external split layers and export a Live2D-ready layer session."""
    _emit(progress, 0.05, "import_layer_source")
    layer_set = import_layer_source(layer_source, source=source)
    return process_layer_set(
        layer_set,
        output_dir=output_dir,
        session_id=session_id,
        progress=progress,
    )


def process_layer_set(
    layer_set: RawLayerSet,
    *,
    output_dir: str | Path = OUTPUT_DIR,
    session_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> ProcessResult:
    """Assemble an existing layer set into exported Live2D-ready assets."""
    ensure_dirs()
    started = time.perf_counter()
    if session_id is None:
        session_id = f"layer_source_{int(time.time())}"
    session_id = validate_session_id(session_id)

    base_output = Path(output_dir)
    session_dir = (base_output / session_id).resolve()
    if session_dir.parent != base_output.resolve():
        raise ValueError("session_id resolves outside output_dir")
    layers_dir = session_dir / "layers"
    session_dir.mkdir(parents=True, exist_ok=True)
    layers_dir.mkdir(parents=True, exist_ok=True)

    return _export_assembled_layer_set(
        layer_set,
        session_id=session_id,
        session_dir=session_dir,
        layers_dir=layers_dir,
        started=started,
        message_template="Imported and assembled {count} layer(s)",
        metrics_base={
            "method": "layer_source",
            "source": layer_set.source,
            "source_path": layer_set.source_path,
            "canvas_size": list(layer_set.canvas_size or (0, 0)),
        },
        progress=progress,
        assemble_progress=0.25,
        export_progress=0.65,
        preview_progress=0.85,
    )


def _load_image(image: Image.Image | str | Path) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    return Image.open(Path(image)).convert("RGBA")


def _unique_layer_name(name: str, *, seen: set[str]) -> str:
    clean_name = name.strip() or "Layer"
    if clean_name not in seen:
        seen.add(clean_name)
        return clean_name
    index = 2
    while f"{clean_name}_{index}" in seen:
        index += 1
    unique = f"{clean_name}_{index}"
    seen.add(unique)
    return unique


def _export_assembled_layer_set(
    layer_set: RawLayerSet,
    *,
    session_id: str,
    session_dir: Path,
    layers_dir: Path,
    started: float,
    message_template: str,
    metrics_base: dict[str, object],
    progress: ProgressCallback | None,
    assemble_progress: float,
    export_progress: float,
    preview_progress: float,
) -> ProcessResult:
    _emit(progress, assemble_progress, "assemble")
    assembled = Live2DAssembler().assemble(layer_set)
    if not assembled:
        raise ValueError("Layer source did not produce any layers")

    seen_names: set[str] = set()
    export_layers: dict[str, Image.Image] = {}
    for layer in assembled:
        export_layers[_unique_layer_name(layer.part_name, seen=seen_names)] = layer.image

    _emit(progress, export_progress, "export_layers")
    exporter = LayerExporter(output_dir=session_dir)
    exporter.export_pngs(export_layers, session_name="layers")
    zip_path = exporter.export_zip(export_layers, zip_name="live2d_layers.zip")

    _emit(progress, preview_progress, "export_preview")
    preview = export_preview_image(export_layers, max_dim=1024)
    preview_path = session_dir / "preview.png"
    preview.save(preview_path, format="PNG")

    exported_names = list(export_layers.keys())
    artifacts = [
        LayerArtifact(
            name=name,
            path=str(layers_dir / f"{LayerExporter._sanitize_filename(name)}.png"),
            width=layer.image.width,
            height=layer.image.height,
            area=layer.area,
        )
        for name, layer in zip(exported_names, assembled)
    ]
    elapsed = time.perf_counter() - started
    metrics = {
        "duration_seconds": round(elapsed, 3),
        **metrics_base,
        "assembly": [
            {
                "source_name": layer.source_name,
                "part_name": layer.part_name,
                "z_index": layer.z_index,
                "bbox": list(layer.bbox),
                "area": layer.area,
                "source": layer.source,
                "confidence": layer.confidence,
            }
            for layer in assembled
        ],
    }
    result = ProcessResult(
        session_id=session_id,
        status="succeeded",
        message=message_template.format(count=len(artifacts)),
        output_dir=str(session_dir),
        preview_path=str(preview_path),
        zip_path=str(zip_path),
        manifest_path=str(session_dir / "manifest.json"),
        layers=artifacts,
        warnings=list(layer_set.warnings),
        metrics=metrics,
    )
    _emit(progress, 1.0, "done")
    _write_manifest(result)
    return result


def _write_manifest(result: ProcessResult) -> None:
    Path(result.manifest_path).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _emit(progress: ProgressCallback | None, value: float, stage: str) -> None:
    if progress is not None:
        progress(value, stage)
