from __future__ import annotations

import base64
import io
import mimetypes
import re
from pathlib import Path

from PIL import Image

from ..core.constants import DEFAULT_PARTS
from ..core.config import OUTPUT_DIR
from ..core.types import ProcessResult, SegmentMethod
from .environment import EnvironmentService
from .session_store import SessionStore

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[-\w.]+/[-\w.+]+);base64,(?P<payload>.+)$", re.DOTALL)
_MAX_INLINE_IMAGE_BYTES = 16 * 1024 * 1024
_MAX_THUMBNAIL_SIZE = 240


class LayerService:
    def __init__(
        self,
        *,
        output_dir: str | Path = OUTPUT_DIR,
        environment: EnvironmentService | None = None,
        sessions: SessionStore | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.environment = environment or EnvironmentService()
        self.sessions = sessions or SessionStore(self.output_dir)

    def split_image(
        self,
        input_path: str | Path | None = None,
        *,
        input_data_url: str = "",
        session_id: str | None = None,
        method: SegmentMethod = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
    ) -> ProcessResult:
        image_source = self._resolve_image_source(input_path=input_path, input_data_url=input_data_url)
        report = self.environment.check()
        if method == "anime_face" and not report.recommended_method_ready:
            missing = _missing_recommended_requirements(report.to_dict())
            raise RuntimeError(f"AnimeFace+SAM is not ready: {', '.join(missing)}")
        if method == "color" and not report.ready:
            raise RuntimeError("Color fallback is not ready: rembg/cv2/numpy dependencies are missing")
        from ..core.pipeline import process_image

        return process_image(
            image_source,
            output_dir=self.output_dir,
            session_id=session_id,
            method=method,
            parts=parts or DEFAULT_PARTS,
            feather_radius=feather_radius,
            gpt_api_key=gpt_api_key,
        )

    def list_sessions(self) -> list[dict[str, object]]:
        return self.sessions.list_sessions()

    def import_layer_source(
        self,
        layer_source_path: str | Path,
        *,
        session_id: str | None = None,
        source: str = "see_through",
    ) -> ProcessResult:
        from ..core.pipeline import process_layer_source

        return process_layer_source(
            layer_source_path,
            output_dir=self.output_dir,
            session_id=session_id,
            source=source,
        )

    def get_session(self, session_id: str) -> ProcessResult | None:
        return self.sessions.load(session_id)

    def export_cubism_handoff(self, session_id: str) -> dict[str, object]:
        result = self.sessions.load(session_id)
        if result is None:
            raise FileNotFoundError(f"Session not found: {session_id}")
        from ..core.cubism import export_cubism_handoff

        return export_cubism_handoff(result)

    def export_auto_rig_model(
        self,
        session_id: str,
        *,
        mesh_alpha_threshold: int = 10,
    ) -> dict[str, object]:
        result = self.sessions.load(session_id)
        if result is None:
            raise FileNotFoundError(f"Session not found: {session_id}")
        from ..core.auto_rig import export_auto_rig_model

        return export_auto_rig_model(
            result,
            mesh_alpha_threshold=mesh_alpha_threshold,
        )

    def delete_session(self, session_id: str) -> bool:
        return self.sessions.delete(session_id)

    def resegment_session(
        self,
        session_id: str,
        *,
        method: SegmentMethod = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
    ) -> ProcessResult:
        manifest = self.sessions.load(session_id)
        if manifest is None:
            raise FileNotFoundError(f"Session not found: {session_id}")
        foreground = _composite_layers([layer.path for layer in manifest.layers])
        return self._process_pil_image(
            foreground,
            session_id=session_id,
            method=method,
            parts=parts,
            feather_radius=feather_radius,
            gpt_api_key=gpt_api_key,
        )

    def result_to_ui_dict(self, result: ProcessResult) -> dict[str, object]:
        return self.with_inline_artifacts(result.to_dict())

    def with_inline_artifacts(self, data: dict[str, object]) -> dict[str, object]:
        decorated = dict(data)
        preview_path = decorated.get("preview_path")
        if isinstance(preview_path, str):
            decorated["preview_data_url"] = _image_file_to_data_url(preview_path)
        raw_layers = decorated.get("layers")
        if isinstance(raw_layers, list):
            layers: list[object] = []
            for item in raw_layers:
                if not isinstance(item, dict):
                    layers.append(item)
                    continue
                layer = dict(item)
                path = layer.get("path")
                if isinstance(path, str):
                    layer["preview_data_url"] = _image_file_to_data_url(path, max_size=_MAX_THUMBNAIL_SIZE)
                layers.append(layer)
            decorated["layers"] = layers
        return decorated

    def _resolve_image_source(self, *, input_path: str | Path | None, input_data_url: str = "") -> Image.Image | Path:
        data_url = str(input_data_url or "").strip()
        if data_url:
            return _image_from_data_url(data_url)
        path_text = str(input_path or "").strip()
        if _DATA_URL_RE.match(path_text):
            return _image_from_data_url(path_text)
        image_path = Path(path_text).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"Input image not found: {image_path}")
        return image_path

    def _process_pil_image(
        self,
        image: Image.Image,
        *,
        session_id: str,
        method: SegmentMethod,
        parts: list[str] | None,
        feather_radius: int,
        gpt_api_key: str,
    ) -> ProcessResult:
        report = self.environment.check()
        if method == "anime_face" and not report.recommended_method_ready:
            missing = _missing_recommended_requirements(report.to_dict())
            raise RuntimeError(f"AnimeFace+SAM is not ready: {', '.join(missing)}")
        if method == "color" and not report.ready:
            raise RuntimeError("Color fallback is not ready: rembg/cv2/numpy dependencies are missing")
        from ..core.pipeline import process_image

        return process_image(
            image,
            output_dir=self.output_dir,
            session_id=session_id,
            method=method,
            parts=parts or DEFAULT_PARTS,
            feather_radius=feather_radius,
            gpt_api_key=gpt_api_key,
        )


def _missing_recommended_requirements(report: dict[str, object]) -> list[str]:
    missing: list[str] = []
    packages = report.get("python_packages")
    if isinstance(packages, dict):
        missing.extend(str(name) for name, ok in packages.items() if not ok)
    models = report.get("models")
    if isinstance(models, dict):
        missing.extend(str(name) for name, ok in models.items() if not ok)
    return missing


def _image_from_data_url(data_url: str) -> Image.Image:
    match = _DATA_URL_RE.match(data_url)
    if match is None:
        raise ValueError("input_data_url must be a base64 data URL")
    mime = match.group("mime").lower()
    if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        raise ValueError(f"Unsupported image data URL MIME type: {mime}")
    try:
        raw = base64.b64decode(match.group("payload"), validate=True)
    except Exception as exc:
        raise ValueError("input_data_url is not valid base64") from exc
    if len(raw) > _MAX_INLINE_IMAGE_BYTES:
        raise ValueError("input_data_url image is too large")
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:
        raise ValueError("input_data_url is not a readable image") from exc


def _image_file_to_data_url(path: str | Path, *, max_size: int | None = None) -> str:
    image_path = Path(path)
    if not image_path.is_file():
        return ""
    try:
        with Image.open(image_path) as image:
            output = image.convert("RGBA")
            if max_size is not None:
                output.thumbnail((max_size, max_size), Image.LANCZOS)
            buffer = io.BytesIO()
            output.save(buffer, format="PNG")
    except Exception:
        return ""
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    if max_size is not None:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _composite_layers(paths: list[str]) -> Image.Image:
    images: list[Image.Image] = []
    for path in paths:
        image_path = Path(path)
        if not image_path.is_file():
            continue
        try:
            images.append(Image.open(image_path).convert("RGBA"))
        except Exception:
            continue
    if not images:
        raise FileNotFoundError("No readable layer PNGs found for session")
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for image in images:
        if image.size != canvas.size:
            next_canvas = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            next_canvas.alpha_composite(image, (0, 0))
            image = next_canvas
        canvas = Image.alpha_composite(canvas, image)
    return canvas
