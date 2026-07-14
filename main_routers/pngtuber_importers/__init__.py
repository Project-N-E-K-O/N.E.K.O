# -*- coding: utf-8 -*-
"""Import helpers for third-party PNGTuber package formats."""

from dataclasses import dataclass, field
from pathlib import Path

from .pngtube_remix import PNGTubeRemixConversionError, import_pngtube_remix_model
from .pngtuber_plus import import_pngtuber_plus_save
from .simple_package import import_simple_package
from .veadotube import identify_veadotube


class PNGTuberImportError(ValueError):
    """Raised when a package is recognized but cannot be imported."""

    def __init__(self, message: str, *, source_format: str = "unknown", warnings: list[str] | None = None):
        super().__init__(message)
        self.source_format = source_format
        self.warnings = warnings or []


@dataclass
class PNGTuberImportResult:
    source_format: str
    model_name: str
    model_json: dict
    warnings: list[str] = field(default_factory=list)
    message: str = ""


def _select_pngtuber_plus_save(package_dir: Path, save_files: list[Path], fallback_model_name: str) -> Path:
    if len(save_files) == 1:
        return save_files[0]

    expected_name = f"{fallback_model_name}.save".lower()
    root_matches = [
        path for path in save_files
        if path.parent.resolve() == package_dir.resolve() and path.name.lower() == expected_name
    ]
    if len(root_matches) == 1:
        return root_matches[0]

    candidates = [str(path.relative_to(package_dir)).replace("\\", "/") for path in save_files]
    preview = ", ".join(candidates[:8])
    if len(candidates) > 8:
        preview += f", ... (+{len(candidates) - 8})"
    raise PNGTuberImportError(
        "检测到多个 PNGTuber Plus .save 文件，无法确定要导入哪一个。"
        f"请只保留一个 .save，或把目标文件放在根目录并命名为 {fallback_model_name}.save。"
        f"候选文件: {preview}",
        source_format="pngtuber_plus_save",
        warnings=candidates,
    )


def import_pngtuber_package(package_dir: Path, fallback_model_name: str) -> PNGTuberImportResult:
    """Detect and normalize an uploaded PNGTuber package in-place."""
    simple_result = import_simple_package(package_dir)
    if simple_result:
        return PNGTuberImportResult(
            source_format="simple_package",
            model_name=simple_result.get("name") or fallback_model_name,
            model_json=simple_result,
            message="PNGTuber 模型导入成功",
        )

    save_files = sorted(package_dir.rglob("*.save"))
    if save_files:
        save_file = _select_pngtuber_plus_save(package_dir, save_files, fallback_model_name)
        imported = import_pngtuber_plus_save(package_dir, save_file, fallback_model_name)
        return PNGTuberImportResult(**imported)

    remix_files = sorted([p for p in package_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pngremix"])
    if remix_files:
        try:
            imported = import_pngtube_remix_model(package_dir, remix_files[0], fallback_model_name)
            return PNGTuberImportResult(**imported)
        except PNGTubeRemixConversionError as exc:
            raise PNGTuberImportError(
                f"已识别 PNGTubeRemix 模型，但转换失败: {exc}",
                source_format="pngtube_remix_pngremix",
            ) from exc

    veadotube_files = sorted([
        p for p in package_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".veadomini", ".veado"}
    ])
    if veadotube_files:
        info = identify_veadotube(veadotube_files[0])
        raise PNGTuberImportError(
            "已识别 veadotube 模型文件，但该版本格式暂未支持。请提供样本用于适配。",
            source_format=info["source_format"],
            warnings=info.get("warnings", []),
        )

    image_files = [
        p for p in package_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".png", ".gif", ".jpg", ".jpeg", ".webp"}
    ]
    if image_files:
        raise PNGTuberImportError(
            "检测到图片文件，但未找到 model.json 或第三方 PNGTuber 工程文件。请使用两图导入入口，或补充 idle/talking 配置。",
            source_format="image_pair_candidate",
        )

    raise PNGTuberImportError(
        "PNGTuber 文件夹根目录必须包含 model.json，或包含 .save/.pngRemix/.veadomini/.veado 第三方工程文件。",
        source_format="unknown",
    )
