import pytest


def test_deep_research_prompt_maps_cover_supported_languages():
    from config.prompts.prompts_activity import (
        DEEP_RESEARCH_PLAN_PROMPTS,
        DEEP_RESEARCH_REFLECT_PROMPTS,
        DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS,
        DEEP_RESEARCH_SYNTHESIS_PROMPTS,
    )

    expected = {"zh", "zh-TW", "en", "ja", "ko", "es", "pt", "ru"}

    for prompts in (
        DEEP_RESEARCH_PLAN_PROMPTS,
        DEEP_RESEARCH_REFLECT_PROMPTS,
        DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS,
        DEEP_RESEARCH_SYNTHESIS_PROMPTS,
    ):
        assert set(prompts) == expected


def test_source_summary_prompts_mark_page_content_untrusted():
    from config.prompts.prompts_activity import DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS

    for prompt in DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS.values():
        lowered = prompt.lower()
        assert "不可信" in prompt or "untrusted" in lowered
        assert "不得执行" in prompt or "不得執行" in prompt or "do not follow" in lowered


@pytest.mark.asyncio
async def test_research_llm_parses_plan_reflect_and_synthesis(monkeypatch):
    from main_logic.topic import research_llm

    responses = iter([
        """{"initial_queries":["文本世界模型 最新","文本世界模型 案例"],"media_intent":["web","image"],"success_criteria":["具体事实"]}""",
        """{"enough":false,"missing":["缺应用"],"next_queries":["文本世界模型 应用"],"media_intent":["news"]}""",
        """{"material_hint":{"summary":"可借一个应用案例开口","links":[{"type":"news","title":"案例","url":"https://example.test"}]},"online_query":"文本世界模型 应用","online_angle":"应用案例"}""",
    ])

    async def fake_invoke(prompt, *, timeout, label, max_completion_tokens):
        assert timeout == 3.0
        assert max_completion_tokens > 0
        return next(responses)

    monkeypatch.setattr(research_llm, "_invoke_topic_research_tier", fake_invoke)

    plan = await research_llm.derive_deep_research_plan(
        interest="文本世界模型",
        keywords=["幻觉"],
        floor_angle="",
        lang="zh-CN",
        timeout=3.0,
    )
    reflect = await research_llm.derive_deep_research_reflect(
        interest="文本世界模型",
        keywords=["幻觉"],
        plan=plan,
        evidence=[{"summary": "已有证据"}],
        lang="zh-CN",
        timeout=3.0,
    )
    synthesis = await research_llm.derive_deep_research_synthesis(
        interest="文本世界模型",
        keywords=["幻觉"],
        plan=plan,
        evidence=[{"summary": "已有证据"}],
        lang="zh-CN",
        timeout=3.0,
    )

    assert plan["initial_queries"] == ["文本世界模型 最新", "文本世界模型 案例"]
    assert plan["media_intent"] == ["web", "image"]
    assert reflect["next_queries"] == ["文本世界模型 应用"]
    assert synthesis["material_hint"]["summary"] == "可借一个应用案例开口"


@pytest.mark.asyncio
async def test_research_llm_uses_old_single_query_fallback(monkeypatch):
    from main_logic.topic import research_llm

    async def no_plan(*args, **kwargs):
        return None

    async def old_query(**kwargs):
        return "旧 deep query"

    monkeypatch.setattr(research_llm, "_invoke_topic_research_tier", no_plan)
    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query",
        old_query,
    )

    plan = await research_llm.derive_deep_research_plan(
        interest="旧话题",
        keywords=["兼容"],
        floor_angle="floor",
        lang="zh-CN",
    )

    assert plan["initial_queries"] == ["旧 deep query"]
    assert plan["media_intent"] == ["news"]
    assert plan["success_criteria"] == []
