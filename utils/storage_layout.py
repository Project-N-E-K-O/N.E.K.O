from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from utils.storage_policy import compute_anchor_root, load_storage_policy, normalize_runtime_root

NEKO_STORAGE_SELECTED_ROOT_ENV = "NEKO_STORAGE_SELECTED_ROOT"
NEKO_STORAGE_ANCHOR_ROOT_ENV = "NEKO_STORAGE_ANCHOR_ROOT"
NEKO_STORAGE_CLOUDSAVE_ROOT_ENV = "NEKO_STORAGE_CLOUDSAVE_ROOT"


def build_storage_layout(
    *,
    selected_root: Path | str,
    anchor_root: Path | str,
    source: str,
) -> dict[str, Any]:
    normalized_selected_root = normalize_runtime_root(selected_root)
    normalized_anchor_root = normalize_runtime_root(anchor_root)
    return {
        "selected_root": str(normalized_selected_root),
        "anchor_root": str(normalized_anchor_root),
        "cloudsave_root": str(normalized_anchor_root / "cloudsave"),
        "source": str(source or "runtime_default").strip() or "runtime_default",
    }


def export_storage_layout_to_env(
    layout: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    target_env = environ if environ is not None else os.environ
    target_env[NEKO_STORAGE_SELECTED_ROOT_ENV] = str(layout.get("selected_root") or "").strip()
    target_env[NEKO_STORAGE_ANCHOR_ROOT_ENV] = str(layout.get("anchor_root") or "").strip()
    target_env[NEKO_STORAGE_CLOUDSAVE_ROOT_ENV] = str(layout.get("cloudsave_root") or "").strip()
    return target_env


def clear_storage_layout_env(*, environ: dict[str, str] | None = None) -> dict[str, str]:
    target_env = environ if environ is not None else os.environ
    for key in (
        NEKO_STORAGE_SELECTED_ROOT_ENV,
        NEKO_STORAGE_ANCHOR_ROOT_ENV,
        NEKO_STORAGE_CLOUDSAVE_ROOT_ENV,
    ):
        target_env.pop(key, None)
    return target_env


def resolve_storage_layout(config_manager) -> dict[str, Any]:
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    default_anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    if bool(getattr(config_manager, "recovery_committed_root_unavailable", False)):
        return build_storage_layout(
            selected_root=current_root,
            anchor_root=getattr(config_manager, "anchor_root", default_anchor_root),
            source="recovery_runtime",
        )

    policy = load_storage_policy(config_manager, anchor_root=default_anchor_root)

    if not isinstance(policy, dict):
        return build_storage_layout(
            selected_root=current_root,
            anchor_root=default_anchor_root,
            source="runtime_default",
        )

    selected_root_value = str(policy.get("selected_root") or "").strip()
    if not selected_root_value:
        return build_storage_layout(
            selected_root=current_root,
            anchor_root=default_anchor_root,
            source="runtime_default",
        )

    try:
        selected_root = normalize_runtime_root(selected_root_value)
    except Exception:
        return build_storage_layout(
            selected_root=current_root,
            anchor_root=default_anchor_root,
            source="runtime_default",
        )

    anchor_root_value = str(policy.get("anchor_root") or "").strip()
    try:
        anchor_root = normalize_runtime_root(anchor_root_value or default_anchor_root)
    except Exception:
        anchor_root = default_anchor_root
    return build_storage_layout(
        selected_root=selected_root,
        anchor_root=anchor_root,
        source="policy",
    )
