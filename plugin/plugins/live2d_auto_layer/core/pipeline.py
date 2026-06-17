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

from .constants import DEFAULT_PARTS
from .config import OUTPUT_DIR, ensure_dirs
from .export import LayerExporter, export_preview_image
from .matting import AlphaRefiner, BackgroundRemover
from .preprocess import Preprocessor
from .session_id import validate_session_id
from .segment import segment_image
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

    _emit(progress, 0.75, "export_layers")
    exporter = LayerExporter(output_dir=session_dir)
    exporter.export_pngs(layers, session_name="layers")
    zip_path = exporter.export_zip(layers, zip_name="live2d_layers.zip")

    _emit(progress, 0.9, "export_preview")
    preview = export_preview_image(layers, max_dim=1024)
    preview_path = session_dir / "preview.png"
    preview.save(preview_path, format="PNG")

    artifacts = [
        LayerArtifact(
            name=name,
            path=str(layers_dir / f"{LayerExporter._sanitize_filename(name)}.png"),
            width=layer.width,
            height=layer.height,
            area=_layer_area(layer),
        )
        for name, layer in layers.items()
    ]
    elapsed = time.perf_counter() - started
    _emit(progress, 1.0, "done")
    result = ProcessResult(
        session_id=session_id,
        status="succeeded",
        message=f"Exported {len(artifacts)} layer(s)",
        output_dir=str(session_dir),
        preview_path=str(preview_path),
        zip_path=str(zip_path),
        manifest_path=str(session_dir / "manifest.json"),
        layers=artifacts,
        warnings=warnings,
        metrics={
            "duration_seconds": round(elapsed, 3),
            "method": method,
            "parts": selected_parts,
        },
    )
    Path(result.manifest_path).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _load_image(image: Image.Image | str | Path) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    return Image.open(Path(image)).convert("RGBA")


def _layer_area(image: Image.Image) -> int:
    if image.mode != "RGBA":
        return image.width * image.height
    alpha = image.getchannel("A")
    return sum(1 for value in alpha.getdata() if value > 10)


def _emit(progress: ProgressCallback | None, value: float, stage: str) -> None:
    if progress is not None:
        progress(value, stage)
