from __future__ import annotations

from pathlib import Path

from ..core.constants import DEFAULT_PARTS
from ..core.config import OUTPUT_DIR
from ..core.types import ProcessResult, SegmentMethod
from .environment import EnvironmentService
from .session_store import SessionStore


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
        input_path: str | Path,
        *,
        session_id: str | None = None,
        method: SegmentMethod = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
    ) -> ProcessResult:
        image_path = Path(input_path).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"Input image not found: {image_path}")
        report = self.environment.check()
        if method == "anime_face" and not report.recommended_method_ready:
            missing = _missing_recommended_requirements(report.to_dict())
            raise RuntimeError(f"AnimeFace+SAM is not ready: {', '.join(missing)}")
        if method == "color" and not report.ready:
            raise RuntimeError("Color fallback is not ready: rembg/cv2/numpy dependencies are missing")
        from ..core.pipeline import process_image

        return process_image(
            image_path,
            output_dir=self.output_dir,
            session_id=session_id,
            method=method,
            parts=parts or DEFAULT_PARTS,
            feather_radius=feather_radius,
            gpt_api_key=gpt_api_key,
        )

    def list_sessions(self) -> list[dict[str, object]]:
        return self.sessions.list_sessions()

    def get_session(self, session_id: str) -> ProcessResult | None:
        return self.sessions.load(session_id)

    def delete_session(self, session_id: str) -> bool:
        return self.sessions.delete(session_id)


def _missing_recommended_requirements(report: dict[str, object]) -> list[str]:
    missing: list[str] = []
    packages = report.get("python_packages")
    if isinstance(packages, dict):
        missing.extend(str(name) for name, ok in packages.items() if not ok)
    models = report.get("models")
    if isinstance(models, dict):
        missing.extend(str(name) for name, ok in models.items() if not ok)
    return missing
