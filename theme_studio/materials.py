"""高级材质设置系统 —— 版本化 schema、预设、校验与 CSS 变量编译。

设计（与 ``THEME_CONTRACT.md`` 的变量层并列，不取代它）：

* 材质是 ``theme.json`` 的 ``materials`` 子树：version / enabled / preset /
  quality / global / lighting / refraction / surface / animation / dark /
  components / custom_presets。
* 后端只做三件事：**规整**（缺失字段补默认、未知字段丢弃）、**校验**
  （类型/范围钳制并回传警告）、**编译**（把材质参数换算成 --nts-mt-* 变量，
  亮/暗各一份，随 get_theme 返回给加载器）。
* 加载器拿编译结果直接写 <style>，MATERIAL_CSS 常量消费这些变量。因此
  enabled=false 时加载器既不写变量也不打 data-nts-mt 属性，材质 CSS 整段
  惰性 —— 「关闭材质 = 零效果」是构造性保证，不靠回退值。

参数分五节（面板的 10 个导航按这五节 + 总览/组件/性能/预设/高级拆分）。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

MATERIAL_VERSION = 1

QUALITY_MODES = ("saver", "balanced", "high", "ultra")

# 编译产物版本：新增 / 重命名派生变量时必须 +1。theme.json 里的镜像靠它判过期 ——
# 只看 loader_version / css_sha8 的话，编译器升级而加载器没变时镜像永远不重算（0.15.12 踩过）。
COMPILE_VERSION = "1.6.20"
QUALITY_LABELS = {
    "saver": "省电",
    "balanced": "均衡",
    "high": "高品质",
    "ultra": "极致",
}

PRESET_IDS = ("clear", "frosted", "liquid", "smoke", "soft", "prism", "custom")
PRESET_LABELS = {
    "clear": "Clear Glass",
    "frosted": "Frosted Glass",
    "liquid": "Liquid Glass (Apple)",
    "smoke": "Smoke Glass",
    "soft": "Soft Glass",
    "prism": "Prism Glass",
    "custom": "Custom",
}

SECTIONS = ("global", "lighting", "refraction", "surface", "animation")
SECTION_LABELS = {
    "global": "基础材质",
    "lighting": "光线与反射",
    "refraction": "折射与液态",
    "surface": "表面细节",
    "animation": "动画与交互",
}

COMPONENTS = (
    "chat",
    "bubble",
    "input",
    "sidebar",
    "topbar",
    "settings",
    "dialog",
    "button",
    "plugin",
    "menu",
)
COMPONENT_LABELS = {
    "chat": "主聊天面板",
    "bubble": "聊天气泡",
    "input": "输入框",
    "sidebar": "消息区",
    "topbar": "顶部工具栏",
    "settings": "设置面板",
    "dialog": "弹窗",
    "button": "按钮",
    "plugin": "插件卡片",
    "menu": "下拉菜单",
}

#: 组件可独立覆盖的参数（继承全局时不产出变量）。
COMPONENT_PARAMS = ("opacity", "blur", "radius", "borderWidth", "shadowStrength")

EASINGS = ("ease", "linear", "ease-in", "ease-out", "ease-in-out")

BLUR_LEVELS = {"low": 1, "medium": 2, "high": 3, "ultra": 4}


@dataclass(frozen=True)
class Param:
    """一个材质参数的完整定义（后端校验、编译器与面板表单共用同一口径）。"""

    key: str
    section: str
    kind: str  # number | px | percent | deg | ms | color | bool | enum
    default: Any
    minimum: float = 0.0
    maximum: float = 100.0
    choices: tuple[str, ...] = ()
    label: str = ""


_RAW_PARAMS: tuple[Param, ...] = (
    # ---------------------------------------------------------------- 基础材质
    Param("opacity", "global", "number", 0.58, 0.0, 1.0, label="玻璃透明度（越低越透）"),
    Param("tint", "global", "color", "#ffffff", label="玻璃底色"),
    Param("tintStrength", "global", "number", 0.35, 0.0, 1.0, label="背景染色强度"),
    Param("blur", "global", "px", 22.0, 0.0, 60.0, label="模糊半径"),
    Param("blurLevel", "global", "enum", "medium", choices=("low", "medium", "high", "ultra"), label="模糊层级"),
    Param("saturation", "global", "percent", 165.0, 0.0, 300.0, label="背景饱和度"),
    Param("brightness", "global", "percent", 105.0, 0.0, 200.0, label="背景亮度"),
    Param("dyePrimary", "global", "color", "#7dd3fc", label="染色主色"),
    Param("dyeSecondary", "global", "color", "#a78bfa", label="染色辅色"),
    Param("dyeStrength", "global", "number", 0.28, 0.0, 1.0, label="色彩浓度"),
    Param("dyeWarmth", "global", "number", 0.0, -100.0, 100.0, label="冷暖倾向"),
    Param("dyeAlpha", "global", "number", 0.45, 0.0, 1.0, label="颜色透明度"),
    Param("dyeGradient", "global", "number", 0.4, 0.0, 1.0, label="渐变染色强度"),
    # ---------------------------------------------------------------- 边缘光
    Param("edgeIntensity", "lighting", "number", 0.4, 0.0, 1.0, label="边缘光强度"),
    Param("edgeWidth", "lighting", "px", 1.0, 0.0, 8.0, label="边缘光宽度"),
    Param("edgeAlpha", "lighting", "number", 0.35, 0.0, 1.0, label="边缘光透明度"),
    Param("edgeColor", "lighting", "color", "#ffffff", label="边缘光颜色"),
    Param("edgeTop", "lighting", "number", 0.9, 0.0, 1.0, label="上缘高光（镜面亮线）"),
    Param("edgeBottom", "lighting", "number", 0.2, 0.0, 1.0, label="下缘反光"),
    Param("edgeLeft", "lighting", "number", 0.25, 0.0, 1.0, label="左缘亮度"),
    Param("edgeRight", "lighting", "number", 0.25, 0.0, 1.0, label="右缘亮度"),
    Param("edgeFade", "lighting", "percent", 70.0, 0.0, 100.0, label="边缘渐隐"),
    # ---------------------------------------------------------------- 内部高光
    Param("innerIntensity", "lighting", "number", 0.35, 0.0, 1.0, label="内部高光强度"),
    Param("innerAlpha", "lighting", "number", 0.3, 0.0, 1.0, label="内部高光透明度"),
    Param("innerRange", "lighting", "percent", 55.0, 0.0, 100.0, label="内部高光范围"),
    Param("innerPos", "lighting", "percent", 30.0, 0.0, 100.0, label="内部高光位置"),
    Param("innerSoftness", "lighting", "percent", 60.0, 0.0, 100.0, label="内部高光柔和度"),
    Param("innerDouble", "lighting", "bool", False, label="双层内部高光"),
    # ---------------------------------------------------------------- 环境反射
    Param("ambientIntensity", "lighting", "number", 0.25, 0.0, 1.0, label="环境反射强度"),
    Param("ambientColor", "lighting", "color", "#cfe8ff", label="环境反射颜色"),
    Param("ambientDirection", "lighting", "deg", 135.0, 0.0, 360.0, label="环境反射方向"),
    Param("ambientRange", "lighting", "percent", 60.0, 0.0, 100.0, label="环境反射范围"),
    Param("ambientBlur", "lighting", "px", 24.0, 0.0, 60.0, label="环境反射模糊"),
    Param("ambientAlpha", "lighting", "number", 0.25, 0.0, 1.0, label="环境反射透明度"),
    # ---------------------------------------------------------------- 动态光照
    Param("dynamic", "lighting", "bool", True, label="动态光照"),
    Param("dynamicSpeed", "lighting", "number", 6.0, 0.5, 30.0, label="动态光照速度"),
    Param("dynamicIntensity", "lighting", "number", 0.3, 0.0, 1.0, label="动态光照强度"),
    Param("dynamicRange", "lighting", "percent", 45.0, 0.0, 100.0, label="动态光照范围"),
    Param("mouseFollow", "lighting", "number", 0.5, 0.0, 1.0, label="鼠标跟随强度"),
    Param("mouseDelay", "lighting", "ms", 120.0, 0.0, 800.0, label="鼠标跟随延迟"),
    Param("dynamicReflect", "lighting", "bool", True, label="动态反射"),
    # ---------------------------------------------------------------- 折射
    Param("refractStrength", "refraction", "number", 0.2, 0.0, 1.0, label="折射强度"),
    Param("refractRange", "refraction", "percent", 40.0, 0.0, 100.0, label="折射范围"),
    Param("refractEdge", "refraction", "px", 6.0, 0.0, 20.0, label="折射边缘"),
    Param("refractBlur", "refraction", "px", 18.0, 0.0, 60.0, label="折射模糊"),
    Param("refractAlpha", "refraction", "number", 0.3, 0.0, 1.0, label="折射透明度"),
    Param("refractLocal", "refraction", "number", 0.5, 0.0, 1.0, label="局部折射强度"),
    # ---------------------------------------------------------------- 色散
    Param("dispStrength", "refraction", "number", 0.12, 0.0, 1.0, label="色散强度"),
    Param("dispRange", "refraction", "percent", 30.0, 0.0, 100.0, label="色散范围"),
    Param("dispAlpha", "refraction", "number", 0.25, 0.0, 1.0, label="色散透明度"),
    Param("dispShift", "refraction", "px", 4.0, 0.0, 30.0, label="色散色彩偏移"),
    Param("dispWidth", "refraction", "px", 2.0, 0.0, 10.0, label="色散边缘宽度"),
    # ---------------------------------------------------------------- 液态动态
    Param("liquid", "refraction", "bool", True, label="液态动态"),
    Param("liquidAmp", "refraction", "px", 4.0, 0.0, 24.0, label="形变幅度"),
    Param("liquidSpeed", "refraction", "number", 9.0, 2.0, 30.0, label="形变速度"),
    Param("liquidRange", "refraction", "percent", 50.0, 0.0, 100.0, label="波动范围"),
    Param("liquidSmooth", "refraction", "percent", 70.0, 0.0, 100.0, label="平滑度"),
    Param("liquidStrength", "refraction", "number", 0.35, 0.0, 1.0, label="液态强度"),
    # ---------------------------------------------------------------- 纹理
    Param("noise", "surface", "bool", False, label="噪点纹理"),
    Param("noiseStrength", "surface", "number", 0.18, 0.0, 1.0, label="噪点强度"),
    Param("noiseSize", "surface", "px", 3.0, 1.0, 24.0, label="噪点尺寸"),
    Param("noiseAlpha", "surface", "number", 0.2, 0.0, 1.0, label="噪点透明度"),
    Param("noiseDensity", "surface", "percent", 50.0, 0.0, 100.0, label="噪点密度"),
    # ---------------------------------------------------------------- 圆角
    Param("radius", "surface", "px", 20.0, 0.0, 40.0, label="全局圆角"),
    Param("radiusCard", "surface", "px", 20.0, 0.0, 40.0, label="卡片圆角"),
    Param("radiusButton", "surface", "px", 13.0, 0.0, 40.0, label="按钮圆角"),
    Param("radiusDialog", "surface", "px", 24.0, 0.0, 40.0, label="弹窗圆角"),
    Param("radiusInput", "surface", "px", 16.0, 0.0, 40.0, label="输入框圆角"),
    Param("borderWidth", "surface", "px", 1.0, 0.0, 8.0, label="边框宽度"),
    Param("borderAlpha", "surface", "number", 0.42, 0.0, 1.0, label="边框透明度"),
    # ---------------------------------------------------------------- 阴影
    Param("shadowStrength", "surface", "number", 0.35, 0.0, 1.0, label="外阴影强度"),
    Param("shadowSpread", "surface", "px", 0.0, 0.0, 40.0, label="外阴影扩散"),
    Param("shadowDistance", "surface", "px", 8.0, 0.0, 40.0, label="外阴影距离"),
    Param("shadowBlur", "surface", "px", 24.0, 0.0, 60.0, label="外阴影模糊"),
    Param("innerShadow", "surface", "bool", False, label="内阴影"),
    Param("innerShadowStrength", "surface", "number", 0.25, 0.0, 1.0, label="内阴影强度"),
    # ---------------------------------------------------------------- 动画
    Param("animation", "animation", "bool", True, label="动画总开关"),
    Param("enterDuration", "animation", "ms", 260.0, 0.0, 2000.0, label="进入动画时长"),
    Param("exitDuration", "animation", "ms", 180.0, 0.0, 2000.0, label="退出动画时长"),
    Param("hoverDuration", "animation", "ms", 140.0, 0.0, 2000.0, label="悬停动画时长"),
    Param("pressDuration", "animation", "ms", 90.0, 0.0, 2000.0, label="点击动画时长"),
    Param("expandDuration", "animation", "ms", 220.0, 0.0, 2000.0, label="展开收起时长"),
    Param("easing", "animation", "enum", "ease-out", choices=EASINGS, label="缓动曲线"),
    Param("hover", "animation", "number", 0.4, 0.0, 1.0, label="悬停高光"),
    Param("press", "animation", "number", 0.6, 0.0, 1.0, label="点击下沉"),
    # ---------------------------------------------------------------- 鼠标交互
    Param("spotlight", "animation", "bool", True, label="鼠标追光"),
    Param("spotRadius", "animation", "px", 240.0, 0.0, 600.0, label="光斑半径"),
    Param("spotIntensity", "animation", "number", 0.35, 0.0, 1.0, label="光斑强度"),
    Param("spotSpeed", "animation", "percent", 60.0, 0.0, 100.0, label="跟随速度"),
    Param("reducedMotion", "animation", "bool", False, label="减少动态效果"),
)

PARAMS: tuple[Param, ...] = _RAW_PARAMS
PARAM_BY_KEY: dict[str, Param] = {p.key: p for p in PARAMS}
PARAMS_BY_SECTION: dict[str, tuple[Param, ...]] = {
    section: tuple(p for p in PARAMS if p.section == section) for section in SECTIONS
}

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
    r"(?:\s*,\s*([0-9.]+)\s*)?\)$",
    re.IGNORECASE,
)
_KEYWORD_COLORS = {"transparent", "white", "black"}


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def var_name(param: Param) -> str:
    """材质参数 → CSS 变量名（opacity → --nts-mt-opacity）。"""
    return "--nts-mt-" + _kebab(param.key)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# 三个锚点：ease（起步缓）→ ease-in-out（对称）→ linear（匀速）。
_EASE_LOW = (0.25, 0.1, 0.25, 1.0)
_EASE_MID = (0.42, 0.0, 0.58, 1.0)
_EASE_TOP = (0.0, 0.0, 1.0, 1.0)


def _ease_curve(smooth: float) -> str:
    """把 0-1 的平滑度映射成连续的 cubic-bezier。

    0 端最像 ease，0.5 是对称的 ease-in-out，1 是 linear。
    返回 animation 简写里可以直出、不需要额外变量的字符串。
    """
    t = _clamp(float(smooth), 0.0, 1.0)
    if t <= 0.5:
        one, two, part = _EASE_LOW, _EASE_MID, t / 0.5
    else:
        one, two, part = _EASE_MID, _EASE_TOP, (t - 0.5) / 0.5
    pts = [round(one[i] + (two[i] - one[i]) * part, 3) for i in range(4)]
    return "cubic-bezier(" + ", ".join(f"{v:g}" for v in pts) + ")"


def _coerce_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "on", "yes"):
            return True
        if lowered in ("false", "0", "off", "no"):
            return False
    return None


def _parse_color(raw: Any) -> tuple[int, int, int, float] | None:
    """把 #rgb / #rrggbb / rgb() / rgba() 解析成 RGBA 元组。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if _HEX_RE.match(text):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(ch * 2 for ch in body)
        r, g, b = (int(body[i : i + 2], 16) for i in (0, 2, 4))
        alpha = int(body[6:8], 16) / 255 if len(body) == 8 else 1.0
        return r, g, b, alpha
    match = _RGB_RE.match(text)
    if match:
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if any(v > 255 for v in (r, g, b)):
            return None
        alpha = float(match.group(4)) if match.group(4) is not None else 1.0
        return r, g, b, _clamp(alpha, 0.0, 1.0)
    return None


def _rgba(rgb: tuple[int, int, int, float], alpha: float) -> str:
    r, g, b, base_alpha = rgb
    return f"rgba({r}, {g}, {b}, {round(_clamp(alpha * base_alpha, 0.0, 1.0), 3)})"


def _mix_rgb(one: tuple[int, int, int, float], two: tuple[int, int, int, float], ratio: float) -> tuple[int, int, int, float]:
    """把 one 按 ratio（0-1）混向 two，返回 RGBA 元组。染料冷暖倾向用。"""
    t = _clamp(float(ratio), 0.0, 1.0)
    return (
        int(round(one[0] * (1.0 - t) + two[0] * t)),
        int(round(one[1] * (1.0 - t) + two[1] * t)),
        int(round(one[2] * (1.0 - t) + two[2] * t)),
        1.0,
    )


def _coerce_param(param: Param, value: Any) -> tuple[bool, Any]:
    """单个参数值 → (是否合法, 规整后的值)。"""
    if param.kind == "bool":
        coerced = _coerce_bool(value)
        return (coerced is not None, coerced)
    if param.kind == "enum":
        text = str(value).strip().lower()
        return (text in param.choices, text)
    if param.kind == "color":
        text = str(value).strip()
        if _parse_color(text) is not None or text.lower() in _KEYWORD_COLORS:
            return (True, text)
        return (False, None)
    if isinstance(value, bool) or value is None:
        return (False, None)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return (False, None)
    if number != number or number in (float("inf"), float("-inf")):
        return (False, None)
    return (True, round(_clamp(number, param.minimum, param.maximum), 4))


def _css_value(param: Param, value: Any) -> str:
    """把参数值编译成 CSS 字面量（不含变量名）。"""
    kind = param.kind
    if kind == "bool":
        return "1" if value else "0"
    if kind == "enum":
        if param.key == "blurLevel":
            return str(BLUR_LEVELS.get(str(value), 2))
        return str(value)
    if kind == "number":
        return f"{round(float(value), 3):g}"
    if kind == "px":
        return f"{round(float(value), 2):g}px"
    if kind == "percent":
        return f"{round(float(value), 2):g}%"
    if kind == "deg":
        return f"{round(float(value), 2):g}deg"
    if kind == "ms":
        return f"{round(float(value), 0):g}ms"
    if kind == "color":
        rgb = _parse_color(value)
        if rgb is None:
            return "transparent"
        return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {round(rgb[3], 3)})"
    return str(value)


def _section_defaults(section: str) -> dict[str, Any]:
    return {p.key: p.default for p in PARAMS_BY_SECTION[section]}


def _blank_params() -> dict[str, dict[str, Any]]:
    return {section: _section_defaults(section) for section in SECTIONS}


def _clean_section(raw: Any, section: str, warnings: list[str], prefix: str) -> dict[str, Any]:
    """一节参数 → 合法值；非法值回退该参数默认值并记警告。"""
    out = _section_defaults(section)
    if not isinstance(raw, dict):
        return out
    for param in PARAMS_BY_SECTION[section]:
        if param.key not in raw:
            continue
        ok, value = _coerce_param(param, raw[param.key])
        if not ok:
            warnings.append(f"{prefix}.{param.key}: 非法值 {raw[param.key]!r}，已回退默认")
            continue
        if param.kind in ("number", "px", "percent", "deg", "ms") and float(value) != float(raw[param.key]):
            warnings.append(
                f"{prefix}.{param.key}: {raw[param.key]!r} 超出范围 "
                f"[{param.minimum:g}, {param.maximum:g}]，已钳制为 {value:g}"
            )
        out[param.key] = value
    return out


def _preset(**overrides: Any) -> dict[str, dict[str, Any]]:
    """用默认值铺满五节再覆盖 —— 保证每套预设都是**完整**配置，不是只改一个颜色。"""
    params = _blank_params()
    for key, value in overrides.items():
        param = PARAM_BY_KEY.get(key)
        if param is None:
            continue
        params[param.section][key] = value
    return params


PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "clear": _preset(
        opacity=0.38, tintStrength=0.18, blur=12, blurLevel="low", saturation=120, brightness=108,
        dyeStrength=0.12, dyeAlpha=0.25, dyeGradient=0.2,
        edgeIntensity=0.3, edgeAlpha=0.22, edgeWidth=1, edgeTop=0.5, edgeBottom=0.12,
        innerIntensity=0.22, innerAlpha=0.16, ambientIntensity=0.12,
        refractStrength=0.08, dispStrength=0.03, liquidStrength=0.12,
        noise=False, borderAlpha=0.22, shadowStrength=0.18, shadowBlur=18,
        dynamic=True, dynamicIntensity=0.18, mouseFollow=0.35, spotlight=True, spotIntensity=0.22,
    ),
    "frosted": _preset(
        opacity=0.72, tintStrength=0.35, blur=18, blurLevel="medium", saturation=140, brightness=105,
        dyeStrength=0.28, dyeAlpha=0.45, dyeGradient=0.4,
        edgeIntensity=0.4, edgeAlpha=0.35, edgeWidth=1, edgeTop=0.6, edgeBottom=0.2,
        innerIntensity=0.35, innerAlpha=0.3, ambientIntensity=0.25,
        refractStrength=0.2, dispStrength=0.12, liquidStrength=0.35,
        noise=False, borderAlpha=0.35, shadowStrength=0.35, shadowBlur=24,
        dynamic=True, dynamicIntensity=0.3, mouseFollow=0.5, spotlight=True, spotIntensity=0.35,
    ),
    "liquid": _preset(
        # Apple Liquid Glass 观感：低不透明度 + 高饱和背板模糊 + 极强顶部镜面亮线
        # + 明显内发光与底部折射收边 + 大圆角。每一项都对应 v1.6.1 光学层里的
        # 一个派生变量，所以面板上拖这些滑块时应用窗口里真的会变。
        opacity=0.34, tintStrength=0.5, blur=30, blurLevel="high", saturation=200, brightness=108,
        dyeStrength=0.55, dyeAlpha=0.5, dyeGradient=0.85,
        dyePrimary="#bfe8ff", dyeSecondary="#d8c8ff",
        edgeIntensity=0.9, edgeAlpha=0.62, edgeWidth=1, edgeTop=1.0, edgeBottom=0.22,
        edgeLeft=0.3, edgeRight=0.3, edgeFade=82,
        innerIntensity=0.6, innerAlpha=0.6, innerRange=62, innerPos=22, innerSoftness=78, innerDouble=True,
        ambientIntensity=0.45, ambientColor="#dfefff", ambientDirection=150,
        ambientRange=72, ambientBlur=34, ambientAlpha=0.4,
        dynamic=True, dynamicSpeed=4, dynamicIntensity=0.5, dynamicRange=60,
        mouseFollow=0.8, mouseDelay=90,
        refractStrength=0.55, refractRange=62, refractEdge=12, refractBlur=26,
        refractAlpha=0.5, refractLocal=0.75,
        dispStrength=0.22, dispRange=42, dispAlpha=0.32, dispShift=3, dispWidth=1.5,
        liquid=True, liquidAmp=9, liquidSpeed=5, liquidRange=78, liquidSmooth=85, liquidStrength=0.7,
        radius=26, radiusCard=24, radiusButton=16, radiusDialog=30, radiusInput=20,
        borderWidth=1, borderAlpha=0.55, shadowStrength=0.55, shadowSpread=2,
        shadowDistance=14, shadowBlur=44,
        innerShadow=True, innerShadowStrength=0.2,
        enterDuration=300, exitDuration=220, hoverDuration=160, pressDuration=110, expandDuration=260,
        hover=0.55, press=0.75, spotRadius=300, spotIntensity=0.5, spotSpeed=72,
    ),
    "smoke": _preset(
        opacity=0.68, tintStrength=0.55, blur=26, blurLevel="high", saturation=70, brightness=82,
        dyeStrength=0.22, dyeAlpha=0.35, dyeGradient=0.3,
        dyePrimary="#94a3b8", dyeSecondary="#64748b",
        edgeIntensity=0.2, edgeAlpha=0.18, edgeWidth=1, edgeTop=0.3, edgeBottom=0.1,
        innerIntensity=0.18, innerAlpha=0.14, ambientIntensity=0.12,
        refractStrength=0.1, dispStrength=0.02, liquidStrength=0.15,
        noise=True, noiseStrength=0.3, noiseAlpha=0.24, noiseSize=2, noiseDensity=65,
        borderAlpha=0.25, shadowStrength=0.42, shadowDistance=10, shadowBlur=30,
        dynamic=True, dynamicIntensity=0.16, dynamicSpeed=10, mouseFollow=0.3, spotlight=True,
    ),
    "soft": _preset(
        opacity=0.8, tintStrength=0.42, blur=16, blurLevel="medium", saturation=125, brightness=108,
        dyeStrength=0.2, dyeAlpha=0.3, dyeGradient=0.28,
        dyePrimary="#fbcfe8", dyeSecondary="#fde68a",
        edgeIntensity=0.28, edgeAlpha=0.24, edgeWidth=1,
        innerIntensity=0.45, innerAlpha=0.34, innerRange=75, innerSoftness=85, innerDouble=True,
        ambientIntensity=0.22, ambientAlpha=0.2,
        refractStrength=0.12, dispStrength=0.04, liquidStrength=0.2,
        noise=False, radius=18, radiusCard=18, radiusButton=12, radiusDialog=20, radiusInput=14,
        borderAlpha=0.3, shadowStrength=0.28, shadowBlur=26, shadowDistance=6,
        dynamic=True, dynamicIntensity=0.2, mouseFollow=0.4, spotIntensity=0.28,
    ),
    "prism": _preset(
        opacity=0.52, tintStrength=0.3, blur=20, blurLevel="ultra", saturation=200, brightness=104,
        dyeStrength=0.6, dyeAlpha=0.55, dyeGradient=0.8,
        dyePrimary="#f472b6", dyeSecondary="#38bdf8",
        edgeIntensity=0.8, edgeAlpha=0.6, edgeWidth=2, edgeTop=0.9, edgeBottom=0.4,
        edgeLeft=0.5, edgeRight=0.5,
        innerIntensity=0.55, innerAlpha=0.45, innerDouble=True,
        ambientIntensity=0.5, ambientAlpha=0.4, ambientBlur=30,
        refractStrength=0.6, refractRange=60, refractEdge=10, refractAlpha=0.5,
        dispStrength=0.55, dispRange=45, dispAlpha=0.45, dispShift=9, dispWidth=4,
        liquidStrength=0.4, liquidAmp=5, liquidSpeed=6,
        borderAlpha=0.5, shadowStrength=0.34, shadowBlur=32,
        dynamic=True, dynamicIntensity=0.5, dynamicSpeed=4, mouseFollow=0.8, spotlight=True, spotIntensity=0.5,
    ),
}


def default_materials() -> dict[str, Any]:
    """出厂材质：**未启用**，完整参数已铺好（面板一开就能调）。"""
    blank = _blank_params()
    return {
        "version": MATERIAL_VERSION,
        "enabled": False,
        "preset": "custom",
        "quality": "balanced",
        "lock": False,
        "global": blank["global"],
        "lighting": blank["lighting"],
        "refraction": blank["refraction"],
        "surface": blank["surface"],
        "animation": blank["animation"],
        "dark": {},
        "components": {},
        "custom_presets": {},
    }


def normalize_materials(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """任意来源的 materials → 合法结构；坏值回退默认并返回人类可读警告。"""
    base = default_materials()
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return base, warnings

    clean: dict[str, Any] = copy.deepcopy(base)

    enabled = _coerce_bool(raw.get("enabled", base["enabled"]))
    clean["enabled"] = bool(enabled) if enabled is not None else base["enabled"]

    # 内置预设名大小写不敏感（全部小写）；自定义预设名按用户原文匹配 ——
    # 这里先只做内置归一，命中不上的先原样留着，等 custom_presets 解析完再审一次。
    preset_raw = str(raw.get("preset", "custom")).strip()
    preset_key = preset_raw.lower()
    clean["preset"] = preset_key if preset_key in PRESET_IDS else preset_raw

    quality = str(raw.get("quality", "balanced")).strip().lower()
    clean["quality"] = quality if quality in QUALITY_MODES else "balanced"

    # 「跟随主题切换」的反面：lock=True 时应用主题不替换材质（用户自己调的那套要留住）。
    lock = _coerce_bool(raw.get("lock", base.get("lock", False)))
    clean["lock"] = bool(lock) if lock is not None else False

    for section in SECTIONS:
        clean[section] = _clean_section(raw.get(section), section, warnings, section)

    # 亮暗差异：只保留用户显式给出的键，未给出 / 非法的键继承亮色值。
    dark_raw = raw.get("dark")
    if isinstance(dark_raw, dict):
        dark_clean: dict[str, Any] = {}
        for section in SECTIONS:
            payload = dark_raw.get(section)
            if not isinstance(payload, dict):
                continue
            explicit: dict[str, Any] = {}
            for key, value in payload.items():
                param = PARAM_BY_KEY.get(key)
                if param is None or param.section != section:
                    continue
                ok, coerced = _coerce_param(param, value)
                if not ok:
                    warnings.append(f"dark.{section}.{key}: 非法值 {value!r}，继承亮色")
                    continue
                explicit[key] = coerced
            if explicit:
                dark_clean[section] = explicit
        clean["dark"] = dark_clean

    components_raw = raw.get("components")
    if isinstance(components_raw, dict):
        components: dict[str, Any] = {}
        for comp, payload in components_raw.items():
            name = str(comp)
            if name not in COMPONENTS or not isinstance(payload, dict):
                continue
            mode = str(payload.get("mode", "inherit")).strip().lower()
            if mode not in ("inherit", "preset", "custom"):
                mode = "inherit"
            entry: dict[str, Any] = {"mode": mode}
            preset_name = str(payload.get("preset", "")).strip().lower()
            if preset_name in PRESETS:
                entry["preset"] = preset_name
            for key in COMPONENT_PARAMS:
                if key not in payload:
                    continue
                ok, coerced = _coerce_param(PARAM_BY_KEY[key], payload[key])
                if not ok:
                    warnings.append(f"components.{name}.{key}: 非法值 {payload[key]!r}，已忽略")
                    continue
                entry[key] = coerced
            if entry["mode"] == "custom" and not any(k in entry for k in COMPONENT_PARAMS):
                entry["mode"] = "inherit"
            components[name] = entry
        clean["components"] = components

    custom = raw.get("custom_presets")
    if isinstance(custom, dict):
        kept: dict[str, Any] = {}
        for name, payload in custom.items():
            title = str(name).strip()[:40]
            if not title or not isinstance(payload, dict):
                continue
            if title.lower() in PRESET_IDS:
                warnings.append(
                    "custom_presets: %r 与内置预设同名，已忽略（内置预设不能被自定义覆盖）" % title
                )
                continue
            kept[title] = {
                section: _clean_section(payload.get(section), section, [], title) for section in SECTIONS
            }
        clean["custom_presets"] = kept

    # 自定义预设名只有在它真的存在于 custom_presets 里才算数：否则（含旧版本
    # 遗留的自由文本）一律退回 custom，不能让 theme.json 里留着指向空气的标签。
    if clean["preset"] not in PRESET_IDS and clean["preset"] not in clean["custom_presets"]:
        clean["preset"] = "custom"

    return clean, warnings


def apply_materials(current: dict[str, Any], patch: Any) -> tuple[dict[str, Any], list[str]]:
    """把一次「应用材质」的增量补丁合并进现有材质。

    语义：

    * preset 指向内置预设且补丁没带具体参数 → 用整套预设铺底（切换预设）。
    * 补丁里显式给出的参数优先；未给出的保持现状。
    * enabled / quality / preset 直接覆盖。
    """
    base = copy.deepcopy(current) if isinstance(current, dict) else default_materials()
    if not isinstance(patch, dict):
        return base, ["materials: 补丁必须是对象"]

    warnings: list[str] = []

    preset = patch.get("preset")
    if isinstance(preset, str):
        key = preset.strip().lower()
        if key in PRESETS:
            # 切换预设 = 整套参数替换（不是只改一个颜色）。
            for section in SECTIONS:
                base[section] = copy.deepcopy(PRESETS[key][section])
            base["dark"] = {}
            base["preset"] = key
        elif key == "custom":
            base["preset"] = "custom"
        elif key in (base.get("custom_presets") or {}):
            for section in SECTIONS:
                source = (base["custom_presets"][key] or {}).get(section)
                if isinstance(source, dict):
                    base[section].update(copy.deepcopy(source))
            base["preset"] = key
        else:
            warnings.append(f"preset: 未知预设 {preset!r}，已保持 custom")

    if "enabled" in patch:
        coerced = _coerce_bool(patch.get("enabled"))
        if coerced is None:
            warnings.append(f"enabled: 无法解析为布尔值 {patch.get('enabled')!r}")
        else:
            base["enabled"] = coerced

    if "quality" in patch:
        quality = str(patch.get("quality", "")).strip().lower()
        if quality in QUALITY_MODES:
            base["quality"] = quality
        else:
            warnings.append(f"quality: 未知画质档位 {patch.get('quality')!r}")

    if "lock" in patch:
        coerced_lock = _coerce_bool(patch.get("lock"))
        if coerced_lock is None:
            warnings.append(f"lock: 无法解析为布尔值 {patch.get('lock')!r}")
        else:
            base["lock"] = coerced_lock

    for section in SECTIONS:
        payload = patch.get(section)
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            param = PARAM_BY_KEY.get(key)
            if param is None or param.section != section:
                continue
            ok, coerced = _coerce_param(param, value)
            if not ok:
                warnings.append(f"{section}.{key}: 非法值 {value!r}，已忽略")
                continue
            base[section][key] = coerced

    dark_patch = patch.get("dark")
    if isinstance(dark_patch, dict):
        dark = base.setdefault("dark", {})
        for section in SECTIONS:
            payload = dark_patch.get(section)
            if not isinstance(payload, dict):
                continue
            bucket = dark.setdefault(section, {})
            for key, value in payload.items():
                param = PARAM_BY_KEY.get(key)
                if param is None or param.section != section:
                    continue
                ok, coerced = _coerce_param(param, value)
                if not ok:
                    warnings.append(f"dark.{section}.{key}: 非法值 {value!r}，已忽略")
                    continue
                bucket[key] = coerced

    components_patch = patch.get("components")
    if isinstance(components_patch, dict):
        merged = dict(base.get("components") or {})
        cleaned, component_warnings = normalize_materials({"components": components_patch})
        warnings.extend(component_warnings)
        for comp, entry in (cleaned.get("components") or {}).items():
            merged[comp] = entry
        base["components"] = merged

    custom_patch = patch.get("custom_presets")
    if isinstance(custom_patch, dict):
        stored, _ = normalize_materials({"custom_presets": custom_patch})
        merged_custom = dict(base.get("custom_presets") or {})
        merged_custom.update(stored.get("custom_presets") or {})
        base["custom_presets"] = merged_custom

    base["version"] = MATERIAL_VERSION
    return base, warnings


def resolve_section(materials: dict[str, Any], section: str, dark: bool) -> dict[str, Any]:
    """取某一节在某模式下的**实际生效值**（暗色缺省键继承亮色）。"""
    light = materials.get(section) or {}
    if not dark:
        return dict(light)
    out = dict(light)
    dark_section = (materials.get("dark") or {}).get(section) or {}
    out.update(dark_section)
    return out


def _scale_px(value: str, factor: float) -> str:
    """把 '18px' 这类字面量按比例缩放；不是 px 就原样返回。"""
    if not isinstance(value, str) or not value.endswith("px"):
        return value
    try:
        number = float(value[:-2])
    except ValueError:
        return value
    return f"{round(number * factor, 2):g}px"


def _scale_unit(value: str, factor: float) -> str:
    """把 '0.35' 这类 0-1 乘子按比例缩放并钳到 [0, 1]。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return f"{round(_clamp(number * factor, 0.0, 1.0), 3):g}"


def _scale_shadow_blur(value: str, factor: float) -> str:
    """把 box-shadow shorthand 里的模糊半径按 factor 缩放。

    组件级阴影统一是 `0 <distance>px <blur>px <spread>px rgba(...)`，模糊是第 3 个长度值。
    省电/极致档对全局 `--nts-mt-shadow-blur` 做了缩放，组件自定义阴影也必须同步 ——
    否则用户给某个组件单独设了阴影之后，切画质档就只剩全局在降级。
    """
    if not isinstance(value, str) or value == "none":
        return value
    tokens = value.split()
    lengths = 0
    for index, token in enumerate(tokens):
        if token.startswith(("rgba(", "rgb(", "#")):
            return value
        lengths += 1
        if lengths == 3:
            tokens[index] = _scale_px(token, factor)
            return " ".join(tokens)
    return value


def _boost_alpha(value: str, factor: float) -> str:
    """把 'rgba(r, g, b, a)' 的 a 按比例放大（钳到 1）。"""
    if not isinstance(value, str) or not value.startswith("rgba("):
        return value
    body = value[5:-1].split(",")
    if len(body) != 4:
        return value
    try:
        alpha = float(body[3])
    except ValueError:
        return value
    body[3] = f" {round(_clamp(alpha * factor, 0.0, 1.0), 3):g}"
    return "rgba(" + ",".join(body) + ")"


def apply_quality(target: dict[str, str], quality: str) -> None:
    """按画质档位做**实质性**降级/升级（不是只改个标签）。

    * 省电：模糊减半、关掉折射色散/动态光照/液态形变/噪点/光斑 —— 这些是
      逐帧重绘和 backdrop-filter 的大头，关掉之后滚动明显更顺。
    * 均衡：出厂值，不额外处理。
    * 高品质：环境反射与内高光加强一档，阴影更细腻。
    * 极致：保持全量（折射/色散/动态反射都按用户设定跑）。
    """
    if quality == "saver":
        for key in ("--nts-mt-blur", "--nts-mt-refract-blur", "--nts-mt-ambient-blur"):
            if key in target:
                target[key] = _scale_px(target[key], 0.5)
        # 组件级模糊也要跟着降，否则「省电」在自定义组件上失效。
        for key in list(target.keys()):
            if key.startswith("--nts-mt-c-blur-"):
                target[key] = _scale_px(target[key], 0.5)
        if "--nts-mt-shadow-blur" in target:
            target["--nts-mt-shadow-blur"] = _scale_px(target["--nts-mt-shadow-blur"], 0.7)
        # 组件自定义阴影同理：它是 shorthand，改不了 blur 变量，只能整条缩放。
        for key in list(target.keys()):
            if key.startswith("--nts-mt-c-shadow-"):
                target[key] = _scale_shadow_blur(target[key], 0.7)
        # 透明 + 关掉逐帧效果：用完全透明代替「有值但很弱」，避免残留边缘。
        for key in ("--nts-mt-disp",):
            target[key] = "rgba(0, 0, 0, 0)"
        for key in ("--nts-mt-dynamic", "--nts-mt-dynamic-reflect", "--nts-mt-liquid",
                    "--nts-mt-noise", "--nts-mt-spotlight"):
            target[key] = "0"
        # 无限循环的液态漂移才是省电模式的大头：上面把 liquid 置 0 只让渐变退化成
        # 静态，但 --nts-mt-anim 若仍是 infinite，浏览器照样逐帧重绘 —— 等于白关。
        target["--nts-mt-anim"] = "none"
        # 玻璃表面是叠 7 层背景画出来的。把变量调透明只是「画了但看不见」，
        # 图层仍在合成里；省电要真的少画几层 —— 纯装饰的多层渐变直接置 none，
        # 只保留底色与染色（视觉从「液态玻璃」退化为「磨砂平涂」，这是省电该付的代价）。
        for key in ("--nts-mt-inner-img", "--nts-mt-inner2", "--nts-mt-sheen-img", "--nts-mt-noise-img",
                    "--nts-mt-ambient-img", "--nts-mt-spot"):
            if key in target:
                target[key] = "none"
    elif quality == "high":
        # 多层高光更强、阴影更细（blur 收一点，玻璃层次更清楚）。
        for key in ("--nts-mt-ambient", "--nts-mt-inner", "--nts-mt-edge"):
            if key in target:
                target[key] = _boost_alpha(target[key], 1.15)
        if "--nts-mt-shadow-blur" in target:
            target["--nts-mt-shadow-blur"] = _scale_px(target["--nts-mt-shadow-blur"], 0.85)
    elif quality == "ultra":
        # 极致档必须与均衡档有可见差异：否则四档画质里有两档是同一个东西，
        # 用户切了没反应，就成了摆设。更大模糊、更宽色散、更强高光正是选它的理由。
        for key in ("--nts-mt-ambient", "--nts-mt-inner", "--nts-mt-edge"):
            if key in target:
                target[key] = _boost_alpha(target[key], 1.3)
        for key in ("--nts-mt-blur", "--nts-mt-disp-blur", "--nts-mt-refract-blur"):
            if key in target:
                target[key] = _scale_px(target[key], 1.15)
        # 组件级模糊同样跟着提，否则内置组件仍是均衡档的厚度。
        for key in list(target.keys()):
            if key.startswith("--nts-mt-c-blur-"):
                target[key] = _scale_px(target[key], 1.15)
        if "--nts-mt-shadow-blur" in target:
            target["--nts-mt-shadow-blur"] = _scale_px(target["--nts-mt-shadow-blur"], 1.15)
        for key in list(target.keys()):
            if key.startswith("--nts-mt-c-shadow-"):
                target[key] = _scale_shadow_blur(target[key], 1.15)


def compile_materials(materials: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """材质 → --nts-mt-* 变量（亮色 / 暗色两份）。

    编译产物是**完整值**（颜色已带 alpha、阴影已拼成 shorthand），加载器与
    面板预览都只做字符串拼接，不再做数值换算 —— 预览与应用窗口因此不会因为
    「两边各算一遍」而出现偏差。
    """
    light: dict[str, str] = {}
    dark: dict[str, str] = {}

    for section in SECTIONS:
        light_values = resolve_section(materials, section, dark=False)
        dark_values = resolve_section(materials, section, dark=True)
        for param in PARAMS_BY_SECTION[section]:
            light[var_name(param)] = _css_value(param, light_values.get(param.key, param.default))
            dark[var_name(param)] = _css_value(param, dark_values.get(param.key, param.default))

    for target, dark_mode in ((light, False), (dark, True)):
        values = resolve_section(materials, "global", dark_mode)
        tint = _parse_color(values.get("tint")) or (255, 255, 255, 1.0)
        if dark_mode:
            # 暗色主题下玻璃底必须压暗：宿主暗色主题的文字是浅色，玻璃底若仍是白色（tint 默认白），
            # 不透明度一高就是白底浅字，直接看不清。混入深灰而不是换纯黑 —— 用户染色仍要透得出来。
            tint = _mix_rgb(tint, (16, 20, 28, 1.0), 0.62)
        opacity = float(values.get("opacity", 0.72))
        tint_strength = float(values.get("tintStrength", 0.35))
        target["--nts-mt-bg"] = _rgba(tint, opacity * tint_strength)

        dye_a = _parse_color(values.get("dyePrimary")) or (125, 211, 252, 1.0)
        dye_b = _parse_color(values.get("dyeSecondary")) or (167, 139, 250, 1.0)
        # 冷暖倾向直接改染料本身：背景层没有 hue-rotate 的干净落点（会连带文字颜色），
        # 旧 --nts-mt-dye-warmth 产出后没有任何消费者，滑块是死的。
        warmth = _clamp(float(values.get("dyeWarmth", 0.0)), -100.0, 100.0) / 100.0
        if warmth > 0.0:
            dye_a = _mix_rgb(dye_a, (255, 150, 92, 1.0), warmth * 0.45)
            dye_b = _mix_rgb(dye_b, (255, 176, 120, 1.0), warmth * 0.35)
        elif warmth < 0.0:
            dye_a = _mix_rgb(dye_a, (96, 176, 255, 1.0), -warmth * 0.45)
            dye_b = _mix_rgb(dye_b, (140, 200, 255, 1.0), -warmth * 0.35)
        strength = float(values.get("dyeStrength", 0.28))
        alpha = float(values.get("dyeAlpha", 0.45))
        gradient = float(values.get("dyeGradient", 0.4))
        # 液态强度也调染料浓度：液态越强，流动的染料越明显。
        # 旧 --nts-mt-liquid-alpha 只是个数字，没有任何规则消费它。
        liq_strength = _clamp(float(resolve_section(materials, "refraction", dark_mode).get("liquidStrength", 0.35)), 0.0, 1.0)
        liquid_gain = 0.55 + liq_strength * 0.9
        if strength <= 0.001 or alpha <= 0.001:
            target["--nts-mt-dye"] = "none"
        else:
            a1 = alpha * strength * liquid_gain
            a2 = alpha * strength * (1.0 - gradient * 0.55) * liquid_gain
            target["--nts-mt-dye"] = (
                f"linear-gradient(135deg, {_rgba(dye_a, a1)}, {_rgba(dye_b, a2)})"
            )

    for target, dark_mode in ((light, False), (dark, True)):
        values = resolve_section(materials, "lighting", dark_mode)
        edge = _parse_color(values.get("edgeColor")) or (255, 255, 255, 1.0)
        edge_alpha = float(values.get("edgeAlpha", 0.35)) * float(values.get("edgeIntensity", 0.4))
        target["--nts-mt-edge"] = _rgba(edge, edge_alpha)
        ambient = _parse_color(values.get("ambientColor")) or (207, 232, 255, 1.0)
        ambient_alpha = float(values.get("ambientAlpha", 0.25)) * float(values.get("ambientIntensity", 0.25))
        target["--nts-mt-ambient"] = _rgba(ambient, ambient_alpha)
        inner_alpha = float(values.get("innerAlpha", 0.3)) * float(values.get("innerIntensity", 0.35))
        target["--nts-mt-inner"] = _rgba(edge, inner_alpha)

    for target, dark_mode in ((light, False), (dark, True)):
        values = resolve_section(materials, "surface", dark_mode)
        strength = float(values.get("shadowStrength", 0.35))
        distance = float(values.get("shadowDistance", 8.0))
        blur = float(values.get("shadowBlur", 24.0))
        spread = float(values.get("shadowSpread", 0.0))
        if strength <= 0.001:
            # box-shadow 是逗号分隔的多层声明，任何一层写 none 都会让整条失效，
            # 所以「不加阴影」要用全透明的合法层，而不是 none。
            target["--nts-mt-shadow"] = "0 0 0 rgba(0, 0, 0, 0)"
        else:
            target["--nts-mt-shadow"] = (
                f"0 {round(distance, 2):g}px {round(blur, 2):g}px {round(spread, 2):g}px "
                f"rgba(0, 0, 0, {round(strength * 0.45, 3)})"
            )
        if not values.get("innerShadow"):
            target["--nts-mt-inset"] = "inset 0 0 0 rgba(0, 0, 0, 0)"
        else:
            inner = float(values.get("innerShadowStrength", 0.25))
            target["--nts-mt-inset"] = (
                f"inset 0 0 {round(blur * 0.6, 2):g}px rgba(0, 0, 0, {round(inner * 0.5, 3)})"
            )
        target["--nts-mt-border"] = _rgba((255, 255, 255, 1.0), float(values.get("borderAlpha", 0.35)))

    for target, dark_mode in ((light, False), (dark, True)):
        values = resolve_section(materials, "refraction", dark_mode)
        refract_alpha = float(values.get("refractAlpha", 0.3)) * float(values.get("refractStrength", 0.2))
        target["--nts-mt-refract-tint"] = _rgba((255, 255, 255, 1.0), refract_alpha)
        disp = float(values.get("dispStrength", 0.12)) * float(values.get("dispAlpha", 0.25))
        target["--nts-mt-disp"] = _rgba((140, 200, 255, 1.0), disp)
        target["--nts-mt-liquid-alpha"] = str(round(float(values.get("liquidStrength", 0.35)), 3))

    # 派生变量（v2）：CSS 里需要"已算好"的复合值，避免在样式表里做颜色/时长运算。
    # 这样"开关类参数"（噪点、追光、动态反射、减弱动效）在关闭时产出惰性值，
    # CSS 不需要 if，也不会因为 alpha=0 的元素仍在逐帧合成而白烧 GPU。
    for target, dark_mode in ((light, False), (dark, True)):
        lig = resolve_section(materials, "lighting", dark_mode)
        edge_color = _parse_color(lig.get("edgeColor")) or (255, 255, 255, 1.0)
        edge_base = float(lig.get("edgeAlpha", 0.35)) * float(lig.get("edgeIntensity", 0.4))
        for side, side_key in (("t", "edgeTop"), ("b", "edgeBottom"), ("l", "edgeLeft"), ("r", "edgeRight")):
            target["--nts-mt-edge-" + side] = _rgba(edge_color, edge_base * float(lig.get(side_key, 0.25)))
        if lig.get("innerDouble"):
            # 第二层高光落在对侧内角，表达双层玻璃的厚度感。
            # 旧值是纯 rgba，作为 background-image 的一层根本画不出来。
            target["--nts-mt-inner2"] = (
                "radial-gradient(circle at 28% 24%, "
                + _rgba(edge_color, edge_base * 0.5)
                + " 0%, rgba(0, 0, 0, 0) 58%)"
            )
        else:
            target["--nts-mt-inner2"] = "none"
        liq = resolve_section(materials, "refraction", dark_mode)
        # dynamicReflect 关闭 = 不显示环境反射（ambient 层置全透明），比只停动画更彻底
        if not lig.get("dynamicReflect"):
            target["--nts-mt-ambient"] = "rgba(0, 0, 0, 0)"
        liquid_on = bool(liq.get("liquid"))
        dynamic_on = bool(lig.get("dynamic"))
        if liquid_on or dynamic_on:
            # 漂移一轮的时长：120 / 速度。旧口径 30 / 速度 会算出 5s 一轮，
            # 玻璃表面像被风刮；苹果那种「几乎静止的流动」需要 20s 以上。
            liquid_dur = _clamp(120.0 / max(0.5, float(liq.get("liquidSpeed", 9.0))), 10.0, 90.0)
            dynamic_dur = _clamp(120.0 / max(0.5, float(lig.get("dynamicSpeed", 6.0))), 10.0, 90.0)
            if liquid_on and dynamic_on:
                dur = min(liquid_dur, dynamic_dur)
            else:
                dur = liquid_dur if liquid_on else dynamic_dur
            smooth = _clamp(float(liq.get("liquidSmooth", 70.0)) / 100.0, 0.0, 1.0)
            # 平滑度是 0-100 的连续滑块，不能只有三档：70 与 83 拖起来一模一样等于没做。
            ease = _ease_curve(smooth)
            target["--nts-mt-anim"] = f"nts-mt-drift {round(dur, 2):g}s {ease} infinite"
        else:
            target["--nts-mt-anim"] = "none"

        surf = resolve_section(materials, "surface", dark_mode)
        if surf.get("noise"):
            noise_a = float(surf.get("noiseStrength", 0.18)) * float(surf.get("noiseAlpha", 0.2)) * 0.5
            density = _clamp(float(surf.get("noiseDensity", 50.0)) / 100.0, 0.0, 1.0)
            # 密度越高，同一点径下颗粒间隙越小 —— 视觉上更「密」。
            noise_gap = max(1.0, float(surf.get("noiseSize", 3.0)) * (1.6 - density))
            target["--nts-mt-noise-img"] = (
                "repeating-radial-gradient(circle at 50% 50%, "
                f"rgba(255, 255, 255, {round(noise_a, 3):g}) 0, rgba(0, 0, 0, 0) {round(noise_gap, 2):g}px)"
            )
        else:
            target["--nts-mt-noise-img"] = "none"

        anim = resolve_section(materials, "animation", dark_mode)
        if not anim.get("animation") or anim.get("reducedMotion"):
            for dur_key in ("enterDuration", "exitDuration", "hoverDuration", "pressDuration", "expandDuration"):
                target[var_name(PARAM_BY_KEY[dur_key])] = "0ms"
        if anim.get("spotlight"):
            spot_a = float(anim.get("spotIntensity", 0.35)) * 0.5
            # spotSpeed 之前是死参数：它只写进 theme.json，没有任何规则消费。
            # 这里把它变成「光斑半径倍率」（越快越大越柔），用 calc 直接乘在
            # 半径变量上，于是 spotRadius 与 spotSpeed 同时生效。
            spot_speed = _clamp(float(anim.get("spotSpeed", 60.0)), 0.0, 100.0)
            spot_mult = round(0.55 + spot_speed / 100.0 * 0.85, 3)
            target["--nts-mt-spot"] = (
                "radial-gradient(circle calc(var(--nts-mt-spot-radius) * "
                + f"{spot_mult:g}"
                + ") at var(--nts-mt-mx, 50%) var(--nts-mt-my, 50%), "
                + f"rgba(255, 255, 255, {round(spot_a, 3):g}), rgba(0, 0, 0, 0) 70%)"
            )
        else:
            target["--nts-mt-spot"] = "none"

        # ---- v1.6.1 Liquid Glass 光学层 ----
        # 苹果玻璃的观感不是「一条描边」，而是五件事叠出来的：顶部镜面亮线、
        # 亮线下方的柔光带、底部被折射压暗的收边、整面内发光、边缘微色散。
        # 这一段同时把 v1.6.0 里「算出来但 CSS 没人消费」的 15 个参数接上 ——
        # 之前拖那些滑块只会改 theme.json，观感一动不动，属于明确的假功能。
        glb = resolve_section(materials, "global", dark_mode)
        refr = resolve_section(materials, "refraction", dark_mode)
        level_factor = {"low": 0.55, "medium": 1.0, "high": 1.65, "ultra": 2.35}.get(
            str(glb.get("blurLevel", "medium")), 1.0
        )
        base_blur = float(glb.get("blur", 18.0)) * level_factor
        target["--nts-mt-blur"] = f"{round(_clamp(base_blur, 0.0, 80.0), 2):g}px"
        edge_top = float(lig.get("edgeTop", 0.6))
        sheen = _clamp(edge_base * edge_top * 1.55, 0.0, 0.95)
        target["--nts-mt-sheen"] = _rgba((255, 255, 255, 1.0), sheen)
        fade = _clamp(float(lig.get("edgeFade", 70.0)) / 100.0, 0.0, 1.0)
        stop_a = 8.0 + fade * 34.0
        stop_b = min(100.0, stop_a + 18.0)
        if sheen <= 0.004:
            target["--nts-mt-sheen-img"] = "none"
        else:
            target["--nts-mt-sheen-img"] = (
                "linear-gradient(var(--nts-mt-light-angle, 162deg), "
                + _rgba((255, 255, 255, 1.0), round(sheen * 0.62, 3))
                + " 0%, "
                + _rgba((255, 255, 255, 1.0), round(sheen * 0.18, 3))
                + f" {round(stop_a, 1):g}%, rgba(0, 0, 0, 0) {round(stop_b, 1):g}%)"
            )
        edge_bottom = float(lig.get("edgeBottom", 0.2))
        heel = _clamp(0.10 + edge_base * (1.0 - edge_bottom) * 0.38, 0.0, 0.52)
        target["--nts-mt-heel"] = _rgba((4, 8, 16, 1.0), heel)
        inner_alpha = float(lig.get("innerAlpha", 0.3)) * float(lig.get("innerIntensity", 0.35))
        ipos = _clamp(float(lig.get("innerPos", 30.0)), 0.0, 100.0)
        irange = _clamp(float(lig.get("innerRange", 55.0)), 2.0, 200.0)
        isoft = _clamp(float(lig.get("innerSoftness", 60.0)), 0.0, 100.0)
        if inner_alpha <= 0.004:
            target["--nts-mt-inner-img"] = "none"
        else:
            target["--nts-mt-inner-img"] = (
                "radial-gradient(ellipse "
                + f"{round(irange * 1.25, 1):g}% {round(irange * 1.25, 1):g}% at 50% {round(ipos, 1):g}%, "
                + _rgba((255, 255, 255, 1.0), round(_clamp(inner_alpha * 0.95, 0.0, 0.9), 3))
                + f" 0%, rgba(0, 0, 0, 0) {round(_clamp(60.0 + isoft * 0.4, 0.0, 100.0), 1):g}%)"
            )
        amb_blur = _clamp(float(lig.get("ambientBlur", 24.0)), 0.0, 120.0)
        amb_a = float(lig.get("ambientAlpha", 0.25)) * float(lig.get("ambientIntensity", 0.25))
        dyn_on = bool(lig.get("dynamic"))
        dyn_int = _clamp(float(lig.get("dynamicIntensity", 0.3)), 0.0, 1.0) if dyn_on else 0.0
        amb_color = _parse_color(lig.get("ambientColor")) or (207, 232, 255, 1.0)
        amb_stop = _clamp(30.0 + amb_blur * 1.8, 30.0, 100.0)
        if not lig.get("dynamicReflect") or amb_a <= 0.004:
            target["--nts-mt-ambient-img"] = "none"
        else:
            target["--nts-mt-ambient-img"] = (
                f"linear-gradient({round(float(lig.get('ambientDirection', 135.0)), 1):g}deg, "
                + _rgba(amb_color, round(_clamp(amb_a * (0.75 + dyn_int * 0.9), 0.0, 0.95), 3))
                + f" 0%, rgba(0, 0, 0, 0) {round(amb_stop, 1):g}%)"
            )
        amb_range = _clamp(float(lig.get("ambientRange", 60.0)), 20.0, 200.0)
        target["--nts-mt-ambient-size"] = f"{round(_clamp(amb_range + amb_blur * 0.8, 20.0, 260.0), 1):g}%"
        dyn_range = _clamp(float(lig.get("dynamicRange", 45.0)), 0.0, 200.0)
        liq_range = _clamp(float(refr.get("liquidRange", 50.0)), 0.0, 200.0)
        target["--nts-mt-drift-size"] = (
            f"{round(_clamp(100.0 + dyn_range, 100.0, 320.0), 1):g}% "
            f"{round(_clamp(100.0 + liq_range * 0.6, 100.0, 320.0), 1):g}%"
        )
        r_edge = _clamp(float(refr.get("refractEdge", 6.0)), 0.0, 40.0)
        r_blur = _clamp(float(refr.get("refractBlur", 18.0)), 0.0, 80.0)
        r_range = _clamp(float(refr.get("refractRange", 40.0)), 0.0, 200.0)
        r_local = _clamp(float(refr.get("refractLocal", 0.5)), 0.0, 1.0)
        refract_a = float(refr.get("refractAlpha", 0.3)) * float(refr.get("refractStrength", 0.2))
        target["--nts-mt-lens"] = f"{round(max(1.5, r_edge * (0.6 + r_local * 0.9)), 2):g}px"
        target["--nts-mt-lens-blur"] = f"{round(max(1.0, r_blur * 0.9), 2):g}px"
        target["--nts-mt-lens-glow"] = _rgba((255, 255, 255, 1.0), round(_clamp(refract_a * 0.9, 0.0, 0.7), 3))
        target["--nts-mt-refract-size"] = f"{round(_clamp(100.0 + r_range * 0.5, 100.0, 260.0), 1):g}%"
        disp_rng = _clamp(float(refr.get("dispRange", 30.0)), 0.0, 200.0)
        target["--nts-mt-disp-blur"] = f"{round(max(0.5, float(refr.get('dispWidth', 2.0)) * (1.0 + disp_rng / 60.0)), 2):g}px"
        # v1.6.10 光学补强：随指针的边缘镜面环带 + 边缘收暗 + 双向色散分色。
        # 环带用 var(--nts-mt-mx/-my) 定位 —— 加载器在指针移动时写这两个变量，
        # 于是高光沿玻璃边缘走，而不是钉死在某个固定角度。
        rim_alpha = _clamp(edge_base * (0.35 + edge_top * 0.55), 0.0, 0.62)
        if rim_alpha <= 0.004:
            target["--nts-mt-rim-img"] = "none"
        else:
            target["--nts-mt-rim-img"] = (
                "radial-gradient(115% 105% at var(--nts-mt-mx, 50%) var(--nts-mt-my, 50%), "
                + "rgba(0, 0, 0, 0) 56%, "
                + _rgba((255, 255, 255, 1.0), round(rim_alpha, 3))
                + " 92%, rgba(0, 0, 0, 0) 100%)"
            )
        rim_shade = _clamp(0.08 + edge_base * (1.0 - edge_bottom) * 0.30, 0.0, 0.42)
        target["--nts-mt-rim-shade"] = _rgba((6, 10, 18, 1.0), round(rim_shade, 3))
        disp_strength = float(refr.get("dispStrength", 0.2))
        disp_alpha = _clamp(float(refr.get("dispAlpha", 0.25)), 0.0, 1.0)
        # dispAlpha 之前只写进没人消费的 --nts-mt-disp，色散透明度滑块是死的。
        disp_a = _clamp(disp_strength * (0.35 + disp_alpha * 1.2), 0.0, 0.55)
        target["--nts-mt-disp-warm"] = _rgba((255, 126, 176, 1.0), round(disp_a, 3))
        target["--nts-mt-disp-cool"] = _rgba((126, 214, 255, 1.0), round(disp_a, 3))
        # 底部折光亮线：玻璃有厚度时，底边会把光折回来。只用 box-shadow，不进布局。
        under_a = _clamp(edge_base * (0.22 + edge_bottom * 0.5), 0.0, 0.34)
        target["--nts-mt-under-glow"] = _rgba((255, 255, 255, 1.0), round(under_a, 3))

    # 组件级覆盖：只有 mode=preset/custom 的组件才产出专属变量；
    # 其它组件在 CSS 里走 var(--nts-mt-c-<prop>-<comp>, var(--nts-mt-<prop>)) 继承全局。
    # 统一加 c- 前缀，避免与全局参数同名（radiusButton / radiusInput / radiusDialog）。
    for comp, entry in (materials.get("components") or {}).items():
        if comp not in COMPONENTS or not isinstance(entry, dict):
            continue
        mode = entry.get("mode", "inherit")
        if mode == "inherit":
            continue
        style: dict[str, Any] = {}
        if mode == "preset" and entry.get("preset") in PRESETS:
            preset = PRESETS[entry["preset"]]
            style = {
                "opacity": preset["global"]["opacity"],
                "blur": preset["global"]["blur"],
                "radius": preset["surface"]["radius"],
                "borderWidth": preset["surface"]["borderWidth"],
                "shadowStrength": preset["surface"]["shadowStrength"],
            }
        for key in COMPONENT_PARAMS:
            if key in entry:
                style[key] = entry[key]
        for target, dark_mode in ((light, False), (dark, True)):
            glob = resolve_section(materials, "global", dark_mode)
            surf = resolve_section(materials, "surface", dark_mode)
            if "opacity" in style:
                tint = _parse_color(glob.get("tint")) or (255, 255, 255, 1.0)
                tint_strength = float(glob.get("tintStrength", 0.35))
                comp_opacity = _clamp(float(style["opacity"]), 0.0, 1.0)
                target[f"--nts-mt-c-bg-{comp}"] = _rgba(tint, comp_opacity * tint_strength)
            if "shadowStrength" in style:
                comp_strength = _clamp(float(style["shadowStrength"]), 0.0, 1.0)
                if comp_strength <= 0.001:
                    # 同 --nts-mt-shadow：多层 box-shadow 里不能出现 none。
                    target[f"--nts-mt-c-shadow-{comp}"] = "0 0 0 rgba(0, 0, 0, 0)"
                else:
                    distance = float(surf.get("shadowDistance", 8.0))
                    blur = float(surf.get("shadowBlur", 24.0))
                    spread = float(surf.get("shadowSpread", 0.0))
                    target[f"--nts-mt-c-shadow-{comp}"] = (
                        f"0 {round(distance, 2):g}px {round(blur, 2):g}px {round(spread, 2):g}px "
                        f"rgba(0, 0, 0, {round(comp_strength * 0.45, 3)})"
                    )
            for key, value in style.items():
                if key in ("opacity", "shadowStrength"):
                    continue
                target[f"--nts-mt-c-{_kebab(key)}-{comp}"] = _css_value(PARAM_BY_KEY[key], value)

    quality = materials.get("quality", "balanced")
    if quality not in QUALITY_MODES:
        quality = "balanced"
    apply_quality(light, quality)
    apply_quality(dark, quality)

    return light, dark


def material_attributes(materials: dict[str, Any]) -> dict[str, str]:
    """加载器要打在 <html> 上的材质属性（CSS 用它做性能降级 / 开关门控）。"""
    quality = materials.get("quality", "balanced")
    if quality not in QUALITY_MODES:
        quality = "balanced"
    return {
        "data-nts-mt": "1",
        "data-nts-mt-quality": quality,
        "data-nts-mt-preset": str(materials.get("preset", "custom")),
    }
