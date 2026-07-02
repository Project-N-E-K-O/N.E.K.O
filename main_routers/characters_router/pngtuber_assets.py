# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PNGTuber asset reference collection/rewrite/packaging helpers used
by character card export/import.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import logger

import shutil
import copy
from pathlib import Path
from utils.config_manager import (
    get_reserved,
)


_PNGTUBER_CARD_MODEL_DIR = "pngtuber"


_PNGTUBER_IMAGE_KEYS = (
    "idle_image",
    "talking_image",
    "drag_image",
    "click_image",
    "happy_image",
    "sad_image",
    "angry_image",
    "surprised_image",
)


_PNGTUBER_PACKABLE_KEYS = (*_PNGTUBER_IMAGE_KEYS, "layered_metadata", "metadata")


def _strip_url_suffix(path: str) -> str:
    return str(path or "").split("?", 1)[0].split("#", 1)[0]


def _pngtuber_user_rel_from_url(value: str) -> str:
    normalized = _strip_url_suffix(str(value or "").strip().replace("\\", "/"))
    prefix = "/user_pngtuber/"
    if not normalized.startswith(prefix):
        return ""
    rel = normalized[len(prefix):]
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return ""
    return rel


def _collect_pngtuber_user_asset_refs(pngtuber_config: dict) -> dict[str, str]:
    refs: dict[str, str] = {}
    if not isinstance(pngtuber_config, dict):
        return refs
    for key in _PNGTUBER_PACKABLE_KEYS:
        rel = _pngtuber_user_rel_from_url(str(pngtuber_config.get(key) or ""))
        if rel:
            refs[key] = rel
    return refs


def _pngtuber_package_roots_from_refs(refs: dict[str, str]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for rel in refs.values():
        parts = Path(rel).parts
        if not parts:
            continue
        root = parts[0]
        if root in ("", ".", "..") or root in seen:
            continue
        roots.append(root)
        seen.add(root)
    return roots


def _with_pngtuber_model_path_rewrites(data, rewrites: dict[str, str]):
    if not rewrites:
        return data
    if isinstance(data, dict):
        return {
            key: _with_pngtuber_model_path_rewrites(value, rewrites)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_with_pngtuber_model_path_rewrites(item, rewrites) for item in data]
    if isinstance(data, str):
        rel = _pngtuber_user_rel_from_url(data)
        if rel in rewrites:
            suffix = ""
            for marker in ("?", "#"):
                index = data.find(marker)
                if index >= 0:
                    suffix = data[index:]
                    break
            return rewrites[rel] + suffix
    return data


def _add_pngtuber_assets_to_character_zip(zf, catgirl_data: dict, config_manager) -> bool:
    pngtuber_config = get_reserved(catgirl_data, "avatar", "pngtuber", default={})
    refs = _collect_pngtuber_user_asset_refs(pngtuber_config)
    if not refs:
        return False
    added = False
    added_arcs: set[str] = set()
    for root_name in _pngtuber_package_roots_from_refs(refs):
        source_root = config_manager.pngtuber_dir / root_name
        if source_root.is_file():
            rel = source_root.relative_to(config_manager.pngtuber_dir).as_posix()
            arc_name = f"model/{_PNGTUBER_CARD_MODEL_DIR}/{rel}"
            if arc_name not in added_arcs:
                zf.write(source_root, arc_name)
                added_arcs.add(arc_name)
                added = True
            continue

        if not source_root.is_dir():
            logger.warning(f"PNGTuber export asset missing, skipping: {source_root}")
            continue

        for file_path in sorted(source_root.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(config_manager.pngtuber_dir).as_posix()
            arc_name = f"model/{_PNGTUBER_CARD_MODEL_DIR}/{rel}"
            if arc_name in added_arcs:
                continue
            zf.write(file_path, arc_name)
            added_arcs.add(arc_name)
            added = True
    return added


def _rewrite_imported_pngtuber_refs(character_data: dict, rel_map: dict[str, str]) -> dict:
    rewrites = {
        rel: f"/user_pngtuber/{new_rel}"
        for rel, new_rel in rel_map.items()
    }
    return _with_pngtuber_model_path_rewrites(character_data, rewrites)


def _restore_imported_pngtuber_avatar_config(character_data: dict, source_data: dict, rel_map: dict[str, str]) -> dict:
    if not isinstance(character_data, dict) or not isinstance(source_data, dict):
        return character_data

    model_type = get_reserved(
        source_data,
        "avatar",
        "model_type",
        default="",
        legacy_keys=("model_type",),
    )
    pngtuber_config = get_reserved(source_data, "avatar", "pngtuber", default={})
    if model_type != "pngtuber" or not isinstance(pngtuber_config, dict):
        return character_data

    restored = {"_reserved": {"avatar": {"pngtuber": copy.deepcopy(pngtuber_config)}}}
    if rel_map:
        restored = _rewrite_imported_pngtuber_refs(restored, rel_map)

    avatar = character_data.setdefault("_reserved", {}).setdefault("avatar", {})
    avatar["model_type"] = "pngtuber"
    avatar["live3d_sub_type"] = ""
    avatar["pngtuber"] = restored["_reserved"]["avatar"]["pngtuber"]
    avatar["asset_source"] = "local_imported"
    avatar["asset_source_id"] = ""
    return character_data


def _copy_imported_pngtuber_assets(model_dir: Path, config_manager) -> dict[str, str]:
    pngtuber_model_dir = model_dir / _PNGTUBER_CARD_MODEL_DIR
    if not pngtuber_model_dir.exists() or not pngtuber_model_dir.is_dir():
        return {}

    config_manager.pngtuber_dir.mkdir(parents=True, exist_ok=True)
    rel_map: dict[str, str] = {}

    for item in pngtuber_model_dir.iterdir():
        if item.name in ("", ".", ".."):
            continue
        target_name = item.name
        target_path = config_manager.pngtuber_dir / target_name
        if target_path.exists():
            counter = 1
            stem = item.stem
            suffix = item.suffix
            while target_path.exists():
                target_name = f"{stem}({counter}){suffix}" if item.is_file() else f"{item.name}({counter})"
                target_path = config_manager.pngtuber_dir / target_name
                counter += 1

        if item.is_dir():
            shutil.copytree(item, target_path)
            for copied in item.rglob("*"):
                if copied.is_file():
                    old_rel = str(copied.relative_to(pngtuber_model_dir)).replace("\\", "/")
                    new_rel = str((Path(target_name) / copied.relative_to(item)).as_posix())
                    rel_map[old_rel] = new_rel
        elif item.is_file():
            shutil.copy2(item, target_path)
            rel_map[item.name] = target_name

    return rel_map
