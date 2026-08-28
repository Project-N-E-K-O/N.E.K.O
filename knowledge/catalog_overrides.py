"""Local enable/disable overrides kept outside the five-field entry table."""

from __future__ import annotations

import json
from pathlib import Path

from utils.file_utils import atomic_write_json

from knowledge._mutation_lock import mutation_lock
from knowledge.models import normalize_knowledge_title


EntryKey = tuple[str, str]


class CatalogOverrideError(ValueError):
    """The override file exists but cannot be trusted or safely mutated."""


def get_catalog_override_path(database_path: str | Path) -> Path:
    return Path(database_path).with_name("catalog.override.json")


def load_disabled_entries(path: str | Path) -> frozenset[EntryKey]:
    override_path = Path(path)
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogOverrideError("catalog override is unreadable or invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("disabled"), list):
        raise CatalogOverrideError("catalog override must contain a disabled list")
    rows = payload["disabled"]
    result: set[EntryKey] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("source"), str)
            or not isinstance(row.get("title"), str)
        ):
            raise CatalogOverrideError("catalog override contains an invalid entry")
        source = row["source"].strip()
        title = normalize_knowledge_title(row["title"])
        if not source.startswith("source:") or not title:
            raise CatalogOverrideError("catalog override contains an invalid entry")
        result.add((source, title))
    return frozenset(result)


def set_entry_disabled(
    path: str | Path,
    *,
    source_tag: str,
    title: str,
    disabled: bool,
) -> int:
    """Atomically update one source/title override and return the disabled count."""
    if not isinstance(source_tag, str) or not isinstance(title, str):
        raise ValueError("source and title are required")
    source_tag = source_tag.strip()
    title = normalize_knowledge_title(title)
    if not source_tag.startswith("source:") or not title:
        raise ValueError("source and title are required")
    output_path = Path(path)
    with mutation_lock(output_path):
        entries = set(load_disabled_entries(output_path))
        key = (source_tag, title)
        if disabled:
            entries.add(key)
        else:
            entries.discard(key)
        payload = {
            "disabled": [
                {"source": source, "title": entry_title}
                for source, entry_title in sorted(entries)
            ]
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_path, payload, ensure_ascii=False, indent=2)
        count = len(entries)
    return count


def entry_key(entry) -> EntryKey:
    return entry.source_tag, normalize_knowledge_title(entry.title)
