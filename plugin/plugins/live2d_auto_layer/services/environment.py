from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.config import MODEL_DIR

SAM_VIT_B_FILENAME = "sam_vit_b_01ec64.pth"
LBP_CASCADE_FILENAME = "lbpcascade_animeface.xml"

COLOR_RUNTIME_REQUIREMENTS = [
    "Pillow>=10.0",
    "numpy>=1.24",
    "opencv-python-headless>=4.8",
    "scikit-learn>=1.3",
    "scikit-image>=0.21",
    "scipy>=1.10",
    "rembg[cpu]>=2.0",
    "onnxruntime>=1.17",
]

ANIME_FACE_RUNTIME_REQUIREMENTS = [
    "torch>=2.0",
    "torchvision>=0.15",
    "segment-anything>=1.0",
]


@dataclass(slots=True)
class EnvironmentReport:
    python_packages: dict[str, bool]
    models: dict[str, bool]
    devices: dict[str, bool]
    ready: bool
    recommended_method_ready: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class EnvironmentService:
    def __init__(self, model_dir: str | Path = MODEL_DIR):
        self.model_dir = Path(model_dir)

    def check(self) -> EnvironmentReport:
        packages = {
            "PIL": _has_module("PIL"),
            "cv2": _has_module("cv2"),
            "numpy": _has_module("numpy"),
            "rembg": _has_module("rembg"),
            "onnxruntime": _has_module("onnxruntime"),
            "torch": _has_module("torch"),
            "segment_anything": _has_module("segment_anything"),
        }
        models = {
            "lbpcascade_animeface": self.lbpcascade_path.exists(),
            "sam_vit_b": self.sam_vit_b_path.exists(),
        }
        devices = self._device_report(packages["torch"])
        warnings: list[str] = []
        if not packages["PIL"]:
            warnings.append("Pillow is missing: install Pillow.")
        if not packages["cv2"]:
            warnings.append("OpenCV is missing: install opencv-python-headless.")
        if not packages["numpy"]:
            warnings.append("NumPy is missing: install numpy.")
        if not packages["rembg"]:
            warnings.append("rembg is missing: install rembg[cpu].")
        if not packages["onnxruntime"]:
            warnings.append("rembg CPU backend is missing: install rembg[cpu] or onnxruntime.")
        if not packages["torch"]:
            warnings.append("Torch is missing: AnimeFace+SAM mode is unavailable.")
        if not packages["segment_anything"]:
            warnings.append("segment-anything is missing: AnimeFace+SAM mode is unavailable.")
        if not models["sam_vit_b"]:
            warnings.append(f"SAM vit_b checkpoint is missing: {self.sam_vit_b_path}")
        if not models["lbpcascade_animeface"]:
            warnings.append(f"Anime face cascade is missing: {self.lbpcascade_path}")
        recommended_ready = all([
            packages["PIL"],
            packages["cv2"],
            packages["numpy"],
            packages["rembg"],
            packages["onnxruntime"],
            packages["torch"],
            packages["segment_anything"],
            models["lbpcascade_animeface"],
            models["sam_vit_b"],
        ])
        color_ready = all([
            packages["PIL"],
            packages["cv2"],
            packages["numpy"],
            packages["rembg"],
            packages["onnxruntime"],
        ])
        return EnvironmentReport(
            python_packages=packages,
            models=models,
            devices=devices,
            ready=color_ready,
            recommended_method_ready=recommended_ready,
            warnings=warnings,
        )

    @property
    def sam_vit_b_path(self) -> Path:
        return self.model_dir / SAM_VIT_B_FILENAME

    @property
    def lbpcascade_path(self) -> Path:
        return self.model_dir / LBP_CASCADE_FILENAME

    def _device_report(self, has_torch: bool) -> dict[str, bool]:
        if not has_torch:
            return {"cuda": False, "mps": False}
        try:
            import torch

            return {
                "cuda": bool(torch.cuda.is_available()),
                "mps": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
            }
        except Exception:
            return {"cuda": False, "mps": False}


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
