from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.plugin import Ok

from plugin.plugins.lifekit import _recipe
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit.routers.recipe import RecipeRouter


class _Plugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")


@pytest.mark.asyncio
async def test_chinese_dish_name_is_not_replaced_with_a_generic_ingredient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    async def search(query: str) -> list[_recipe.Recipe]:
        queries.append(query)
        return []

    monkeypatch.setattr(_recipe, "search_by_name", search)
    router = RecipeRouter()
    router._bind(_Plugin())

    result = await router.search_recipe(query="红烧肉")

    assert isinstance(result, Ok)
    assert queries == ["红烧肉"]
    assert result.value["query"] == "红烧肉"


@pytest.mark.asyncio
async def test_recipe_provider_failure_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed(_: str) -> list[_recipe.Recipe]:
        raise _recipe.RecipeAPIError("provider down", cause="network")

    monkeypatch.setattr(_recipe, "search_by_name", failed)
    router = RecipeRouter()
    router._bind(_Plugin())

    result = await router.search_recipe(query="chicken")

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["recipes"] == []


def test_recipe_parser_tolerates_non_text_provider_fields() -> None:
    recipe = _recipe._parse_meal({
        "idMeal": 42,
        "strMeal": 7,
        "strIngredient1": 123,
        "strMeasure1": {"bad": "shape"},
        "strInstructions": ["bad", "shape"],
        "strTags": 9,
    })

    assert recipe.id == "42"
    assert recipe.name == ""
    assert recipe.ingredients == []
    assert recipe.instructions == ""
    assert recipe.tags == []
