# -*- coding: utf-8 -*-
"""Importer for the native N.E.K.O PNGTuber package format."""

import json
from pathlib import Path


def import_simple_package(package_dir: Path) -> dict | None:
    model_path = package_dir / "model.json"
    if not model_path.exists():
        return None
    with model_path.open("r", encoding="utf-8") as f:
        model_json = json.load(f)
    if model_json.get("model_type") != "pngtuber":
        return None
    return model_json
