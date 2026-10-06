"""Source-level public-knowledge policy and display metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .filters import sanitize_external_text


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    tag: str
    name: str
    homepage: str
    license: str
    material_type: str = "knowledge"


SOURCES: dict[str, KnowledgeSource] = {
    "source:chime": KnowledgeSource("source:chime", "CHIME", "https://github.com/yuboxie/chime", "MIT"),
    "source:geng-guide": KnowledgeSource("source:geng-guide", "梗指南", "local-import://geng-guide-output.md", "User-provided export; license not stated"),
    "source:moegirl": KnowledgeSource("source:moegirl", "萌娘百科", "https://zh.moegirl.org.cn/", "CC BY-NC-SA 3.0 CN"),
    "source:geng8": KnowledgeSource("source:geng8", "梗8", "https://www.geng8.com/", "Verify site terms before redistribution"),
    "source:corpora": KnowledgeSource(
        "source:corpora",
        "Darius Kazemi's Corpora",
        "https://github.com/dariusk/corpora",
        "CC0 1.0",
        "corpus",
    ),
}


def get_source(
    tag: str,
    *,
    database_path: str | Path | None = None,
) -> KnowledgeSource:
    return get_sources((tag,), database_path=database_path)[tag]


def get_sources(
    tags: tuple[str, ...] | list[str],
    *,
    database_path: str | Path | None = None,
) -> dict[str, KnowledgeSource]:
    """Resolve source display metadata with at most one registry read."""
    unique_tags = tuple(dict.fromkeys(str(tag) for tag in tags))
    pack_sources = (
        _get_pack_sources(Path(database_path).with_name("packs.json"))
        if database_path is not None
        else {}
    )
    return {
        tag: SOURCES.get(tag)
        or pack_sources.get(tag)
        or KnowledgeSource(tag, tag.removeprefix("source:"), "", "Unknown")
        for tag in unique_tags
    }


def _get_pack_source(tag: str, registry_path: Path) -> KnowledgeSource | None:
    return _get_pack_sources(registry_path).get(tag)


def _get_pack_sources(registry_path: Path) -> dict[str, KnowledgeSource]:
    # Keep display metadata on the same bounded, regular-file-only trust path as
    # routing and mutation code. Import lazily to avoid coupling module startup.
    from .packs import KnowledgePackRegistryError, _load_registry

    try:
        payload = _load_registry(registry_path)
    except KnowledgePackRegistryError:
        return {}
    packs = payload.get("packs") if isinstance(payload, dict) else None
    if not isinstance(packs, dict):
        return {}
    sources: dict[str, KnowledgeSource] = {}
    for pack in packs.values():
        if not isinstance(pack, dict):
            continue
        tag = str(pack.get("source_tag") or "")
        if not tag:
            continue
        source = pack.get("source")
        if not isinstance(source, dict):
            continue
        sources[tag] = KnowledgeSource(
            tag=tag,
            name=sanitize_external_text(
                str(source.get("name") or tag.removeprefix("source:")),
                max_chars=200,
            ),
            homepage=sanitize_external_text(str(source.get("homepage") or ""), max_chars=2_000),
            license=sanitize_external_text(
                str(source.get("license") or "Unknown"),
                max_chars=500,
            ),
            material_type=(
                str(pack.get("effective_material_type"))
                if pack.get("effective_material_type") in {"knowledge", "corpus"}
                else "knowledge"
            ),
        )
    return sources
