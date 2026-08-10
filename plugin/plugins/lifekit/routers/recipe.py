"""菜谱 router — 搜索菜谱 + 随机推荐。

数据源: TheMealDB (免费, 无需 key)。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .. import _recipe as recipe_api
from .._chat import push_lifekit_content
from .._contracts import RandomRecipeResult, SearchRecipeParams, SearchRecipeResult
from .._entry_errors import unavailable_error


def _format_recipe_summary(r: recipe_api.Recipe) -> str:
    """生成菜谱的 LLM 友好摘要。"""
    parts = [r.name]
    if r.area:
        parts.append(f"({r.area})")
    if r.category:
        parts.append(f"[{r.category}]")
    return " ".join(parts)


def _format_ingredients(ingredients: List[Dict[str, str]]) -> str:
    """格式化食材列表。"""
    lines = []
    for ing in ingredients:
        measure = ing.get("measure", "").strip()
        name = ing.get("name", "")
        if measure:
            lines.append(f"  • {name} — {measure}")
        else:
            lines.append(f"  • {name}")
    return "\n".join(lines)


def _recipe_to_dict(r: recipe_api.Recipe, brief: bool = False) -> Dict[str, Any]:
    """转换为 JSON 可序列化的 dict。"""
    d: Dict[str, Any] = {
        "id": r.id,
        "name": r.name,
    }
    if r.category:
        d["category"] = r.category
    if r.area:
        d["area"] = r.area
    if r.thumbnail:
        d["thumbnail"] = r.thumbnail
    if not brief:
        if r.ingredients:
            d["ingredients"] = r.ingredients
        if r.instructions:
            d["instructions"] = r.instructions
        if r.tags:
            d["tags"] = r.tags
    return d


class RecipeRouter(PluginRouter):
    """search_recipe + random_recipe entries。"""

    def __init__(self):
        super().__init__(name="recipe")

    @plugin_entry(
        id="search_recipe",
        name=tr("recipe.entry_search_name", default="Search recipes"),
        description=tr(
            "recipe.entry_search_description",
            default="Search recipes by the requested dish name or ingredient without replacing a dish with a generic ingredient.",
        ),
        params=SearchRecipeParams,
        llm_result_model=SearchRecipeResult,
    )
    @quick_action(icon="📖", priority=5)
    async def search_recipe(
        self,
        params: SearchRecipeParams | None = None,
        query: str = "",
        by_ingredient: bool = False,
        **_,
    ):
        if params is not None:
            query = params.query
            by_ingredient = params.by_ingredient

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not query.strip():
            return Err(SdkError(i18n.t("recipe.no_query")))

        q = query.strip()
        try:
            if by_ingredient:
                brief_results = await recipe_api.search_by_ingredient(q)
                details = await asyncio.gather(
                    *(recipe_api.get_by_id(brief.id) for brief in brief_results[:3]),
                    return_exceptions=True,
                )
                detailed = [
                    recipe
                    for recipe in details
                    if isinstance(recipe, recipe_api.Recipe)
                ]
                results = detailed if detailed else brief_results
                result_count = len(brief_results)
            else:
                results = await recipe_api.search_by_name(q)
                result_count = len(results)
        except recipe_api.RecipeAPIError:
            return unavailable_error(
                i18n.t("recipe.provider_unavailable"),
                code="UPSTREAM_UNAVAILABLE",
                details={"recipes": [], "query": q},
            )

        if not results:
            return Ok({
                "status": "ready",
                "summary": i18n.t("recipe.not_found", query=q),
                "recipes": [],
                "query": q,
            })

        # 取前 3 个
        top = results[:3]
        recipes_data = [_recipe_to_dict(r) for r in top]

        # 摘要
        names = "、".join(_format_recipe_summary(r) for r in top)
        summary = i18n.t("recipe.found", count=result_count, names=names)

        # 推送卡片 — 只展示第一个的详情
        first = top[0]
        blocks = [
            {"type": "text", "text": f"📖 {_format_recipe_summary(first)}"},
        ]
        if first.ingredients:
            blocks.append({"type": "text", "text": f"🥘 {i18n.t('recipe.ingredients')}:\n{_format_ingredients(first.ingredients)}"})
        if first.instructions:
            # 截取前 200 字符
            steps = first.instructions[:200]
            if len(first.instructions) > 200:
                steps += "…"
            blocks.append({"type": "text", "text": f"👨‍🍳 {i18n.t('recipe.instructions')}:\n{steps}"})
        if first.thumbnail:
            blocks.append({"type": "image", "url": first.thumbnail, "alt": first.name})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({
            "status": "ready",
            "summary": summary,
            "recipes": recipes_data,
            "query": q,
            "count": result_count,
            "next_actions": [
                i18n.t("recipe.action_food", query=q),
                i18n.t("recipe.action_market"),
            ],
        })

    @plugin_entry(
        id="random_recipe",
        name=tr("recipe.entry_random_name", default="Random recipe"),
        description=tr(
            "recipe.entry_random_description",
            default="Recommend a random recipe and suggest nearby restaurants as an alternative.",
        ),
        llm_result_model=RandomRecipeResult,
    )
    @quick_action(icon="🎲", priority=4)
    async def random_recipe(self, **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n
        try:
            meal = await recipe_api.random_meal()
        except recipe_api.RecipeAPIError:
            return unavailable_error(
                i18n.t("recipe.random_fail"),
                code="UPSTREAM_UNAVAILABLE",
                details={"recipe": None},
            )
        if not meal:
            return unavailable_error(
                i18n.t("recipe.random_fail"),
                code="UPSTREAM_UNAVAILABLE",
                details={"recipe": None},
            )

        recipe_data = _recipe_to_dict(meal)
        summary = i18n.t("recipe.random_summary", recipe=_format_recipe_summary(meal))

        # 推送卡片
        blocks = [
            {"type": "text", "text": i18n.t("recipe.today_try", recipe=_format_recipe_summary(meal))},
        ]
        if meal.ingredients:
            blocks.append({"type": "text", "text": f"🥘 {i18n.t('recipe.ingredients')}:\n{_format_ingredients(meal.ingredients)}"})
        if meal.instructions:
            steps = meal.instructions[:200]
            if len(meal.instructions) > 200:
                steps += "…"
            blocks.append({"type": "text", "text": f"👨‍🍳 {i18n.t('recipe.instructions')}:\n{steps}"})
        if meal.thumbnail:
            blocks.append({"type": "image", "url": meal.thumbnail, "alt": meal.name})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({
            "status": "ready",
            "summary": summary,
            "recipe": recipe_data,
            "next_actions": [
                i18n.t("recipe.action_food", query=meal.name),
                i18n.t("recipe.action_market"),
            ],
        })
