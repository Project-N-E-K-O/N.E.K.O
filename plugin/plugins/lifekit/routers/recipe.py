"""菜谱 router — 搜索菜谱 + 随机推荐。

数据源: TheMealDB (免费, 无需 key)。
"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .. import _recipe as recipe_api


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
        name="搜索菜谱",
        description=(
            "按菜名或食材搜索菜谱，返回做法、食材清单。"
            "支持中英文菜名。搜不到时 LLM 可自行补充。"
        ),
        llm_result_fields=["summary", "recipes"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "菜名或食材（如：红烧肉、chicken curry、tomato）",
                },
                "by_ingredient": {
                    "type": "boolean",
                    "description": "是否按食材搜索（默认按菜名）",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    )
    @quick_action(icon="📖", priority=5)
    async def search_recipe(self, query: str = "", by_ingredient: bool = False, **_):
        if not query.strip():
            return Err(SdkError("请输入菜名或食材"))

        q = query.strip()

        if by_ingredient:
            results = await recipe_api.search_by_ingredient(q)
            # 食材搜索只返回简要列表，取前3个获取详情
            detailed: List[recipe_api.Recipe] = []
            for brief in results[:3]:
                full = await recipe_api.get_by_id(brief.id)
                if full:
                    detailed.append(full)
            results = detailed if detailed else results
        else:
            results = await recipe_api.search_by_name(q)

        if not results:
            return Ok({
                "summary": f"没有找到「{q}」相关的菜谱，你可以直接问我怎么做",
                "recipes": [],
                "query": q,
            })

        # 取前 3 个
        top = results[:3]
        recipes_data = [_recipe_to_dict(r) for r in top]

        # 摘要
        names = "、".join(_format_recipe_summary(r) for r in top)
        summary = f"找到 {len(results)} 个菜谱: {names}"

        # 推送卡片 — 只展示第一个的详情
        first = top[0]
        blocks = [
            {"type": "text", "text": f"📖 {_format_recipe_summary(first)}"},
        ]
        if first.ingredients:
            blocks.append({"type": "text", "text": f"🥘 食材:\n{_format_ingredients(first.ingredients)}"})
        if first.instructions:
            # 截取前 200 字符
            steps = first.instructions[:200]
            if len(first.instructions) > 200:
                steps += "…"
            blocks.append({"type": "text", "text": f"👨‍🍳 做法:\n{steps}"})
        if first.thumbnail:
            blocks.append({"type": "image", "url": first.thumbnail, "alt": first.name})

        self.main_plugin.push_chat_content(blocks)

        return Ok({
            "summary": summary,
            "recipes": recipes_data,
            "query": q,
            "count": len(results),
        })

    @plugin_entry(
        id="random_recipe",
        name="随机菜谱",
        description="随机推荐一道菜，适合回答「今天吃什么」「不知道做什么菜」。",
        llm_result_fields=["summary", "recipe"],
    )
    @quick_action(icon="🎲", priority=4)
    async def random_recipe(self, **_):
        meal = await recipe_api.random_meal()
        if not meal:
            return Ok({
                "summary": "随机菜谱获取失败，请稍后重试",
                "recipe": None,
            })

        recipe_data = _recipe_to_dict(meal)
        summary = f"🎲 随机推荐: {_format_recipe_summary(meal)}"

        # 推送卡片
        blocks = [
            {"type": "text", "text": f"🎲 今天试试: {_format_recipe_summary(meal)}"},
        ]
        if meal.ingredients:
            blocks.append({"type": "text", "text": f"🥘 食材:\n{_format_ingredients(meal.ingredients)}"})
        if meal.instructions:
            steps = meal.instructions[:200]
            if len(meal.instructions) > 200:
                steps += "…"
            blocks.append({"type": "text", "text": f"👨‍🍳 做法:\n{steps}"})
        if meal.thumbnail:
            blocks.append({"type": "image", "url": meal.thumbnail, "alt": meal.name})

        self.main_plugin.push_chat_content(blocks)

        return Ok({
            "summary": summary,
            "recipe": recipe_data,
        })
