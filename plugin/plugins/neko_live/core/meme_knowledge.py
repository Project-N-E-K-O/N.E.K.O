"""Plugin-owned meme knowledge retrieval for NEKO Live prompts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from json import JSONDecodeError, loads
from pathlib import Path
from re import sub
from typing import Any, Iterable


DEFAULT_MEME_KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "meme_knowledge.json"


@dataclass(frozen=True, slots=True)
class MemeKnowledgeEntry:
    id: str
    label: str
    triggers: tuple[str, ...]
    tags: tuple[str, ...]
    hint: str
    avoid: str = ""


def retrieve_meme_knowledge(*parts: str, limit: int = 2) -> list[MemeKnowledgeEntry]:
    query = _normalize_query(" ".join(str(part or "") for part in parts))
    if not query:
        return []
    scored: list[tuple[int, int, MemeKnowledgeEntry]] = []
    for index, entry in enumerate(default_meme_knowledge()):
        score = _entry_score(entry, query)
        if score > 0:
            scored.append((score, -index, entry))
    scored.sort(reverse=True)
    return [entry for _, _, entry in scored[: max(0, limit)]]


@lru_cache(maxsize=1)
def default_meme_knowledge() -> tuple[MemeKnowledgeEntry, ...]:
    return load_meme_knowledge(DEFAULT_MEME_KNOWLEDGE_PATH)


def clear_meme_knowledge_cache() -> None:
    default_meme_knowledge.cache_clear()


def load_meme_knowledge(path: str | Path = DEFAULT_MEME_KNOWLEDGE_PATH) -> tuple[MemeKnowledgeEntry, ...]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = loads(raw)
    except (OSError, JSONDecodeError, TypeError, ValueError):
        return ()

    raw_entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(raw_entries, list):
        return ()

    entries: list[MemeKnowledgeEntry] = []
    seen_ids: set[str] = set()
    for raw_entry in raw_entries:
        entry = _coerce_entry(raw_entry)
        if entry is None or entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        entries.append(entry)
    return tuple(entries)


def meme_knowledge_metadata(entries: Iterable[MemeKnowledgeEntry]) -> dict[str, str]:
    values = list(entries)
    if not values:
        return {}
    return {
        "meme_hint_ids": ",".join(entry.id for entry in values[:3]),
        "meme_hint_tags": ",".join(_unique_tag(tag for entry in values for tag in entry.tags)[:6]),
    }


def render_meme_knowledge_block(entries: Iterable[MemeKnowledgeEntry]) -> str:
    values = list(entries)
    if not values:
        return ""
    lines = [
        "Meme knowledge hints, optional seasoning only:",
    ]
    for entry in values[:2]:
        line = f"- {entry.label}: {entry.hint}"
        if entry.avoid:
            line += f" Avoid: {entry.avoid}"
        lines.append(line)
    lines.extend(
        [
            "",
            "Meme usage rule: Use at most one hint only if it naturally fits the current danmaku.",
            "Do not explain meme origins, force a meme, or stack multiple memes in one reply.",
            "Current live-room meaning still wins over meme flavor.",
            "",
        ]
    )
    return "\n".join(lines)


def _coerce_entry(value: Any) -> MemeKnowledgeEntry | None:
    if not isinstance(value, dict):
        return None
    entry_id = _safe_str(value.get("id"))
    label = _safe_str(value.get("label"))
    hint = _safe_str(value.get("hint"))
    triggers = _safe_str_tuple(value.get("triggers"))
    tags = _safe_str_tuple(value.get("tags"))
    if not entry_id or not label or not hint or not triggers:
        return None
    return MemeKnowledgeEntry(
        id=entry_id,
        label=label,
        triggers=triggers,
        tags=tags,
        hint=hint,
        avoid=_safe_str(value.get("avoid")),
    )


def _entry_score(entry: MemeKnowledgeEntry, query: str) -> int:
    score = 0
    for trigger in entry.triggers:
        normalized = _normalize_query(trigger)
        if normalized and normalized in query:
            score += 10 + min(len(normalized), 8)
    for tag in entry.tags:
        normalized_tag = _normalize_query(tag)
        if normalized_tag and normalized_tag in query:
            score += 2
    return score


def _normalize_query(value: str) -> str:
    text = str(value or "").casefold()
    return sub(r"[\s\W_]+", "", text)


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _unique_tag(tags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
