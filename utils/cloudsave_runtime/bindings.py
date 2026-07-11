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

"""Character binding derivation (model reference, asset source, resolved
asset paths, origin metadata) and catalog payload construction/parsing.

Split out of the former monolithic ``utils/cloudsave_runtime.py``.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from config import CHARACTER_RESERVED_FIELDS

from ._shared import GLOBAL_CONVERSATION_KEY, _utc_now_iso
from .legacy_migration import _normalize_catgirl_payload
from .staging import (
    _json_canonical_dumps,
    _load_json_if_exists,
    _sha256_bytes,
    _sha256_file,
)


def _load_user_preferences_entries(config_manager) -> list[dict[str, Any]]:
    preferences_path = Path(config_manager.get_config_path("user_preferences.json"))
    if not preferences_path.exists():
        return []
    try:
        with open(preferences_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except Exception:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


def _extract_conversation_settings(config_manager) -> dict[str, Any]:
    for entry in _load_user_preferences_entries(config_manager):
        if isinstance(entry, dict) and entry.get("model_path") == GLOBAL_CONVERSATION_KEY:
            return {
                key: value
                for key, value in entry.items()
                if key != "model_path"
            }
    return {}


def _build_runtime_preferences_payload(config_manager, conversation_settings: dict[str, Any]) -> list[dict[str, Any]]:
    preferences = [
        entry
        for entry in _load_user_preferences_entries(config_manager)
        if not isinstance(entry, dict) or entry.get("model_path") != GLOBAL_CONVERSATION_KEY
    ]
    filtered_settings = {
        key: value
        for key, value in (conversation_settings or {}).items()
        if key != "model_path"
    }
    if filtered_settings:
        preferences.append({
            "model_path": GLOBAL_CONVERSATION_KEY,
            **filtered_settings,
        })
    return preferences


def _derive_binding_model_reference(character_payload: dict[str, Any]) -> tuple[str, str]:
    from utils.config_manager import get_reserved

    runtime_model_type = str(
        get_reserved(character_payload, "avatar", "model_type", default="live2d", legacy_keys=("model_type",))
    ).strip().lower()
    live2d_model_path = str(
        get_reserved(character_payload, "avatar", "live2d", "model_path", default="", legacy_keys=("live2d",))
        or ""
    ).strip()
    vrm_model_path = str(
        get_reserved(character_payload, "avatar", "vrm", "model_path", default="", legacy_keys=("vrm",))
        or ""
    ).strip()
    mmd_model_path = str(
        get_reserved(character_payload, "avatar", "mmd", "model_path", default="")
        or ""
    ).strip()

    if runtime_model_type in {"live3d", "vrm"}:
        if mmd_model_path:
            return "mmd", mmd_model_path.replace("\\", "/")
        if vrm_model_path:
            return "vrm", vrm_model_path.replace("\\", "/")
        if live2d_model_path:
            return "live2d", live2d_model_path.replace("\\", "/")
        return "vrm", ""

    return "live2d", live2d_model_path.replace("\\", "/")


def _derive_binding_asset_source(*, model_ref: str, stored_asset_source: str, asset_source_id: str) -> str:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    normalized_source = str(stored_asset_source or "").strip().lower()

    if normalized_source == "steam_workshop" or asset_source_id or normalized_ref.startswith("/workshop/"):
        return "steam_workshop"
    if normalized_source == "builtin":
        return "builtin"
    if normalized_source in {"manual_external", "external"}:
        return "manual_external"
    if normalized_source in {"local_imported", "local"}:
        return "local_imported"
    if normalized_ref.startswith(("http://", "https://")):
        return "manual_external"
    if normalized_ref.startswith(("/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/")):
        return "local_imported"
    if normalized_ref.startswith("/static/") or (normalized_ref and not normalized_ref.startswith("/")):
        return "builtin"
    return "local_imported" if normalized_ref else ""


def _derive_binding_asset_source_id(*, model_ref: str, stored_source_id: str) -> str:
    source_id = str(stored_source_id or "").strip()
    if source_id:
        return source_id
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if normalized_ref.startswith("/workshop/"):
        parts = normalized_ref.split("/")
        if len(parts) >= 3:
            return parts[2]
    return ""


def _derive_binding_asset_display_name(model_ref: str) -> str:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if not normalized_ref:
        return ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            return parts[-2]
        return Path(parts[-1]).stem.replace(".model3", "")
    if normalized_ref.endswith((".vrm", ".pmx", ".pmd", ".vmd", ".vrma")):
        return Path(normalized_ref).stem
    parts = [part for part in normalized_ref.split("/") if part]
    return parts[-1] if parts else normalized_ref


def _collect_binding_live2d_roots(config_manager) -> list[Path]:
    get_live2d_lookup_roots = getattr(config_manager, "get_live2d_lookup_roots", None)
    if callable(get_live2d_lookup_roots):
        try:
            return [Path(candidate) for candidate in get_live2d_lookup_roots(prefer_writable=True)]
        except Exception:
            pass

    roots: list[Path] = []
    seen_roots: set[str] = set()
    for candidate in (
        getattr(config_manager, "live2d_dir", None),
        getattr(config_manager, "readable_live2d_dir", None),
    ):
        if not candidate:
            continue
        normalized_root = os.path.normcase(os.path.normpath(str(candidate)))
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)
        roots.append(Path(candidate))
    return roots


def _collect_binding_workshop_roots(config_manager) -> list[Path]:
    roots: list[Path] = []
    seen_roots: set[str] = set()

    get_workshop_path = getattr(config_manager, "get_workshop_path", None)
    if callable(get_workshop_path):
        try:
            configured_workshop_root = get_workshop_path()
        except Exception:
            configured_workshop_root = ""
        if configured_workshop_root:
            normalized_root = os.path.normcase(os.path.normpath(str(configured_workshop_root)))
            if normalized_root not in seen_roots:
                seen_roots.add(normalized_root)
                roots.append(Path(configured_workshop_root))

    fallback_workshop_root = getattr(config_manager, "workshop_dir", None)
    if fallback_workshop_root:
        normalized_root = os.path.normcase(os.path.normpath(str(fallback_workshop_root)))
        if normalized_root not in seen_roots:
            seen_roots.add(normalized_root)
            roots.append(Path(fallback_workshop_root))

    return roots


def _normalize_workshop_character_model_ref(model_type: str, payload: dict[str, Any]) -> str:
    normalized_type = str(model_type or "").strip().lower()
    if normalized_type != "live2d":
        return ""

    live2d_name = str(payload.get("live2d") or "").strip().replace("\\", "/")
    if not live2d_name:
        return ""
    if live2d_name.endswith(".model3.json") or "/" in live2d_name:
        return live2d_name
    return f"{live2d_name}/{live2d_name}.model3.json"


def _build_character_origin_match_payload(payload: Any) -> dict[str, Any]:
    normalized_payload = _normalize_catgirl_payload(payload)
    if normalized_payload is None:
        return {}

    skip_keys = {"档案名", *CHARACTER_RESERVED_FIELDS}
    comparable_payload: dict[str, Any] = {}
    for key, value in normalized_payload.items():
        if key in skip_keys or value is None:
            continue
        comparable_payload[key] = deepcopy(value)
    return comparable_payload


def _build_character_origin_profile_fingerprint(payload: Any) -> str:
    comparable_payload = _build_character_origin_match_payload(payload)
    if not comparable_payload:
        return ""

    fingerprint_payload = {
        "schema_version": 1,
        "character_payload": comparable_payload,
    }
    return "sha256:" + _sha256_bytes(_json_canonical_dumps(fingerprint_payload).encode("utf-8"))


def _collect_workshop_character_origin_candidates(config_manager) -> dict[str, list[dict[str, Any]]]:
    candidates_by_name: dict[str, list[dict[str, Any]]] = {}
    seen_entries: set[tuple[str, str, str, str, str]] = set()

    for workshop_root in _collect_binding_workshop_roots(config_manager):
        if not workshop_root.is_dir():
            continue
        try:
            item_roots = sorted(child for child in workshop_root.iterdir() if child.is_dir())
        except Exception:
            continue

        for item_root in item_roots:
            item_id = str(item_root.name or "").strip()
            if not item_id:
                continue
            try:
                chara_paths = sorted(path for path in item_root.rglob("*.chara.json") if path.is_file())
            except Exception:
                continue

            for chara_path in chara_paths:
                payload = _load_json_if_exists(chara_path)
                if not isinstance(payload, dict):
                    continue

                character_name = str(payload.get("档案名") or payload.get("name") or "").strip()
                if not character_name:
                    continue

                model_type = str(payload.get("model_type") or "live2d").strip().lower() or "live2d"
                model_ref = _normalize_workshop_character_model_ref(model_type, payload)
                origin_profile_fingerprint = _build_character_origin_profile_fingerprint(payload)
                dedupe_key = (character_name, item_id, model_type, model_ref, origin_profile_fingerprint)
                if dedupe_key in seen_entries:
                    continue
                seen_entries.add(dedupe_key)

                candidates_by_name.setdefault(character_name, []).append(
                    {
                        "character_name": character_name,
                        "origin_source": "steam_workshop",
                        "origin_source_id": item_id,
                        "model_type": model_type,
                        "origin_model_ref": model_ref,
                        "origin_display_name": _derive_binding_asset_display_name(model_ref),
                        "origin_profile_fingerprint": origin_profile_fingerprint,
                    }
                )

    return candidates_by_name


def _select_workshop_character_origin_candidate(
    candidates: list[dict[str, Any]],
    *,
    model_type: str,
    origin_source_id_hint: str = "",
    origin_model_ref_hint: str = "",
    origin_profile_fingerprint_hint: str = "",
) -> dict[str, Any] | None:
    if not candidates:
        return None

    selected_pool = [
        candidate
        for candidate in candidates
        if not candidate.get("model_type") or str(candidate.get("model_type") or "") == str(model_type or "")
    ] or list(candidates)

    origin_source_id_hint = str(origin_source_id_hint or "").strip()
    if origin_source_id_hint:
        id_matches = [
            candidate
            for candidate in selected_pool
            if str(candidate.get("origin_source_id") or "").strip() == origin_source_id_hint
        ]
        if len(id_matches) == 1:
            return deepcopy(id_matches[0])
        if id_matches:
            selected_pool = id_matches

    origin_model_ref_hint = str(origin_model_ref_hint or "").strip().replace("\\", "/")
    if origin_model_ref_hint:
        exact_ref_matches = [
            candidate
            for candidate in selected_pool
            if str(candidate.get("origin_model_ref") or "").strip().replace("\\", "/") == origin_model_ref_hint
        ]
        if len(exact_ref_matches) == 1:
            return deepcopy(exact_ref_matches[0])
        if exact_ref_matches:
            selected_pool = exact_ref_matches

    origin_profile_fingerprint_hint = str(origin_profile_fingerprint_hint or "").strip()
    if origin_profile_fingerprint_hint:
        fingerprint_matches = [
            candidate
            for candidate in selected_pool
            if str(candidate.get("origin_profile_fingerprint") or "").strip() == origin_profile_fingerprint_hint
        ]
        if len(fingerprint_matches) == 1:
            return deepcopy(fingerprint_matches[0])

    return None


def _derive_character_origin_metadata(
    config_manager,
    *,
    character_name: str,
    character_payload: dict[str, Any],
    model_type: str,
    workshop_origin_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, str]:
    from utils.config_manager import get_reserved

    origin_source = ""
    origin_source_id = ""
    origin_model_ref = ""
    origin_display_name = ""

    origin_source = str(get_reserved(character_payload, "character_origin", "source", default="") or "").strip()
    origin_source_id = str(get_reserved(character_payload, "character_origin", "source_id", default="") or "").strip()
    origin_model_ref = str(get_reserved(character_payload, "character_origin", "model_ref", default="") or "").strip().replace("\\", "/")
    origin_display_name = str(get_reserved(character_payload, "character_origin", "display_name", default="") or "").strip()
    origin_profile_fingerprint = _build_character_origin_profile_fingerprint(character_payload)

    candidates = (workshop_origin_index or {}).get(character_name) or []
    selected_candidate = _select_workshop_character_origin_candidate(
        candidates,
        model_type=model_type,
        origin_source_id_hint=origin_source_id,
        origin_model_ref_hint=origin_model_ref,
        origin_profile_fingerprint_hint=origin_profile_fingerprint,
    )
    if selected_candidate is not None:
        if not origin_source:
            origin_source = str(selected_candidate.get("origin_source") or "")
        if not origin_source_id:
            origin_source_id = str(selected_candidate.get("origin_source_id") or "")
        if not origin_model_ref:
            origin_model_ref = str(selected_candidate.get("origin_model_ref") or "")
        if not origin_display_name:
            origin_display_name = str(selected_candidate.get("origin_display_name") or "")

    return {
        "origin_source": origin_source,
        "origin_source_id": origin_source_id,
        "origin_model_ref": origin_model_ref,
        "origin_display_name": origin_display_name,
    }


def _build_live2d_model_ref_hints(model_ref: str) -> tuple[str, str, str, str]:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    normalized_suffix = normalized_ref.lstrip("/")
    if normalized_ref.startswith("/workshop/"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 3:
            normalized_suffix = "/".join(parts[2:])

    relative_parent = ""
    if normalized_suffix:
        relative_parent = Path(normalized_suffix).parent.as_posix()
        if relative_parent == ".":
            relative_parent = ""

    expected_filename = Path(normalized_ref).name if normalized_ref else ""
    expected_folder_name = ""
    expected_model_name = ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            expected_folder_name = parts[-2]
        expected_model_name = Path(expected_filename).stem.replace(".model3", "")
    elif normalized_ref:
        expected_model_name = Path(normalized_ref).stem

    return normalized_suffix, relative_parent, expected_filename, expected_folder_name or expected_model_name


def _rank_live2d_model3_path(
    candidate_path: Path,
    *,
    candidate_root: Path,
    normalized_suffix: str,
    relative_parent: str,
    expected_filename: str,
    expected_folder_name: str,
) -> tuple[int, int, str, Path]:
    try:
        relative_path = candidate_path.relative_to(candidate_root).as_posix()
    except Exception:
        relative_path = candidate_path.name

    expected_model_name = Path(expected_filename).stem.replace(".model3", "") if expected_filename else ""
    candidate_model_name = candidate_path.stem.replace(".model3", "")

    score = 0
    if normalized_suffix and relative_path == normalized_suffix:
        score += 100
    elif normalized_suffix and relative_path.endswith(normalized_suffix):
        score += 80
    if relative_parent and relative_path.startswith(f"{relative_parent}/"):
        score += 20
    if expected_filename and candidate_path.name == expected_filename:
        score += 40
    if expected_folder_name and candidate_path.parent.name == expected_folder_name:
        score += 20
    if expected_model_name and candidate_model_name == expected_model_name:
        score += 10

    return (score, -len(relative_path.split("/")), relative_path, candidate_path)


def _is_path_within(candidate_path: Path, base_path: Path) -> bool:
    try:
        candidate_real = os.path.normcase(os.path.realpath(str(candidate_path)))
        base_real = os.path.normcase(os.path.realpath(str(base_path)))
        return os.path.commonpath([candidate_real, base_real]) == base_real
    except Exception:
        return False


def _infer_binding_source_from_resolved_path(
    config_manager,
    *,
    resolved_path: Path | None,
    asset_source: str,
    asset_source_id: str,
) -> tuple[str, str]:
    if resolved_path is None or not resolved_path.is_file():
        return asset_source, asset_source_id

    for workshop_root in _collect_binding_workshop_roots(config_manager):
        if not _is_path_within(resolved_path, workshop_root):
            continue
        inferred_source_id = str(asset_source_id or "").strip()
        if not inferred_source_id:
            try:
                relative_parts = resolved_path.relative_to(workshop_root).parts
            except Exception:
                relative_parts = ()
            if relative_parts:
                inferred_source_id = str(relative_parts[0])
        return "steam_workshop", inferred_source_id

    for live2d_root in _collect_binding_live2d_roots(config_manager):
        if _is_path_within(resolved_path, live2d_root):
            return "local_imported", ""

    static_root = Path(config_manager.project_root) / "static"
    if _is_path_within(resolved_path, static_root):
        return "builtin", ""

    return asset_source, asset_source_id


def _resolve_binding_file_path(
    config_manager,
    *,
    model_type: str,
    model_ref: str,
    asset_source: str,
    asset_source_id: str,
) -> Path | None:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if not normalized_ref or normalized_ref.startswith(("http://", "https://")):
        return None

    candidates: list[Path] = []
    live2d_roots = _collect_binding_live2d_roots(config_manager)
    readable_live2d_dir = getattr(config_manager, "readable_live2d_dir", None)
    workshop_roots = _collect_binding_workshop_roots(config_manager)

    def _resolve_workshop_live2d_fallback() -> Path | None:
        if model_type != "live2d" or asset_source != "steam_workshop" or not asset_source_id:
            return None

        normalized_suffix, relative_parent, expected_filename, expected_folder_name = _build_live2d_model_ref_hints(normalized_ref)

        ranked_candidates: list[tuple[int, int, str, Path]] = []
        for workshop_root in workshop_roots:
            item_root = workshop_root / asset_source_id
            if not item_root.is_dir():
                continue
            try:
                discovered_files = sorted(path for path in item_root.rglob("*.model3.json") if path.is_file())
            except Exception:
                continue

            for discovered_path in discovered_files:
                try:
                    relative_path = discovered_path.relative_to(item_root).as_posix()
                except Exception:
                    relative_path = discovered_path.name

                ranked_candidates.append(
                    _rank_live2d_model3_path(
                        discovered_path,
                        candidate_root=item_root,
                        normalized_suffix=normalized_suffix,
                        relative_parent=relative_parent,
                        expected_filename=expected_filename,
                        expected_folder_name=expected_folder_name,
                    )
                )

        if not ranked_candidates:
            return None

        ranked_candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
        return ranked_candidates[0][3]

    def _resolve_local_live2d_fallback() -> Path | None:
        if model_type != "live2d" or normalized_ref.startswith("/"):
            return None

        normalized_suffix, relative_parent, expected_filename, expected_folder_name = _build_live2d_model_ref_hints(normalized_ref)
        ranked_candidates: list[tuple[int, int, str, Path]] = []

        def _append_candidates(search_root: Path, candidate_base: Path) -> None:
            if not candidate_base.is_dir():
                return
            try:
                discovered_files = sorted(path for path in candidate_base.rglob("*.model3.json") if path.is_file())
            except Exception:
                return
            for discovered_path in discovered_files:
                ranked_candidates.append(
                    _rank_live2d_model3_path(
                        discovered_path,
                        candidate_root=search_root,
                        normalized_suffix=normalized_suffix,
                        relative_parent=relative_parent,
                        expected_filename=expected_filename,
                        expected_folder_name=expected_folder_name,
                    )
                )

        for live2d_root in live2d_roots:
            candidate_dirs: list[Path] = []
            if relative_parent:
                candidate_dirs.append(live2d_root / relative_parent)
            if expected_folder_name:
                candidate_dirs.append(live2d_root / expected_folder_name)

            seen_candidate_dirs: set[str] = set()
            for candidate_dir in candidate_dirs:
                normalized_dir = os.path.normcase(os.path.normpath(str(candidate_dir)))
                if normalized_dir in seen_candidate_dirs:
                    continue
                seen_candidate_dirs.add(normalized_dir)
                _append_candidates(live2d_root, candidate_dir)

        for workshop_root in workshop_roots:
            try:
                item_roots = sorted(child for child in workshop_root.iterdir() if child.is_dir())
            except Exception:
                continue
            for item_root in item_roots:
                candidate_dirs: list[Path] = []
                if relative_parent:
                    candidate_dirs.append(item_root / relative_parent)
                if expected_folder_name:
                    candidate_dirs.append(item_root / expected_folder_name)
                if not candidate_dirs:
                    candidate_dirs.append(item_root)

                seen_candidate_dirs: set[str] = set()
                for candidate_dir in candidate_dirs:
                    normalized_dir = os.path.normcase(os.path.normpath(str(candidate_dir)))
                    if normalized_dir in seen_candidate_dirs:
                        continue
                    seen_candidate_dirs.add(normalized_dir)
                    _append_candidates(item_root, candidate_dir)

        if not ranked_candidates:
            return None

        ranked_candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
        return ranked_candidates[0][3]

    if model_type == "live2d":
        if normalized_ref.startswith("/user_live2d/"):
            relative_part = normalized_ref[len("/user_live2d/"):]
            if readable_live2d_dir is not None:
                candidates.append(Path(readable_live2d_dir) / relative_part)
            candidates.append(Path(config_manager.live2d_dir) / relative_part)
        elif normalized_ref.startswith("/user_live2d_local/"):
            candidates.append(Path(config_manager.live2d_dir) / normalized_ref[len("/user_live2d_local/"):])
        elif normalized_ref.startswith("/workshop/"):
            relative_workshop_path = "/".join(normalized_ref.split("/")[2:])
            for workshop_root in workshop_roots:
                candidates.append(workshop_root / relative_workshop_path)
        else:
            if asset_source == "steam_workshop" and asset_source_id:
                for workshop_root in workshop_roots:
                    candidates.append(workshop_root / asset_source_id / normalized_ref)
                    candidates.append(workshop_root / asset_source_id / Path(normalized_ref).name)
            if asset_source == "local_imported":
                if readable_live2d_dir is not None:
                    candidates.append(Path(readable_live2d_dir) / normalized_ref)
                candidates.append(Path(config_manager.live2d_dir) / normalized_ref)
            candidates.append(Path(config_manager.project_root) / "static" / normalized_ref)
    elif model_type == "vrm":
        if normalized_ref.startswith("/user_vrm/"):
            candidates.append(Path(config_manager.vrm_dir) / normalized_ref[len("/user_vrm/"):])
        elif normalized_ref.startswith("/static/vrm/"):
            candidates.append(Path(config_manager.project_root) / "static" / "vrm" / normalized_ref[len("/static/vrm/"):])
        elif normalized_ref.startswith("/workshop/"):
            relative_workshop_path = "/".join(normalized_ref.split("/")[2:])
            for workshop_root in workshop_roots:
                candidates.append(workshop_root / relative_workshop_path)
    elif model_type == "mmd":
        if normalized_ref.startswith("/user_mmd/"):
            candidates.append(Path(config_manager.mmd_dir) / normalized_ref[len("/user_mmd/"):])
        elif normalized_ref.startswith("/static/mmd/"):
            candidates.append(Path(config_manager.project_root) / "static" / "mmd" / normalized_ref[len("/static/mmd/"):])
        elif normalized_ref.startswith("/workshop/"):
            relative_workshop_path = "/".join(normalized_ref.split("/")[2:])
            for workshop_root in workshop_roots:
                candidates.append(workshop_root / relative_workshop_path)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    fallback_candidate = _resolve_workshop_live2d_fallback()
    if fallback_candidate is not None and fallback_candidate.is_file():
        return fallback_candidate
    fallback_candidate = _resolve_local_live2d_fallback()
    if fallback_candidate is not None and fallback_candidate.is_file():
        return fallback_candidate
    return None


def _derive_binding_asset_state(*, resolved_path: Path | None, asset_source: str, model_ref: str) -> str:
    if resolved_path is not None and resolved_path.is_file():
        return "ready"
    if not str(model_ref or "").strip():
        return "missing"
    if asset_source == "steam_workshop":
        return "downloadable"
    if asset_source in {"local_imported", "manual_external"}:
        return "import_required"
    return "missing"


def _derive_binding_experience_overrides(character_payload: dict[str, Any]) -> dict[str, Any]:
    from utils.config_manager import get_reserved

    overrides = {
        "touch_set": deepcopy(get_reserved(character_payload, "touch_set", default={}) or {}),
        "vrm_lighting": deepcopy(get_reserved(character_payload, "avatar", "vrm", "lighting", default={}) or {}),
        "mmd_lighting": deepcopy(get_reserved(character_payload, "avatar", "mmd", "lighting", default={}) or {}),
        "mmd_rendering": deepcopy(get_reserved(character_payload, "avatar", "mmd", "rendering", default={}) or {}),
        "mmd_physics": deepcopy(get_reserved(character_payload, "avatar", "mmd", "physics", default={}) or {}),
        "mmd_cursor_follow": deepcopy(get_reserved(character_payload, "avatar", "mmd", "cursor_follow", default={}) or {}),
    }
    return {
        key: value
        for key, value in overrides.items()
        if value not in ({}, None, [])
    }


def _derive_character_binding_summary(
    config_manager,
    character_name: str,
    character_payload: dict[str, Any],
    *,
    workshop_origin_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    from utils.config_manager import get_reserved

    binding_model_type, model_ref = _derive_binding_model_reference(character_payload)
    stored_source = get_reserved(character_payload, "avatar", "asset_source", default="")
    stored_source_id = get_reserved(
        character_payload,
        "avatar",
        "asset_source_id",
        default="",
        legacy_keys=("live2d_item_id", "item_id"),
    )
    asset_source_id = _derive_binding_asset_source_id(model_ref=model_ref, stored_source_id=str(stored_source_id or ""))
    asset_source = _derive_binding_asset_source(
        model_ref=model_ref,
        stored_asset_source=str(stored_source or ""),
        asset_source_id=asset_source_id,
    )
    resolved_path = _resolve_binding_file_path(
        config_manager,
        model_type=binding_model_type,
        model_ref=model_ref,
        asset_source=asset_source,
        asset_source_id=asset_source_id,
    )
    asset_source, asset_source_id = _infer_binding_source_from_resolved_path(
        config_manager,
        resolved_path=resolved_path,
        asset_source=asset_source,
        asset_source_id=asset_source_id,
    )
    asset_state = _derive_binding_asset_state(
        resolved_path=resolved_path,
        asset_source=asset_source,
        model_ref=model_ref,
    )
    origin_payload = _derive_character_origin_metadata(
        config_manager,
        character_name=character_name,
        character_payload=character_payload,
        model_type=binding_model_type,
        workshop_origin_index=workshop_origin_index,
    )
    asset_fingerprint = _sha256_file(resolved_path) if resolved_path is not None else ""

    fallback_model_ref = ""
    if asset_state != "ready" and binding_model_type != "live2d":
        fallback_model_ref = "yui-origin/yui-origin.model3.json"

    return {
        "character_name": character_name,
        "model_type": binding_model_type,
        "asset_source": asset_source,
        "asset_source_id": asset_source_id,
        "model_ref": model_ref,
        "asset_display_name": _derive_binding_asset_display_name(model_ref),
        "asset_fingerprint": asset_fingerprint,
        "asset_state": asset_state,
        "origin_source": str(origin_payload.get("origin_source") or ""),
        "origin_source_id": str(origin_payload.get("origin_source_id") or ""),
        "origin_model_ref": str(origin_payload.get("origin_model_ref") or ""),
        "origin_display_name": str(origin_payload.get("origin_display_name") or ""),
        "fallback_model_ref": fallback_model_ref,
        "last_verified_at": _utc_now_iso() if resolved_path is not None else "",
        "experience_overrides": _derive_binding_experience_overrides(character_payload),
    }


def _build_catalog_index_payload(
    *,
    character_names: list[str],
    characters_payload: dict[str, Any],
    binding_payloads: dict[str, dict[str, Any]],
    sequence_number: int,
    exported_at: str,
) -> dict[str, Any]:
    catgirls_payload = characters_payload.get("猫娘") or {}
    return {
        "schema_version": 1,
        "sequence_number": sequence_number,
        "exported_at_utc": exported_at,
        "characters": [
            {
                "character_name": name,
                "entry_sequence_number": sequence_number,
                "has_memory": True,
                "model_type": binding_payloads.get(name, {}).get("model_type", ""),
                "asset_source": binding_payloads.get(name, {}).get("asset_source", ""),
                "asset_source_id": binding_payloads.get(name, {}).get("asset_source_id", ""),
                "asset_state": binding_payloads.get(name, {}).get("asset_state", ""),
                "origin_source": binding_payloads.get(name, {}).get("origin_source", ""),
                "origin_source_id": binding_payloads.get(name, {}).get("origin_source_id", ""),
                "origin_model_ref": binding_payloads.get(name, {}).get("origin_model_ref", ""),
                "origin_display_name": binding_payloads.get(name, {}).get("origin_display_name", ""),
                "asset_display_name": binding_payloads.get(name, {}).get("asset_display_name", ""),
                "asset_fingerprint": binding_payloads.get(name, {}).get("asset_fingerprint", ""),
                "display_name": str((catgirls_payload.get(name) or {}).get("档案名") or name),
            }
            for name in character_names
        ],
    }


def _load_staged_json_file(staged_entries: dict[str, Path], relative_path: str, *, required: bool = False) -> Any:
    staged_path = staged_entries.get(relative_path)
    if staged_path is None:
        if required:
            raise ValueError(f"cloudsave import requires {relative_path}")
        return None
    with open(staged_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _parse_binding_payloads(staged_entries: dict[str, Path]) -> dict[str, dict[str, Any]]:
    binding_payloads: dict[str, dict[str, Any]] = {}
    for relative_path, staged_path in staged_entries.items():
        if not relative_path.startswith("bindings/") or not relative_path.endswith(".json"):
            continue
        binding_name = Path(relative_path).stem
        with open(staged_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if not isinstance(payload, dict):
            raise ValueError(f"{relative_path} must contain a JSON object")
        payload_name = str(payload.get("character_name") or "").strip()
        if payload_name and payload_name != binding_name:
            raise ValueError(f"{relative_path} character_name does not match filename")
        binding_payloads[binding_name] = payload
    return binding_payloads


def _parse_catalog_character_names(payload: Any) -> set[str]:
    if payload is None:
        return set()
    if not isinstance(payload, dict):
        raise ValueError("catalog/catgirls_index.json must contain a JSON object")
    names: set[str] = set()
    for entry in payload.get("characters") or []:
        if not isinstance(entry, dict):
            raise ValueError("catalog/catgirls_index.json contains a non-object entry")
        name = str(entry.get("character_name") or "").strip()
        if not name:
            raise ValueError("catalog/catgirls_index.json contains an empty character_name")
        names.add(name)
    return names


def _build_catalog_current_character_payload(*, current_character_name: str, exported_at: str, sequence_number: int) -> dict[str, Any]:
    return {
        "current_character_name": current_character_name,
        "last_known_name": current_character_name,
        "applied_at_utc": exported_at,
        "entry_sequence_number": sequence_number,
    }
