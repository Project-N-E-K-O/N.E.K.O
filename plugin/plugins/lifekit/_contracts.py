"""Pydantic contracts for LifeKit entries."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, Field, RootModel, field_validator, model_validator

from ._nearby_intent import (
    MAX_PREFERENCE_HINTS,
    PlaceIntent,
    normalize_preference_hints,
)


def _blankable_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _desc(en: str, zh: str, ja: str) -> str:
    """Keep static schema descriptions useful in the required three locales."""
    return f"{en} / {zh} / {ja}"


class LifeKitModel(BaseModel):
    model_config = {"extra": "ignore"}


class LocationRiskFields(LifeKitModel):
    assumed: bool = False
    assumed_location: str = ""
    ambiguity_warning: str = ""


class ClarificationResult(LifeKitModel):
    status: Literal["clarify"]
    summary: str = Field(..., min_length=1)
    choices: list[str] = Field(default_factory=list)
    confirmation_token: str = ""
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary", mode="before")
    @classmethod
    def clarification_summary_must_not_be_blank(cls, value: Any) -> str:
        summary = _blankable_text(value)
        if not summary:
            raise ValueError("clarification summary must not be blank")
        return summary


class ProjectedResult(RootModel[Any]):
    """Root result that explicitly controls scalar projection to the host."""

    projected_statuses: ClassVar[tuple[str, ...]] = ()
    projected_model: ClassVar[type[BaseModel] | None] = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Expose the fields shared by every union branch to LLM projection."""
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"] = {
            "status": {
                "enum": list(cls.projected_statuses),
                "title": "Status",
                "type": "string",
            },
            "summary": {"title": "Summary", "type": "string"},
        }
        projected_properties: dict[str, Any] = {}
        if cls.projected_model is not None:
            model_properties = cls.projected_model.model_json_schema().get("properties", {})
            projected_properties = {
                name: definition
                for name, definition in model_properties.items()
                if name not in {
                    "status", "summary", "assumed", "assumed_location", "ambiguity_warning",
                }
            }
        schema["properties"].update(projected_properties)
        # The SDK uses ``required`` as its explicit projection allow-list. These
        # fields are not Pydantic validation requirements for every union arm;
        # they are the complete safe surface the host may pass back to the LLM.
        schema["required"] = ["status", "summary", *projected_properties]
        return schema

    @property
    def status(self) -> str:
        return str(self.root.status)

    @property
    def summary(self) -> str:
        return str(getattr(self.root, "summary", ""))

    @property
    def choices(self) -> list[str]:
        return list(getattr(self.root, "choices", []))


class ClarifiableResult(ProjectedResult):
    """Validated union of a complete ready result and a clarification result."""

    projected_statuses = ("ready", "clarify")

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        clarification_properties = ClarificationResult.model_json_schema().get(
            "properties", {}
        )
        schema["properties"].update({
            name: clarification_properties[name]
            for name in ("choices", "confirmation_token")
        })
        schema["required"] = [*schema["required"], "choices", "confirmation_token"]
        return schema


class ReadOnlyLocationResult(ProjectedResult):
    """Successful output from a location query that actually executed."""

    projected_statuses = ("ready",)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"].update(
            {
                "assumed": {"title": "Assumed", "type": "boolean"},
                "assumed_location": {
                    "title": "Assumed Location",
                    "type": "string",
                },
                "ambiguity_warning": {
                    "title": "Ambiguity Warning",
                    "type": "string",
                },
            }
        )
        schema["required"] = [
            "status",
            "summary",
            "assumed",
            "assumed_location",
            "ambiguity_warning",
            *(
                name
                for name in schema["properties"]
                if name not in {
                    "status", "summary", "assumed", "assumed_location", "ambiguity_warning",
                }
            ),
        ]
        return schema


class SavedLocationModel(LifeKitModel):
    id: str | None = None
    label: str
    city: str
    address: str = ""
    lat: float
    lon: float
    country: str = ""
    timezone: str = ""
    is_default: bool = False


class ListLocationsResult(LifeKitModel):
    count: int
    locations: list[SavedLocationModel]


class AddLocationParams(LifeKitModel):
    label: str = Field(..., min_length=1, description=_desc("Location label", "地点标签", "場所ラベル"))
    city: str = Field(..., min_length=1, description=_desc("City", "城市名", "都市名"))
    address: str = Field("", description=_desc("Optional address", "可选详细地址", "任意の住所"))
    set_default: bool = Field(False, description=_desc("Set as default", "设为默认地点", "既定に設定"))
    confirmed: bool = Field(
        False,
        description="Explicit confirmation / 明确确认 / 明示的な確認",
    )
    confirmation_token: str = Field(
        "",
        description="One-time token / 一次性确认令牌 / ワンタイム確認トークン",
    )

    @field_validator("label", "city", "address", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _AddLocationReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    message: str
    location: SavedLocationModel


class AddLocationResult(ClarifiableResult):
    projected_model = _AddLocationReadyResult
    root: Annotated[
        _AddLocationReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class LocationIdParams(LifeKitModel):
    location_id: str = Field(..., min_length=1, description=_desc("Location ID or label", "地点 ID 或标签", "場所 ID またはラベル"))
    confirmed: bool = Field(
        False,
        description="Explicit confirmation / 明确确认 / 明示的な確認",
    )
    confirmation_token: str = Field(
        "",
        description="One-time token / 一次性确认令牌 / ワンタイム確認トークン",
    )

    @field_validator("location_id", mode="before")
    @classmethod
    def _clean_location_id(cls, value: Any) -> str:
        return _blankable_text(value)


class _RemoveLocationReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    message: str
    remaining: int


class RemoveLocationResult(ClarifiableResult):
    projected_model = _RemoveLocationReadyResult
    root: Annotated[
        _RemoveLocationReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class _SetDefaultLocationReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    message: str


class SetDefaultLocationResult(ClarifiableResult):
    projected_model = _SetDefaultLocationReadyResult
    root: Annotated[
        _SetDefaultLocationReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


# Backward-compatible public name retained for existing host integrations.
MessageResult = SetDefaultLocationResult


class _UpdateConfigReadyResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    message: str
    config: dict[str, Any]


class UpdateConfigResult(ClarifiableResult):
    projected_model = _UpdateConfigReadyResult
    root: Annotated[
        _UpdateConfigReadyResult | ClarificationResult,
        Field(discriminator="status"),
    ]


class HourlyForecastParams(LifeKitModel):
    city: str = Field("", description=_desc("City; blank uses auto location", "城市；留空自动定位", "都市；空欄は自動位置情報"))
    hours: int = Field(48, ge=1, le=168, description=_desc("Forecast hours (1-168)", "预报小时数（1-168）", "予報時間（1-168）"))

    @field_validator("city", mode="before")
    @classmethod
    def _clean_city(cls, value: Any) -> str:
        return _blankable_text(value)


class _HourlyForecastReadyResult(LocationRiskFields):
    status: Literal["ready"]
    city: str
    summary: str
    hours: list[dict[str, Any]]
    total_hours: int


class HourlyForecastResult(ReadOnlyLocationResult):
    projected_model = _HourlyForecastReadyResult
    root: _HourlyForecastReadyResult


class NearbyParams(LifeKitModel):
    query: str = Field(..., min_length=1, description=_desc("Original nearby request; preserve full meaning", "附近搜索原话；保留完整语义", "周辺検索の原文；意味を保持"))
    location_hint: str = Field(
        "",
        description=_desc("Search center inferred from the request; blank if uncertain", "从原话识别的搜索中心；不确定留空", "原文から推定した検索中心；不明なら空欄"),
    )
    place_intent: PlaceIntent = Field(
        "explore",
        description=_desc("Closest place intent; use explore if uncertain", "最接近需求的地点意图；不确定用 explore", "最も近い場所意図；不明なら explore"),
    )
    preference_hints: list[str] = Field(
        default_factory=list,
        max_length=MAX_PREFERENCE_HINTS,
        description=_desc(
            "Up to four explicit short preferences; do not invent map search terms",
            "最多四个明确偏好；不要编造地图召回词",
            "明示された好みを最大4件；地図検索語を作らない",
        ),
    )
    radius: int = Field(3000, ge=500, le=50000, description=_desc("Search radius in meters", "搜索半径（米）", "検索半径（メートル）"))

    @model_validator(mode="before")
    @classmethod
    def _accept_request_alias(cls, value: Any) -> Any:
        if isinstance(value, dict) and "query" not in value and "request" in value:
            return {**value, "query": value["request"]}
        return value

    @property
    def request(self) -> str:
        """Compatibility accessor for the clearer internal request wording."""
        return self.query

    @field_validator("query", "location_hint", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)

    @field_validator("preference_hints", mode="before")
    @classmethod
    def _clean_preference_hints(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("preference_hints must be a list")
        return list(normalize_preference_hints(value))


class _NearbyReadyResult(LocationRiskFields):
    status: Literal["ready"]
    summary: str
    request: str
    searched_terms: list[str]
    results: list[dict[str, Any]]
    count: int
    provider: str | None = None
    weather_tip: str = ""
    location_groups: list[dict[str, Any]] = Field(default_factory=list)
    suggestion: str = ""


class NearbyResult(ReadOnlyLocationResult):
    root: _NearbyReadyResult

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"].update({
            "status": {
                "enum": ["ready"],
                "title": "Status",
                "type": "string",
            },
            "request": {"title": "Request", "type": "string"},
            "searched_terms": {
                "items": {"type": "string"},
                "title": "Searched Terms",
                "type": "array",
            },
            "results": {
                "items": {"type": "object"},
                "title": "Results",
                "type": "array",
            },
            "location_groups": {
                "items": {"type": "object"},
                "title": "Location Groups",
                "type": "array",
            },
        })
        schema["required"] = [
            "status",
            "summary",
            "assumed",
            "assumed_location",
            "ambiguity_warning",
            "request",
            "searched_terms",
            "results",
            "location_groups",
        ]
        return schema


class FoodRecommendParams(LifeKitModel):
    cuisine: str = Field("", description=_desc("Cuisine preference; blank searches restaurants and uses weather only as advice", "口味偏好；留空搜索餐厅，天气仅用于提示", "料理の好み；空欄はレストランを検索し、天気は補足にのみ使用"))
    scene: str = Field("", description=_desc("Dining occasion", "用餐场景", "食事の場面"))
    location: str = Field("", description=_desc("Location label or city; blank uses default", "地点标签或城市；留空用默认", "場所ラベルまたは都市；空欄は既定"))
    radius: int = Field(3000, ge=500, le=50000, description=_desc("Search radius in meters", "搜索半径（米）", "検索半径（メートル）"))

    @field_validator("cuisine", "scene", "location", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _FoodRecommendReadyResult(LocationRiskFields):
    status: Literal["ready"]
    summary: str
    recommendations: list[dict[str, Any]]
    query: str
    weather_reason: str = ""
    provider: str | None = None
    next_actions: list[str] = Field(default_factory=list)


class FoodRecommendResult(ReadOnlyLocationResult):
    projected_model = _FoodRecommendReadyResult
    root: _FoodRecommendReadyResult


class UnitConvertParams(LifeKitModel):
    value: float = Field(..., description=_desc("Value to convert", "要换算的数值", "変換する値"))
    from_unit: str = Field(..., min_length=1, description=_desc("Source unit", "源单位", "変換元の単位"))
    to_unit: str = Field(..., min_length=1, description=_desc("Target unit", "目标单位", "変換先の単位"))

    @field_validator("from_unit", "to_unit", mode="before")
    @classmethod
    def _clean_unit(cls, value: Any) -> str:
        return _blankable_text(value)


class UnitConvertResult(LifeKitModel):
    summary: str
    conversion: dict[str, Any]


class CityParams(LifeKitModel):
    city: str = Field("", description=_desc("City; blank uses automatic or default location", "城市；留空用自动或默认地点", "都市；空欄は自動または既定の場所"))

    @field_validator("city", mode="before")
    @classmethod
    def _clean_city(cls, value: Any) -> str:
        return _blankable_text(value)


class _GetWeatherReadyResult(LocationRiskFields):
    status: Literal["ready"]
    city: str
    summary: str
    current: dict[str, Any]
    forecast: list[dict[str, Any]]
    timezone_mismatch: bool = False
    vpn_detected: bool = False
    next_actions: list[str] = Field(default_factory=list)


class GetWeatherResult(ReadOnlyLocationResult):
    projected_model = _GetWeatherReadyResult
    root: _GetWeatherReadyResult


class _AirQualityReadyResult(LocationRiskFields):
    status: Literal["ready"]
    city: str
    summary: str
    aqi: dict[str, Any]
    advice: list[str]
    next_actions: list[str] = Field(default_factory=list)


class AirQualityResult(ReadOnlyLocationResult):
    projected_model = _AirQualityReadyResult
    root: _AirQualityReadyResult


class _TravelAdviceReadyResult(LocationRiskFields):
    status: Literal["ready"]
    city: str
    summary: str
    tips: list[str]
    clothing: str = ""
    umbrella: bool = False
    sunscreen: bool = False
    next_actions: list[str] = Field(default_factory=list)


class TravelAdviceResult(ReadOnlyLocationResult):
    projected_model = _TravelAdviceReadyResult
    root: _TravelAdviceReadyResult


class CurrencyConvertParams(LifeKitModel):
    amount: float = Field(1, description=_desc("Amount", "金额", "金額"))
    from_currency: str = Field(..., min_length=1, description=_desc("Source currency code", "源货币代码", "変換元通貨コード"))
    to_currency: str = Field(..., min_length=1, description=_desc("Target currency code", "目标货币代码", "変換先通貨コード"))

    @field_validator("from_currency", "to_currency", mode="before")
    @classmethod
    def _clean_currency(cls, value: Any) -> str:
        return _blankable_text(value).upper()


class CurrencyConvertResult(LifeKitModel):
    summary: str
    conversion: dict[str, Any]
    next_actions: list[str] = Field(default_factory=list)


class CountdownParams(LifeKitModel):
    target_date: str = Field(..., min_length=1, description=_desc("Target date, month-day, or holiday", "目标日期、月日或节日", "対象日、月日、祝日"))
    label: str = Field("", description=_desc("Optional event label", "可选事件名称", "任意のイベント名"))
    country_hint: str = Field("", description=_desc("Country code used to interpret regional holiday names", "用于解释地域节日名称的国家代码", "地域の祝日名を解釈する国コード"))

    @field_validator("target_date", "label", "country_hint", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class DaysBetweenParams(LifeKitModel):
    start_date: str = Field("", description=_desc("Start date; blank means today", "起始日期；留空为今天", "開始日；空欄は今日"))
    end_date: str = Field("", description=_desc("End date; blank means today", "结束日期；留空为今天", "終了日；空欄は今日"))
    country_hint: str = Field("", description=_desc("Country code used to interpret regional holiday names", "用于解释地域节日名称的国家代码", "地域の祝日名を解釈する国コード"))

    @field_validator("start_date", "end_date", "country_hint", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class DateDetailResult(LifeKitModel):
    summary: str
    detail: dict[str, Any]


class SearchRecipeParams(LifeKitModel):
    query: str = Field(..., min_length=1, description=_desc("Dish or ingredient", "菜名或食材", "料理名または食材"))
    by_ingredient: bool = Field(False, description=_desc("Search by ingredient", "按食材搜索", "食材で検索"))

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value: Any) -> str:
        return _blankable_text(value)


class SearchRecipeResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    recipes: list[dict[str, Any]]
    query: str = ""
    count: int = 0
    next_actions: list[str] = Field(default_factory=list)


class RandomRecipeResult(LifeKitModel):
    status: Literal["ready"]
    summary: str
    recipe: dict[str, Any] | None = None
    next_actions: list[str] = Field(default_factory=list)


class TripAdviceParams(LifeKitModel):
    origin: str = Field("", description=_desc("Origin label or city; blank uses default", "起点标签或城市；留空用默认", "出発地ラベルまたは都市；空欄は既定"))
    destination: str = Field(..., min_length=1, description=_desc("Destination label or city", "终点标签或城市", "目的地ラベルまたは都市"))
    mode: str = Field("", description=_desc("Travel mode; blank selects automatically", "出行方式；留空自动选择", "移動手段；空欄は自動選択"))

    @field_validator("origin", "destination", "mode", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return _blankable_text(value)


class _TripAdviceReadyResult(LocationRiskFields):
    status: Literal["ready"]
    origin: str
    destination: str
    distance_km: float
    summary: str
    routes: list[dict[str, Any]]
    weather_tips: list[str] = Field(default_factory=list)
    mode_advice: str = ""
    requested_mode: str = ""
    selected_mode: str = "auto"
    mode_assumption: str = ""
    provider: str | None = None
    next_actions: list[str] = Field(default_factory=list)


class TripAdviceResult(ReadOnlyLocationResult):
    projected_model = _TripAdviceReadyResult
    root: _TripAdviceReadyResult
