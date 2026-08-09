"""Pydantic contracts for LifeKit entries."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, RootModel, field_validator


def _blankable_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class LifeKitModel(BaseModel):
    model_config = {"extra": "ignore"}


class ClarificationResult(LifeKitModel):
    status: Literal["clarify"]
    summary: str = Field(..., min_length=1)
    choices: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary", mode="before")
    @classmethod
    def clarification_summary_must_not_be_blank(cls, value: Any) -> str:
        summary = _blankable_text(value)
        if not summary:
            raise ValueError("clarification summary must not be blank")
        return summary


class ClarifiableResult(RootModel[Any]):
    """Validated union of a complete ready result and a clarification result."""

    @property
    def status(self) -> str:
        return str(self.root.status)

    @property
    def summary(self) -> str:
        return str(getattr(self.root, "summary", ""))

    @property
    def choices(self) -> list[str]:
        return list(getattr(self.root, "choices", []))


class SavedLocationModel(LifeKitModel):
    id: str | None = None
    label: str
    city: str
    address: str = ""
    lat: float
    lon: float
    country: str = ""
    is_default: bool = False


class ListLocationsResult(LifeKitModel):
    count: int
    locations: list[dict[str, Any]]


class MessageResult(LifeKitModel):
    message: str


class AddLocationParams(LifeKitModel):
    label: str = Field(..., min_length=1, description="地点标签")
    city: str = Field(..., min_length=1, description="城市名")
    address: str = Field("", description="可选的详细地址")
    set_default: bool = Field(False, description="是否设为默认地点")

    @field_validator("label", "city", "address", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _AddLocationReadyResult(LifeKitModel):
    status: Literal["ready"]
    message: str
    location: dict[str, Any]


class AddLocationResult(ClarifiableResult):
    root: Annotated[
        _AddLocationReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class LocationIdParams(LifeKitModel):
    location_id: str = Field(..., min_length=1, description="地点 ID 或地点标签")

    @field_validator("location_id", mode="before")
    @classmethod
    def _clean_location_id(cls, value: Any) -> str:
        return _blankable_text(value)


class RemoveLocationResult(MessageResult):
    remaining: int


class HourlyForecastParams(LifeKitModel):
    city: str = Field("", description="城市名，留空则自动定位")
    hours: int = Field(48, ge=1, le=168, description="预报小时数（1-168，默认 48）")

    @field_validator("city", mode="before")
    @classmethod
    def _clean_city(cls, value: Any) -> str:
        return _blankable_text(value)


class _HourlyForecastReadyResult(LifeKitModel):
    status: Literal["ready"]
    city: str
    summary: str
    hours: list[dict[str, Any]]
    total_hours: int


class HourlyForecastResult(ClarifiableResult):
    root: Annotated[
        _HourlyForecastReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class NearbyParams(LifeKitModel):
    query: str = Field(..., min_length=1, description="搜索关键词或用户原始附近需求")
    location: str = Field("", description="搜索中心（地点标签或城市名，留空用默认位置）")
    radius: int = Field(3000, ge=500, le=50000, description="搜索半径（米，默认 3000）")

    @field_validator("query", "location", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _NearbyReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    results: list[dict[str, Any]]
    count: int
    provider: str | None = None
    weather_tip: str = ""


class NearbyResult(ClarifiableResult):
    root: Annotated[
        _NearbyReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class FoodRecommendParams(LifeKitModel):
    cuisine: str = Field("", description="口味/菜系偏好（如：火锅、日料、川菜、意大利菜），留空则根据天气推荐")
    scene: str = Field("", description="用餐场景：聚餐/约会/一人食/家庭/宵夜，留空不限")
    location: str = Field("", description="位置（地点标签或城市名，留空用默认位置）")
    radius: int = Field(3000, ge=500, le=50000, description="搜索半径（米，默认 3000）")

    @field_validator("cuisine", "scene", "location", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _FoodRecommendReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    recommendations: list[dict[str, Any]]
    query: str
    weather_reason: str = ""
    provider: str | None = None
    next_actions: list[str] = Field(default_factory=list)


class FoodRecommendResult(ClarifiableResult):
    root: Annotated[
        _FoodRecommendReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class UnitConvertParams(LifeKitModel):
    value: float = Field(..., description="要换算的数值")
    from_unit: str = Field(..., min_length=1, description="源单位（如 cm, kg, °C, cup, ml）")
    to_unit: str = Field(..., min_length=1, description="目标单位（如 inch, lb, °F, ml, g）")

    @field_validator("from_unit", "to_unit", mode="before")
    @classmethod
    def _clean_unit(cls, value: Any) -> str:
        return _blankable_text(value)


class UnitConvertResult(LifeKitModel):
    summary: str
    conversion: dict[str, Any]


class CityParams(LifeKitModel):
    city: str = Field("", description="城市名，留空则自动定位或使用默认地点")

    @field_validator("city", mode="before")
    @classmethod
    def _clean_city(cls, value: Any) -> str:
        return _blankable_text(value)


class _GetWeatherReadyResult(LifeKitModel):
    status: Literal["ready"]
    city: str
    summary: str
    current: dict[str, Any]
    forecast: list[dict[str, Any]]
    vpn_detected: bool = False
    next_actions: list[str] = Field(default_factory=list)


class GetWeatherResult(ClarifiableResult):
    root: Annotated[
        _GetWeatherReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class _AirQualityReadyResult(LifeKitModel):
    status: Literal["ready"]
    city: str
    summary: str
    aqi: dict[str, Any]
    advice: list[str]
    next_actions: list[str] = Field(default_factory=list)


class AirQualityResult(ClarifiableResult):
    root: Annotated[
        _AirQualityReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class _TravelAdviceReadyResult(LifeKitModel):
    status: Literal["ready"]
    city: str
    summary: str
    tips: list[str]
    clothing: str = ""
    umbrella: bool = False
    sunscreen: bool = False
    next_actions: list[str] = Field(default_factory=list)


class TravelAdviceResult(ClarifiableResult):
    root: Annotated[
        _TravelAdviceReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class CurrencyConvertParams(LifeKitModel):
    amount: float = Field(1, description="金额（默认 1）")
    from_currency: str = Field(..., min_length=1, description="源货币代码（如 USD, CNY, EUR, JPY）")
    to_currency: str = Field(..., min_length=1, description="目标货币代码（如 CNY, USD, EUR）")

    @field_validator("from_currency", "to_currency", mode="before")
    @classmethod
    def _clean_currency(cls, value: Any) -> str:
        return _blankable_text(value).upper()


class CurrencyConvertResult(LifeKitModel):
    summary: str
    conversion: dict[str, Any]
    next_actions: list[str] = Field(default_factory=list)


class CountdownParams(LifeKitModel):
    target_date: str = Field(..., min_length=1, description="目标日期：YYYY-MM-DD、MM-DD、或节日名（元旦/圣诞节/国庆节等）")
    label: str = Field("", description="事件名称（如：生日、旅行、考试），留空自动识别")

    @field_validator("target_date", "label", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class DaysBetweenParams(LifeKitModel):
    start_date: str = Field("", description="起始日期 (YYYY-MM-DD)，留空表示今天")
    end_date: str = Field("", description="结束日期 (YYYY-MM-DD)，留空表示今天")

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class DateDetailResult(LifeKitModel):
    summary: str
    detail: dict[str, Any]


class SearchRecipeParams(LifeKitModel):
    query: str = Field(..., min_length=1, description="菜名或食材（如：红烧肉、chicken curry、tomato）")
    by_ingredient: bool = Field(False, description="是否按食材搜索（默认按菜名）")

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value: Any) -> str:
        return _blankable_text(value)


class SearchRecipeResult(LifeKitModel):
    summary: str
    recipes: list[dict[str, Any]]
    query: str = ""
    count: int = 0
    next_actions: list[str] = Field(default_factory=list)


class RandomRecipeResult(LifeKitModel):
    summary: str
    recipe: dict[str, Any] | None = None
    next_actions: list[str] = Field(default_factory=list)


class TripAdviceParams(LifeKitModel):
    origin: str = Field("", description="起点（地点标签或城市名，留空用默认地点）")
    destination: str = Field(..., min_length=1, description="终点（地点标签或城市名）")
    mode: str = Field("", description="出行方式: transit/walking/bicycling/driving，留空自动推荐")

    @field_validator("origin", "destination", "mode", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _TripAdviceReadyResult(LifeKitModel):
    status: Literal["ready"]
    origin: str
    destination: str
    distance_km: float
    summary: str
    routes: list[dict[str, Any]]
    weather_tips: list[str] = Field(default_factory=list)
    mode_advice: str = ""
    provider: str | None = None
    next_actions: list[str] = Field(default_factory=list)


class TripAdviceResult(ClarifiableResult):
    root: Annotated[
        _TripAdviceReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]
