import asyncio

import pytest

from main_logic.topic.deep_research import DeepResearchBudget, run_deep_research


@pytest.mark.asyncio
async def test_run_deep_research_returns_updates_and_preserves_future_knowledge_slot():
    async def fake_derive(**kwargs):
        return "文本世界模型 幻觉 最新研究"

    async def fake_enrich(materials, **kwargs):
        material = dict(materials[0])
        material["material_hint"] = {"summary": f"deep:{material['deep_query']}"}
        material["online_used"] = True
        material["online_query"] = material["deep_query"]
        material["online_angle"] = "最新研究综述"
        return [material]

    result = await run_deep_research(
        material={
            "interest": "文本世界模型",
            "keywords": ["文本世界模型", "幻觉"],
            "material_hint": {"summary": "floor"},
            "online_angle": "floor angle",
        },
        lang="zh-CN",
        derive_query=fake_derive,
        enrich_materials=fake_enrich,
    )

    assert result.material_updates == {
        "deep_query": "文本世界模型 幻觉 最新研究",
        "material_hint": {"summary": "deep:文本世界模型 幻觉 最新研究"},
        "online_used": True,
        "online_query": "文本世界模型 幻觉 最新研究",
        "online_angle": "最新研究综述",
    }
    assert result.knowledge_items == []
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_run_deep_research_restores_floor_when_enrich_finds_nothing():
    async def fake_derive(**kwargs):
        return "some deep query"

    async def fake_enrich(materials, **kwargs):
        return [dict(materials[0])]

    result = await run_deep_research(
        material={
            "interest": "x",
            "keywords": ["x"],
            "material_hint": {"summary": "floor"},
        },
        lang="zh-CN",
        derive_query=fake_derive,
        enrich_materials=fake_enrich,
    )

    assert result.material_updates == {
        "deep_query": "some deep query",
        "material_hint": {"summary": "floor"},
    }
    assert result.fallback_reason == "empty_enrichment"


@pytest.mark.asyncio
async def test_run_deep_research_budget_timeout_returns_no_updates():
    async def slow_derive(**kwargs):
        await asyncio.Future()
        return "late query"

    async def fake_enrich(materials, **kwargs):
        raise AssertionError("enrich should not run after timeout")

    result = await run_deep_research(
        material={"interest": "x", "keywords": ["x"]},
        lang="zh-CN",
        budget=DeepResearchBudget(total_wall_clock=0.05),
        derive_query=slow_derive,
        enrich_materials=fake_enrich,
    )

    assert result.material_updates == {}
    assert result.fallback_reason == "timeout"


@pytest.mark.asyncio
async def test_run_deep_research_forwards_per_call_timeout_to_enrich():
    captured = {}

    async def fake_derive(**kwargs):
        return "deep query"

    async def fake_enrich(materials, **kwargs):
        captured["timeout_s"] = kwargs.get("timeout_s")
        material = dict(materials[0])
        material["material_hint"] = {"summary": "deep"}
        return [material]

    await run_deep_research(
        material={"interest": "x", "keywords": ["x"]},
        lang="zh-CN",
        budget=DeepResearchBudget(per_call_timeout=2.5),
        derive_query=fake_derive,
        enrich_materials=fake_enrich,
    )

    assert "timeout_s" in captured
    assert captured["timeout_s"] == 2.5


@pytest.mark.asyncio
async def test_run_deep_research_keeps_query_and_floor_when_enrich_raises():
    async def fake_derive(**kwargs):
        return "deep query before failure"

    async def failing_enrich(materials, **kwargs):
        raise RuntimeError("fetch failed")

    result = await run_deep_research(
        material={
            "interest": "x",
            "keywords": ["x"],
            "material_hint": {"summary": "floor"},
        },
        lang="zh-CN",
        derive_query=fake_derive,
        enrich_materials=failing_enrich,
    )

    assert result.material_updates == {
        "deep_query": "deep query before failure",
        "material_hint": {"summary": "floor"},
    }
    assert result.fallback_reason == "enrichment_error:RuntimeError: fetch failed"
