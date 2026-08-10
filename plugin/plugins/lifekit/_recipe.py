"""TheMealDB API 封装 — 免费菜谱数据源，无需 key。

https://www.themealdb.com/api.php
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

_BASE = "https://www.themealdb.com/api/json/v1/1"
_TIMEOUT = 8.0


class RecipeAPIError(RuntimeError):
    """Expected failure at the external recipe provider boundary."""

    def __init__(self, message: str, *, cause: str) -> None:
        super().__init__(message)
        self.cause = cause


def _text(value: Any) -> str:
    """Normalize optional provider text without trusting its JSON scalar type."""
    return value.strip() if isinstance(value, str) else ""


def _identifier(value: Any) -> str:
    return "" if value is None else str(value).strip()


async def _request_json(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{_BASE}/{path}", params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise RecipeAPIError("recipe provider timed out", cause="timeout") from exc
    except httpx.HTTPError as exc:
        raise RecipeAPIError("recipe provider request failed", cause="network") from exc
    except (TypeError, ValueError) as exc:
        raise RecipeAPIError("invalid recipe provider response", cause="invalid_response") from exc
    if not isinstance(payload, dict):
        raise RecipeAPIError("invalid recipe provider response", cause="invalid_response")
    return payload


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
        name = _text(meal.get(f"strIngredient{i}"))
        measure = _text(meal.get(f"strMeasure{i}"))
        if name:
            ingredients.append({"name": name, "measure": measure})

    tags_raw = _text(meal.get("strTags"))
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    return Recipe(
        id=_identifier(meal.get("idMeal")),
        name=_text(meal.get("strMeal")),
        category=_text(meal.get("strCategory")),
        area=_text(meal.get("strArea")),
        instructions=_text(meal.get("strInstructions")),
        thumbnail=_text(meal.get("strMealThumb")),
        tags=tags,
        ingredients=ingredients,
        source=_text(meal.get("strSource")),
        youtube=_text(meal.get("strYoutube")),
    )


async def search_by_name(query: str) -> List[Recipe]:
    """按菜名搜索。"""
    data = await _request_json("search.php", params={"s": query})
    meals = data.get("meals")
    if not isinstance(meals, list):
        return []
    return [_parse_meal(m) for m in meals if isinstance(m, dict)]


async def search_by_ingredient(ingredient: str) -> List[Recipe]:
    """按食材搜索（返回简要列表，无详细步骤）。"""
    data = await _request_json("filter.php", params={"i": ingredient})
    meals = data.get("meals")
    if not isinstance(meals, list):
        return []
    return [
        Recipe(
            id=_identifier(m.get("idMeal")),
            name=_text(m.get("strMeal")),
            thumbnail=_text(m.get("strMealThumb")),
        )
        for m in meals
        if isinstance(m, dict)
    ]


async def get_by_id(meal_id: str) -> Optional[Recipe]:
    """按 ID 获取完整菜谱。"""
    data = await _request_json("lookup.php", params={"i": meal_id})
    meals = data.get("meals")
    if not isinstance(meals, list) or not meals or not isinstance(meals[0], dict):
        return None
    return _parse_meal(meals[0])


async def random_meal() -> Optional[Recipe]:
    """随机获取一道菜。"""
    data = await _request_json("random.php")
    meals = data.get("meals")
    if not isinstance(meals, list) or not meals or not isinstance(meals[0], dict):
        return None
    return _parse_meal(meals[0])
