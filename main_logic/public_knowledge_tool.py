"""Local-only public-knowledge tool and turn-context adapter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from config.prompts.prompts_knowledge import (
    PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION,
    PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION,
    localized_knowledge_prompt,
)
from config.public_knowledge_settings import (
    PUBLIC_KNOWLEDGE_AUTO_CONTEXT_ENABLED,
    PUBLIC_KNOWLEDGE_AUTO_CONTEXT_TIMEOUT_SECONDS,
)
from knowledge.builtin import BUILTIN_COLLECTIONS, open_builtin_knowledge
from knowledge.diagnostics import record_knowledge_route
from main_logic.tool_calling import ToolDefinition
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Main")
_COLLECTION_IDS = tuple(spec.collection_id for spec in BUILTIN_COLLECTIONS)
_COLLECTION_SPECS = {spec.collection_id: spec for spec in BUILTIN_COLLECTIONS}
_COLLECTION_PRIORITY = {
    spec.collection_id: spec.priority for spec in BUILTIN_COLLECTIONS
}


def _record_route(**values: object) -> None:
    try:
        record_knowledge_route(**values)
    except Exception:
        logger.debug("[public-knowledge] diagnostic recording failed", exc_info=True)


def _bounded_limit(value: object) -> int:
    try:
        return min(max(int(value), 1), 3)
    except (TypeError, ValueError):
        return 3


def _query_local_knowledge(arguments: dict) -> tuple[str, str, list[tuple[str, object]]]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("missing_query")
    collection = str(arguments.get("collection") or "all").strip().lower()
    if collection not in {"all", *_COLLECTION_IDS}:
        raise ValueError("invalid_collection")
    mode = str(arguments.get("mode") or "lookup").strip().lower()
    if mode not in {"lookup", "sample"}:
        raise ValueError("invalid_mode")
    limit = _bounded_limit(arguments.get("limit", 3))
    service = open_builtin_knowledge(get_config_manager().knowledge_dir)
    collection_ids: Iterable[str] = (
        _COLLECTION_IDS if collection == "all" else (collection,)
    )

    if mode == "sample":
        entries: list[tuple[str, object]] = []
        for collection_id in collection_ids:
            try:
                sampled = service.sample_entries(collection_id, query, limit=limit)
            except ValueError:
                continue
            entries.extend((collection_id, entry) for entry in sampled)
            if entries:
                break
        return mode, collection, entries[:limit]

    ranked: list[tuple[float, int, str, object]] = []
    for collection_id in collection_ids:
        for hit in service.search(collection_id, query, limit=limit):
            ranked.append(
                (
                    hit.score,
                    _COLLECTION_PRIORITY[collection_id],
                    collection_id,
                    hit.entry,
                )
            )
    ranked.sort(key=lambda item: (-item[0], -item[1], item[3].title, item[2]))
    return mode, collection, [
        (collection_id, entry)
        for _, _, collection_id, entry in ranked[:limit]
    ]


def _render_entries(entries: list[tuple[str, object]]) -> str:
    if not entries:
        return "No relevant public knowledge is available locally."
    lines = ["Public knowledge (local reference only; not a memory):"]
    for collection_id, entry in entries:
        spec = _COLLECTION_SPECS[collection_id]
        policy = spec.response_policy
        summary = (entry.summary or entry.content[:420]).replace("\n", " ").strip()
        details = [f"- [{collection_id}] {entry.title}: {summary[:500]}"]
        if policy is not None:
            classification = next(
                (
                    tag.removeprefix(policy.classification_tag_prefix).strip()
                    for tag in entry.tags
                    if tag.startswith(policy.classification_tag_prefix)
                    and tag.removeprefix(policy.classification_tag_prefix).strip()
                ),
                "",
            )
            if classification:
                details.append(f"  {policy.classification_label}: {classification}")
            reference_lines = [
                line.strip().removeprefix("- ").strip()
                for line in entry.content.splitlines()
                if line.strip()
                and any(
                    line.strip().startswith(prefix)
                    for prefix in policy.detail_line_prefixes
                )
            ]
            if reference_lines:
                details.append(
                    f"  {policy.detail_label}: {' | '.join(reference_lines)[:600]}"
                )
        source = next(
            (source for source in spec.sources if source.tag == entry.source_tag),
            None,
        )
        source_name = source.name if source is not None else entry.source_tag
        license_name = source.license if source is not None else "Unknown"
        cautions = []
        if any(tag in {"risk:profanity", "risk:offense"} for tag in entry.tags):
            cautions.append("may include profane or offensive usage")
        if "quality:stale-usage" in entry.tags:
            cautions.append("usage may be outdated")
        caution_text = " | caution: " + "; ".join(cautions) if cautions else ""
        details.append(
            f"  Source: {source_name} | license: {license_name}{caution_text}"
        )
        lines.append("\n".join(details))
    return "\n".join(lines)


async def handle_public_knowledge_call(
    arguments: dict,
    *,
    language: str,
    deadline_monotonic: float | None = None,
) -> str:
    """Query or sample built-in knowledge without network or memory access."""
    del language, deadline_monotonic
    started_at = time.perf_counter()
    try:
        mode, collection, entries = await asyncio.to_thread(
            _query_local_knowledge,
            arguments if isinstance(arguments, dict) else {},
        )
    except ValueError as exc:
        messages = {
            "missing_query": "No public knowledge query was provided.",
            "invalid_collection": "The requested public knowledge collection is not available.",
            "invalid_mode": "The requested public knowledge mode is not available.",
        }
        return messages.get(str(exc), "The public knowledge request is invalid.")
    logger.info(
        "[public-knowledge] tool mode=%s collection=%s hits=%d elapsed_ms=%d",
        mode,
        collection,
        len(entries),
        int((time.perf_counter() - started_at) * 1000),
    )
    return _render_entries(entries)


def register_public_knowledge_tool(tool_registry, *, language: str) -> None:
    """Register the public-knowledge tool with localized schema text."""
    tool_registry.register(
        ToolDefinition(
            name="query_public_knowledge",
            description=localized_knowledge_prompt(
                PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION,
                language,
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": localized_knowledge_prompt(
                            PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION,
                            language,
                        ),
                    },
                    "collection": {
                        "type": "string",
                        "enum": ["all", *_COLLECTION_IDS],
                        "default": "all",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["lookup", "sample"],
                        "default": "lookup",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["query"],
            },
            handler=lambda arguments: handle_public_knowledge_call(
                arguments,
                language=language,
            ),
            metadata={"source": "builtin", "domain": "public_knowledge"},
        ),
        replace=True,
    )


def _build_turn_context(user_text: str):
    return open_builtin_knowledge(
        get_config_manager().knowledge_dir
    ).build_conversation_context(user_text, limit=1)


async def build_public_knowledge_turn_context(user_text: str) -> str:
    """Build at most one disposable context card within the latency budget."""
    if not PUBLIC_KNOWLEDGE_AUTO_CONTEXT_ENABLED or not str(user_text).strip():
        return ""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_build_turn_context, user_text),
            timeout=PUBLIC_KNOWLEDGE_AUTO_CONTEXT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        _record_route(result="timeout", error_type="TimeoutError")
        logger.warning("[public-knowledge] automatic context timed out")
        return ""
    except Exception as exc:
        _record_route(result="error", error_type=type(exc).__name__)
        logger.warning(
            "[public-knowledge] automatic context failed: %s",
            type(exc).__name__,
        )
        return ""

    _record_route(
        collection_id=result.collection_id,
        match_mode=result.match_mode,
        card_delivered=bool(result.text),
        result="matched" if result.hit_count else "miss",
    )
    logger.info(
        "[public-knowledge] automatic context hits=%d mode=%s collection=%s",
        result.hit_count,
        result.match_mode,
        result.collection_id or "none",
    )
    return result.text


__all__ = [
    "build_public_knowledge_turn_context",
    "handle_public_knowledge_call",
    "register_public_knowledge_tool",
]
