# -*- coding: utf-8 -*-
"""Recognition helpers for veadotube package formats."""

import zipfile
from pathlib import Path


def identify_veadotube(path: Path) -> dict:
    info = {
        "source_format": "veadotube",
        "file": path.name,
        "is_zip": zipfile.is_zipfile(path),
        "warnings": [],
    }
    if info["is_zip"]:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        info["yaml_files"] = [name for name in names if name.lower().endswith((".yaml", ".yml"))]
        info["image_files"] = [name for name in names if name.lower().endswith((".png", ".gif", ".jpg", ".jpeg", ".webp"))]
    else:
        info["warnings"].append("该 veadotube 文件不是 zip 容器，可能是旧版二进制格式")
    return info
