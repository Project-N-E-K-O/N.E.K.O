from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root

from .store import GalgameStore

logger = get_logger("galgame.tutorial_migration")


def _read_flat_progress(store_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_flat_progress(store_path: Path, progress: dict[str, Any]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = store_path.with_suffix(store_path.suffix + ".tmp")
    # flush + fsync 后再 replace：断电只丢 .tmp 不破坏存档，对齐写同一个
    # galgame_store.json 的 owner（store/core.py）的原子写法。
    with tmp_path.open("w", encoding="utf-8") as tmp_file:
        json.dump(progress, tmp_file, ensure_ascii=False, sort_keys=True, indent=2)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
    tmp_path.replace(store_path)


def _legacy_store_paths() -> tuple[Path, Path]:
    plugin_dir = Path(__file__).resolve().parent
    source_store_path = plugin_dir / "data" / "galgame_store.json"
    runtime_store_path = (
        resolve_runtime_data_root()
        / "plugins"
        / "galgame_plugin"
        / "data"
        / "galgame_store.json"
    )
    return runtime_store_path, source_store_path


def copy_legacy_tutorial_progress_if_missing(store_path: Path) -> None:
    if _read_flat_progress(store_path) is not None:
        logger.info(
            "galgame tutorial progress migration skipped; target store already exists: {}",
            store_path,
        )
        return

    for legacy_store_path in _legacy_store_paths():
        if not legacy_store_path.is_file():
            continue
        try:
            legacy_progress = GalgameStore(legacy_store_path, logger).load_tutorial_progress()
        except Exception:  # noqa: BLE001 - corrupted legacy stores should not abort migration.
            logger.warning(
                "failed to load legacy tutorial progress from {}, skipping",
                legacy_store_path,
                exc_info=True,
            )
            continue
        if isinstance(legacy_progress, dict):
            _write_flat_progress(store_path, legacy_progress)
            logger.info(
                "galgame tutorial progress migrated from legacy store: {} -> {}",
                legacy_store_path,
                store_path,
            )
            return
    logger.info(
        "galgame tutorial progress migration skipped; no legacy progress found for {}",
        store_path,
    )
