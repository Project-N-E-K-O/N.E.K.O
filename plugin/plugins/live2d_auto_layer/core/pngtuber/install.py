"""Install generated PNGTuber packages into the NEKO user PNGTuber library."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from main_routers.pngtuber_importers import import_pngtuber_package
from main_routers.pngtuber_router import _normalize_pngtuber_config, _slugify_name, _validate_model_package
from utils.config_manager import get_config_manager


def install_pngtuber_package(
    package_dir: str | Path,
    *,
    model_name: str = "",
    preferred_folder: str = "",
    pngtuber_dir: str | Path | None = None,
) -> dict[str, object]:
    """Copy a generated package into the user PNGTuber directory."""
    source_dir = Path(package_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"PNGTuber package directory not found: {source_dir}")

    root = Path(pngtuber_dir) if pngtuber_dir is not None else _default_pngtuber_dir()
    root.mkdir(parents=True, exist_ok=True)

    seed = preferred_folder.strip() or model_name.strip() or source_dir.name
    target_name = _unique_model_dir_name(root, _slugify_name(seed))
    temp_dir = root / f".{target_name}.installing"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    try:
        shutil.copytree(source_dir, temp_dir)
        import_result = import_pngtuber_package(temp_dir, target_name)
        model_json = import_result.model_json
        if model_name.strip():
            model_json["name"] = model_name.strip()

        ok, error = _validate_model_package(temp_dir, model_json)
        if not ok:
            raise ValueError(error)

        display_name = str(model_json.get("name") or import_result.model_name or target_name).strip()
        if not preferred_folder.strip():
            target_name = _unique_model_dir_name(root, _slugify_name(display_name or target_name))
            final_temp_dir = root / f".{target_name}.installing"
            if final_temp_dir != temp_dir:
                if final_temp_dir.exists():
                    shutil.rmtree(final_temp_dir)
                temp_dir.rename(final_temp_dir)
                temp_dir = final_temp_dir

        source_format = str(model_json.get("source_format") or import_result.source_format or "simple_package")
        normalized_config = _normalize_pngtuber_config(target_name, model_json)
        model_json["model_type"] = "pngtuber"
        model_json["pngtuber"] = normalized_config
        model_json["source_format"] = source_format
        (temp_dir / "model.json").write_text(
            json.dumps(model_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        target_dir = root / target_name
        temp_dir.rename(target_dir)
        return {
            "success": True,
            "model_type": "pngtuber",
            "model_name": display_name or target_name,
            "name": display_name or target_name,
            "folder": target_name,
            "url": f"/user_pngtuber/{target_name}/model.json",
            "pngtuber": normalized_config,
            "source_format": source_format,
            "warnings": import_result.warnings,
            "message": f"Installed PNGTuber model {display_name or target_name}",
        }
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _default_pngtuber_dir() -> Path:
    config_mgr = get_config_manager()
    if not config_mgr.ensure_pngtuber_directory():
        raise RuntimeError("PNGTuber directory creation failed")
    return Path(config_mgr.pngtuber_dir)


def _unique_model_dir_name(root: Path, base_name: str) -> str:
    clean_base = _slugify_name(base_name)
    candidate = clean_base
    index = 2
    while (root / candidate).exists() or (root / f".{candidate}.installing").exists():
        candidate = f"{clean_base}-{index}"
        index += 1
    return candidate
