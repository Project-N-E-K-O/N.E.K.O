"""Delivery-time deep research helpers for proactive topic hooks.

This module owns the small "search first, then chat" preparation step behind
``TopicHookPool._deepen_material``. The first version intentionally preserves
the existing one-query behavior while giving later PRs a narrow place to add
planning, reflection, and a separate research knowledge store.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

DeriveQuery = Callable[..., Awaitable[str | None]]
EnrichMaterials = Callable[..., Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class DeepResearchBudget:
    """Hard limits for delivery-time research work."""

    max_rounds: int = 1
    per_call_timeout: float = 8.0
    total_wall_clock: float = 12.0

    def normalized(self) -> "DeepResearchBudget":
        return DeepResearchBudget(
            max_rounds=max(0, int(self.max_rounds)),
            per_call_timeout=max(0.001, float(self.per_call_timeout)),
            total_wall_clock=max(0.001, float(self.total_wall_clock)),
        )


@dataclass
class DeepResearchResult:
    """Result prepared for ``_deepen_material`` to merge into a material."""

    material_updates: dict[str, Any] = field(default_factory=dict)
    knowledge_items: list[dict[str, Any]] = field(default_factory=list)
    fallback_reason: str = ""


async def _default_derive_query(**kwargs: Any) -> str | None:
    from main_logic.activity.llm_enrichment import derive_deep_search_query

    return await derive_deep_search_query(**kwargs)


async def _default_enrich_materials(
    materials: list[Mapping[str, Any]], **kwargs: Any
) -> list[dict[str, Any]]:
    from main_logic.topic.materials import enrich_topic_materials_online

    return await enrich_topic_materials_online(materials, **kwargs)


async def run_deep_research(
    *,
    material: Mapping[str, Any],
    lang: str,
    budget: DeepResearchBudget | None = None,
    derive_query: DeriveQuery | None = None,
    enrich_materials: EnrichMaterials | None = None,
) -> DeepResearchResult:
    """Prepare deeper material updates while preserving floor fallback.

    The current implementation is intentionally one round: derive one focused
    query, re-run the existing online enrichment, and return only the fields
    that should be merged back into the material. Later PRs can expand inside
    this function without changing ``TopicHookPool`` again.
    """

    normalized_budget = (budget or DeepResearchBudget()).normalized()

    async def _run_once() -> DeepResearchResult:
        if normalized_budget.max_rounds <= 0:
            return DeepResearchResult(fallback_reason="budget_exhausted")

        derive = derive_query or _default_derive_query
        enrich = enrich_materials or _default_enrich_materials

        floor_hint = material.get("material_hint")
        query = await derive(
            interest=str(material.get("interest") or ""),
            keywords=list(material.get("keywords") or []),
            floor_angle=str(material.get("online_angle") or ""),
            lang=lang,
            timeout=normalized_budget.per_call_timeout,
        )
        if not query:
            return DeepResearchResult(fallback_reason="empty_query")

        probe = dict(material)
        probe["deep_query"] = query
        probe.pop("material_hint", None)
        updates: dict[str, Any] = {"deep_query": query}

        try:
            enriched = await enrich([probe], lang=lang, max_materials=1)
        except Exception:
            if floor_hint is not None:
                updates["material_hint"] = floor_hint
            return DeepResearchResult(
                material_updates=updates,
                fallback_reason="enrichment_error",
            )
        deep = enriched[0] if enriched else None
        if isinstance(deep, Mapping) and deep.get("material_hint"):
            for key in ("material_hint", "online_used", "online_query", "online_angle"):
                if key in deep:
                    updates[key] = deep[key]
            return DeepResearchResult(material_updates=updates)

        if floor_hint is not None:
            updates["material_hint"] = floor_hint
        return DeepResearchResult(
            material_updates=updates,
            fallback_reason="empty_enrichment",
        )

    try:
        return await asyncio.wait_for(
            _run_once(),
            timeout=normalized_budget.total_wall_clock,
        )
    except asyncio.TimeoutError:
        return DeepResearchResult(fallback_reason="timeout")
    except Exception as exc:
        return DeepResearchResult(fallback_reason=f"error:{exc.__class__.__name__}")
