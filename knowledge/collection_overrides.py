"""Small local overrides for collection-level conversational participation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from utils.file_utils import atomic_write_json

from .engine.mutation_lock import mutation_lock


logger = logging.getLogger(__name__)


def get_collection_override_path(knowledge_root: str | Path) -> Path:
    return Path(knowledge_root) / "collection.overrides.json"


def load_auto_context_overrides(path: str | Path) -> dict[str, bool]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[public-knowledge] ignored invalid collection overrides type=%s",
            type(exc).__name__,
        )
        return {}
    values = payload.get("auto_context", {}) if isinstance(payload, dict) else {}
    if not isinstance(values, dict):
        return {}
    return {
        str(collection_id): enabled
        for collection_id, enabled in values.items()
        if isinstance(collection_id, str) and isinstance(enabled, bool)
    }


def set_collection_auto_context(
    path: str | Path,
    *,
    collection_id: str,
    enabled: bool,
) -> None:
    collection_id = str(collection_id or "").strip()
    if not collection_id:
        raise ValueError("collection_id is required")
    output_path = Path(path)
    with mutation_lock(output_path):
        values = load_auto_context_overrides(output_path)
        values[collection_id] = bool(enabled)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            output_path,
            {"auto_context": dict(sorted(values.items()))},
            ensure_ascii=False,
            indent=2,
        )


def clear_collection_auto_context(path: str | Path, *, collection_id: str) -> None:
    """Remove stale authorization when a community collection is unregistered."""
    collection_id = str(collection_id or "").strip()
    if not collection_id:
        raise ValueError("collection_id is required")
    output_path = Path(path)
    with mutation_lock(output_path):
        values = load_auto_context_overrides(output_path)
        if collection_id not in values:
            return
        values.pop(collection_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            output_path,
            {"auto_context": dict(sorted(values.items()))},
            ensure_ascii=False,
            indent=2,
        )
