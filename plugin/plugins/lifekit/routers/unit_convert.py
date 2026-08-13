"""单位换算 router — 纯计算，零依赖。"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._chat import push_lifekit_content
from .._coerce import finite_float
from .._contracts import UnitConvertParams, UnitConvertResult

# 换算表: (from_unit, to_unit) → (multiplier, from_label, to_label)
# value_to = value_from * multiplier
_CONVERSIONS: Dict[Tuple[str, str], Tuple[float, str, str]] = {
    # 长度
    ("cm", "inch"):    (0.393701, "厘米", "英寸"),
    ("inch", "cm"):    (2.54,     "英寸", "厘米"),
    ("m", "ft"):       (3.28084,  "米", "英尺"),
    ("ft", "m"):       (0.3048,   "英尺", "米"),
    ("km", "mile"):    (0.621371, "公里", "英里"),
    ("mile", "km"):    (1.60934,  "英里", "公里"),
    ("cm", "ft"):      (0.0328084, "厘米", "英尺"),
    ("ft", "cm"):      (30.48,    "英尺", "厘米"),
    # 重量
    ("kg", "lb"):      (2.20462,  "公斤", "磅"),
    ("lb", "kg"):      (0.453592, "磅", "公斤"),
    ("g", "oz"):       (0.035274, "克", "盎司"),
    ("oz", "g"):       (28.3495,  "盎司", "克"),
    ("kg", "oz"):      (35.274,   "公斤", "盎司"),
    ("oz", "kg"):      (0.0283495, "盎司", "公斤"),
    # 温度 (特殊处理)
    ("c", "f"):        (0, "°C", "°F"),
    ("f", "c"):        (0, "°F", "°C"),
    # 体积
    ("l", "gal"):      (0.264172, "升", "加仑"),
    ("gal", "l"):      (3.78541,  "加仑", "升"),
    ("ml", "oz_fl"):   (0.033814, "毫升", "液体盎司"),
    ("oz_fl", "ml"):   (29.5735,  "液体盎司", "毫升"),
    ("ml", "cup"):     (0.00422675, "毫升", "杯"),
    ("cup", "ml"):     (236.588,  "杯", "毫升"),
    ("ml", "tbsp"):    (0.067628, "毫升", "汤匙"),
    ("tbsp", "ml"):    (14.7868,  "汤匙", "毫升"),
    ("ml", "tsp"):     (0.202884, "毫升", "茶匙"),
    ("tsp", "ml"):     (4.92892,  "茶匙", "毫升"),
    # 面积
    ("sqm", "sqft"):   (10.7639,  "平方米", "平方英尺"),
    ("sqft", "sqm"):   (0.092903, "平方英尺", "平方米"),
    # 速度
    ("kmh", "mph"):    (0.621371, "km/h", "mph"),
    ("mph", "kmh"):    (1.60934,  "mph", "km/h"),
}

_UNIT_LABELS: Dict[str, str] = {
    "cm": "厘米",
    "inch": "英寸",
    "m": "米",
    "ft": "英尺",
    "km": "公里",
    "mile": "英里",
    "kg": "公斤",
    "lb": "磅",
    "g": "克",
    "oz": "盎司",
    "c": "°C",
    "f": "°F",
    "l": "升",
    "gal": "加仑",
    "ml": "毫升",
    "oz_fl": "液体盎司",
    "cup": "杯",
    "tbsp": "汤匙",
    "tsp": "茶匙",
    "sqm": "平方米",
    "sqft": "平方英尺",
    "kmh": "km/h",
    "mph": "mph",
}

# 单位别名 → 标准 key
_ALIASES: Dict[str, str] = {
    "厘米": "cm", "cm": "cm", "centimeter": "cm",
    "英寸": "inch", "inch": "inch", "in": "inch", "inches": "inch",
    "米": "m", "m": "m", "meter": "m", "meters": "m",
    "英尺": "ft", "ft": "ft", "feet": "ft", "foot": "ft",
    "公里": "km", "km": "km", "kilometer": "km",
    "英里": "mile", "mile": "mile", "miles": "mile", "mi": "mile",
    "公斤": "kg", "kg": "kg", "kilogram": "kg",
    "磅": "lb", "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "克": "g", "g": "g", "gram": "g", "grams": "g",
    "盎司": "oz", "oz": "oz", "ounce": "oz", "ounces": "oz",
    "摄氏": "c", "摄氏度": "c", "c": "c", "celsius": "c", "°c": "c",
    "华氏": "f", "华氏度": "f", "f": "f", "fahrenheit": "f", "°f": "f",
    "升": "l", "l": "l", "liter": "l", "litre": "l",
    "加仑": "gal", "gal": "gal", "gallon": "gal",
    "毫升": "ml", "ml": "ml",
    "液体盎司": "oz_fl", "fl oz": "oz_fl", "fl_oz": "oz_fl",
    "杯": "cup", "cup": "cup", "cups": "cup",
    "汤匙": "tbsp", "tbsp": "tbsp", "tablespoon": "tbsp",
    "茶匙": "tsp", "tsp": "tsp", "teaspoon": "tsp",
    "平方米": "sqm", "sqm": "sqm",
    "平方英尺": "sqft", "sqft": "sqft",
    "kmh": "kmh", "km/h": "kmh",
    "mph": "mph",
}


def _resolve_unit(raw: str) -> Optional[str]:
    return _ALIASES.get(raw.lower().strip())


def _convert(value: float, from_key: str, to_key: str) -> Optional[Tuple[float, str, str]]:
    """执行换算。返回 (result, from_label, to_label) 或 None。"""
    # 温度特殊处理
    if from_key == "c" and to_key == "f":
        return (value * 9 / 5 + 32, "°C", "°F")
    if from_key == "f" and to_key == "c":
        return ((value - 32) * 5 / 9, "°F", "°C")

    entry = _CONVERSIONS.get((from_key, to_key))
    if entry is None:
        return None
    mult, from_label, to_label = entry
    return (value * mult, from_label, to_label)


class UnitConvertRouter(PluginRouter):
    """unit_convert entry：单位换算。"""

    def __init__(self):
        super().__init__(name="unit_convert")

    @plugin_entry(
        id="unit_convert",
        name=tr("entries.unitConvert.name", default="Convert units"),
        description=tr("entries.unitConvert.description", default="Convert common length, weight, temperature, volume, area, and speed units."),
        params=UnitConvertParams,
        llm_result_model=UnitConvertResult,
    )
    @quick_action(icon="📐", priority=3)
    async def unit_convert(
        self,
        params: UnitConvertParams | None = None,
        value: float = 0,
        from_unit: str = "",
        to_unit: str = "",
        **_,
    ):
        if params is not None:
            value = params.value
            from_unit = params.from_unit
            to_unit = params.to_unit

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        fk = _resolve_unit(from_unit)
        tk = _resolve_unit(to_unit)

        if fk is None:
            return Err(SdkError(i18n.t("unit.unsupported", unit=from_unit)))
        if tk is None:
            return Err(SdkError(i18n.t("unit.unsupported", unit=to_unit)))

        numeric_value = finite_float(value)
        if numeric_value is None:
            return Err(SdkError(i18n.t("unit.invalid_value")))
        if fk == tk:
            label = _UNIT_LABELS.get(fk, fk)
            push_lifekit_content(self.main_plugin, [
                {
                    "type": "text",
                    "text": f"📐 {numeric_value} {label} → {numeric_value} {label}",
                },
            ])
            return Ok({
                "summary": i18n.t(
                    "unit.same", value=numeric_value, source=from_unit, target=to_unit,
                ),
                "conversion": {
                    "value": numeric_value,
                    "from_unit": label,
                    "result": numeric_value,
                    "to_unit": label,
                },
            })

        result = _convert(numeric_value, fk, tk)
        if result is None:
            return Err(SdkError(i18n.t(
                "unit.unsupported_pair", source=from_unit, target=to_unit,
            )))

        converted, fl, tl = result
        converted = round(converted, 4)

        summary = f"{numeric_value} {fl} = {converted} {tl}"

        push_lifekit_content(self.main_plugin, [
            {"type": "text", "text": f"📐 {numeric_value} {fl} → {converted} {tl}"},
        ])

        return Ok({
            "summary": summary,
            "conversion": {
                "value": numeric_value,
                "from_unit": fl,
                "result": converted,
                "to_unit": tl,
            },
        })
