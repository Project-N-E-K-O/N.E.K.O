"""TheMealDB API 封装 — 免费菜谱数据源，无需 key。

https://www.themealdb.com/api.php
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

_BASE = "https://www.themealdb.com/api/json/v1/1"
_TIMEOUT = 10.0


@dataclass
class Recipe:
    """一条菜谱。"""
    id: str
    name: str
    category: str = ""
    area: str = ""          # 菜系 (Chinese, Japanese, Italian, ...)
    instructions: str = ""
    thumbnail: str = ""
    tags: List[str] = field(default_factory=list)
    ingredients: List[Dict[str, str]] = field(default_factory=list)  # [{"name": "鸡蛋", "measure": "2个"}]
    source: str = ""        # 原始来源 URL
    youtube: str = ""


def _parse_meal(meal: Dict[str, Any]) -> Recipe:
    """从 TheMealDB JSON 解析一条菜谱。"""
    ingredients: List[Dict[str, str]] = []
    for i in range(1, 21):
        name = (meal.get(f"strIngredient{i}") or "").strip()
        measure = (meal.get(f"strMeasure{i}") or "").strip()
        if name:
            ingredients.append({"name": name, "measure": measure})

    tags_raw = meal.get("strTags") or ""
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    return Recipe(
        id=str(meal.get("idMeal", "")),
        name=meal.get("strMeal") or "",
        category=meal.get("strCategory") or "",
        area=meal.get("strArea") or "",
        instructions=(meal.get("strInstructions") or "").strip(),
        thumbnail=meal.get("strMealThumb") or "",
        tags=tags,
        ingredients=ingredients,
        source=meal.get("strSource") or "",
        youtube=meal.get("strYoutube") or "",
    )


async def search_by_name(query: str) -> List[Recipe]:
    """按菜名搜索。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/search.php", params={"s": query})
            data = r.json()
        meals = data.get("meals")
        if not meals:
            return []
        return [_parse_meal(m) for m in meals]
    except Exception:
        return []


async def search_by_ingredient(ingredient: str) -> List[Recipe]:
    """按食材搜索（返回简要列表，无详细步骤）。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/filter.php", params={"i": ingredient})
            data = r.json()
        meals = data.get("meals")
        if not meals:
            return []
        return [
            Recipe(
                id=str(m.get("idMeal", "")),
                name=m.get("strMeal") or "",
                thumbnail=m.get("strMealThumb") or "",
            )
            for m in meals
        ]
    except Exception:
        return []


async def get_by_id(meal_id: str) -> Optional[Recipe]:
    """按 ID 获取完整菜谱。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/lookup.php", params={"i": meal_id})
            data = r.json()
        meals = data.get("meals")
        if not meals:
            return None
        return _parse_meal(meals[0])
    except Exception:
        return None


async def random_meal() -> Optional[Recipe]:
    """随机获取一道菜。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/random.php")
            data = r.json()
        meals = data.get("meals")
        if not meals:
            return None
        return _parse_meal(meals[0])
    except Exception:
        return None


async def list_categories() -> List[Dict[str, str]]:
    """获取所有分类。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/categories.php")
            data = r.json()
        cats = data.get("categories")
        if not cats:
            return []
        return [
            {"name": c.get("strCategory", ""), "description": c.get("strCategoryDescription", "")[:80]}
            for c in cats
        ]
    except Exception:
        return []


async def filter_by_category(category: str) -> List[Recipe]:
    """按分类筛选。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/filter.php", params={"c": category})
            data = r.json()
        meals = data.get("meals")
        if not meals:
            return []
        return [
            Recipe(
                id=str(m.get("idMeal", "")),
                name=m.get("strMeal") or "",
                thumbnail=m.get("strMealThumb") or "",
            )
            for m in meals
        ]
    except Exception:
        return []
