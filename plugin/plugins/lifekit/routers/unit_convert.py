"""单位换算 router — 纯计算，零依赖。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._chat import push_lifekit_content

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
    mult, fl, tl = entry
    return (value * mult, fl, tl)


class UnitConvertRouter(PluginRouter):
    """unit_convert entry：单位换算。"""

    def __init__(self):
        super().__init__(name="unit_convert")

    @plugin_entry(
        id="unit_convert",
        name="单位换算",
        description=(
            "常用单位换算：长度(cm/inch/m/ft/km/mile)、重量(kg/lb/g/oz)、"
            "温度(°C/°F)、体积(ml/cup/tbsp/tsp/l/gal)、面积、速度。"
            "适合回答「180cm多少英尺」「30度是多少华氏度」「500g几盎司」。"
            "菜谱中的外国单位也可以用这个换算。"
        ),
        llm_result_fields=["summary", "conversion"],
        input_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "number",
                    "description": "要换算的数值",
                },
                "from_unit": {
                    "type": "string",
                    "description": "源单位（如 cm, kg, °C, cup, ml）",
                },
                "to_unit": {
                    "type": "string",
                    "description": "目标单位（如 inch, lb, °F, ml, g）",
                },
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    )
    @quick_action(icon="📐", priority=3)
    async def unit_convert(self, value: float = 0, from_unit: str = "", to_unit: str = "", **_):
        fk = _resolve_unit(from_unit)
        tk = _resolve_unit(to_unit)

        if fk is None:
            return Err(SdkError(f"不支持的单位「{from_unit}」"))
        if tk is None:
            return Err(SdkError(f"不支持的单位「{to_unit}」"))
        if fk == tk:
            return Ok({"summary": f"{value} {from_unit} = {value} {to_unit}（相同单位）", "conversion": {"value": value, "result": value}})

        result = _convert(float(value), fk, tk)
        if result is None:
            return Err(SdkError(f"不支持 {from_unit} → {to_unit} 的换算"))

        converted, fl, tl = result
        converted = round(converted, 4)

        summary = f"{value} {fl} = {converted} {tl}"

        push_lifekit_content(self.main_plugin, [
            {"type": "text", "text": f"📐 {value} {fl} → {converted} {tl}"},
        ])

        return Ok({
            "summary": summary,
            "conversion": {
                "value": value,
                "from_unit": fl,
                "result": converted,
                "to_unit": tl,
            },
        })
