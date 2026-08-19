"""Source display metadata without built-in domain registrations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .filters import sanitize_external_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    tag: str
    name: str
    homepage: str = ""
    license: str = "Unknown"
    supports_sync: bool = False


def resolve_source(
    tag: str,
    *,
    registered_sources: Iterable[KnowledgeSource] = (),
    database_path: str | Path | None = None,
) -> KnowledgeSource:
    """Resolve trusted collection metadata, then installed-pack metadata."""
    source = next((value for value in registered_sources if value.tag == tag), None)
    if source is not None:
        return source
    if database_path is not None:
        source = _get_pack_source(tag, Path(database_path).with_name("packs.json"))
        if source is not None:
            return source
    return KnowledgeSource(tag, tag.removeprefix("source:"))


def _get_pack_source(tag: str, registry_path: Path) -> KnowledgeSource | None:
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[public-knowledge] ignored invalid source registry type=%s",
            type(exc).__name__,
        )
        return None
    packs = payload.get("packs") if isinstance(payload, dict) else None
    if not isinstance(packs, dict):
        return None
    for pack in packs.values():
        if not isinstance(pack, dict) or pack.get("source_tag") != tag:
            continue
        source = pack.get("source")
        if not isinstance(source, dict):
            return None
        return KnowledgeSource(
            tag=tag,
            name=sanitize_external_text(
                str(source.get("name") or tag.removeprefix("source:")),
                max_chars=200,
            ),
            homepage=sanitize_external_text(
                str(source.get("homepage") or ""),
                max_chars=2_000,
            ),
            license=sanitize_external_text(
                str(source.get("license") or "Unknown"),
                max_chars=500,
            ),
        )
    return None
