from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.cloudsave_runtime import ROOT_MODE_DEFERRED_INIT, _runtime_root_has_user_content

# TEMP(Stage 1 development):
# 当前开发阶段要求网页主页每次打开都弹出存储位置选择层，方便反复验证首屏显示。
# 等 Stage 2/正常模式接入 storage_policy 或首轮完成态后，这里应改回“仅首次需要选择时才返回 True”。
DEVELOPMENT_ALWAYS_REQUIRE_SELECTION = True


def _normalize_path(value: Path | str) -> str:
    return str(Path(value))


def _compute_anchor_root(config_manager, current_root: Path) -> Path:
    getter = getattr(config_manager, "_get_standard_data_directory_candidates", None)
    if callable(getter):
        try:
            candidates = getter()
        except Exception:
            candidates = []
        for candidate in candidates:
            try:
                return Path(candidate) / config_manager.app_name
            except Exception:
                continue
    return current_root


def _collect_legacy_sources(config_manager, *, current_root: Path, anchor_root: Path) -> list[str]:
    legacy_sources: list[str] = []
    seen: set[str] = {_normalize_path(current_root), _normalize_path(anchor_root)}

    for candidate in config_manager.get_legacy_app_root_candidates():
        path = Path(candidate)
        normalized = _normalize_path(path)
        if normalized in seen:
            continue
        if not _runtime_root_has_user_content(path, config_manager=config_manager):
            continue
        seen.add(normalized)
        legacy_sources.append(normalized)

    return legacy_sources


def _extract_last_error(last_migration_result: str) -> str:
    result = (last_migration_result or "").strip()
    if "failed" in result.lower():
        return result
    return ""


def _should_require_selection() -> bool:
    # TEMP(Stage 1 development):
    # 现在始终强制弹窗；开发结束后改为读取正式持久化状态，恢复正常“只在需要时弹出”的模式。
    return DEVELOPMENT_ALWAYS_REQUIRE_SELECTION


def build_storage_location_bootstrap_payload(config_manager) -> dict[str, Any]:
    current_root = Path(config_manager.app_docs_dir)
    anchor_root = _compute_anchor_root(config_manager, current_root)
    root_state = config_manager.load_root_state()
    root_mode = str(root_state.get("mode") or "")
    last_migration_result = str(root_state.get("last_migration_result") or "")

    return {
        "current_root": _normalize_path(current_root),
        "recommended_root": _normalize_path(anchor_root),
        "legacy_sources": _collect_legacy_sources(
            config_manager,
            current_root=current_root,
            anchor_root=anchor_root,
        ),
        "anchor_root": _normalize_path(anchor_root),
        "cloudsave_root": _normalize_path(anchor_root / "cloudsave"),
        "selection_required": _should_require_selection(),
        "migration_pending": False,
        "recovery_required": root_mode == ROOT_MODE_DEFERRED_INIT,
        "legacy_cleanup_pending": False,
        "last_known_good_root": _normalize_path(root_state.get("last_known_good_root") or current_root),
        "migration": {
            "last_error": _extract_last_error(last_migration_result),
        },
        "stage": "stage1_web_bootstrap",
    }
