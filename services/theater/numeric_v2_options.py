"""Numeric v2 小剧场可选模块开关。

除"回复"（Actor 生成）之外的每一步模型调用都可以关闭：默认开启前置判定，
其他可选调用默认关闭。关闭某个模块会改变体验语义，各开关的取舍写在这里的唯一来源里，运行端、
HTTP 接口与前端只读这张表。

开关存在全局偏好的全局条目旁（``theaterModule...``），未设置即默认值。
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TheaterModuleOption:
    """一个可选模块：存储键、默认值与它关闭后的语义代价。"""  # noqa: DOCSTRING_CJK

    key: str
    default: bool
    # 关闭后失去什么；供接口与文档复用，避免三处各写一套说法。
    disabled_effect: str


# 顺序即"每回合调用链"的顺序：判定 → 回复 → 快检 → 争议 → 补推荐 → 历史查找。
# 只有"前置判定"（数值增减/路线/转场意图）默认开启，其余可选模块默认关闭。
THEATER_MODULE_OPTIONS: tuple[TheaterModuleOption, ...] = (
    TheaterModuleOption(
        key="evaluator",
        default=True,
        # 数值增减、路线条件与转场意图来自同一次判定调用，因此这一模块默认保留。
        disabled_effect="不再判定数值增减、路线与转场意图；数值冻结，靠数值条件的出口永远不满足。",
    ),
    TheaterModuleOption(
        key="review",
        default=False,
        disabled_effect="不再复核玩家授权、去向公开、动作归属与作者边界；条件型固定旁白也不会触发。",
    ),
    TheaterModuleOption(
        key="dispute",
        default=False,
        disabled_effect="不再追加首次争议的独立思考复查，快检初判直接生效。",
    ),
    TheaterModuleOption(
        key="review_delivery",
        default=False,
        # 纯程序校验，不增加模型调用：换场时核对作者声明必须保留的关键道具是否真的出现过。
        disabled_effect="不再核对作者声明必须保留的关键道具是否已交付，缺失也能继续换场。",
    ),
    TheaterModuleOption(
        key="review_contract",
        default=False,
        # 换场时一次窄判定（约 +2 秒）：核对作者禁令是否被违反，抓"提前到达/越过阶段边界"。
        disabled_effect="不再核对作者禁令是否被违反，提前到达或越过阶段边界也不会被拦下改写。",
    ),
    TheaterModuleOption(
        key="suggestion_fill",
        default=False,
        disabled_effect="推荐条数不在 2—3 条时不再补一次调用，按演员实际返回展示。",
    ),
    TheaterModuleOption(
        key="history_lookup",
        default=False,
        disabled_effect="不再按需查找 Session 原文，需要回忆旧事时保持未知。",
    ),
    TheaterModuleOption(
        key="actor_retry",
        default=False,
        disabled_effect="演员输出不合格时不再重试，本轮原子回滚并由玩家重发。",
    ),
)

_OPTION_BY_KEY = {option.key: option for option in THEATER_MODULE_OPTIONS}
# 存储键前缀：所有小剧场模块开关共用这一命名。
_STORAGE_PREFIX = "theaterModule"


def storage_key(key: str) -> str:
    """Return the preferences key for one module switch."""

    return f"{_STORAGE_PREFIX}{''.join(part.title() for part in key.split('_'))}"


def default_options() -> dict[str, bool]:
    """Every module's default value: all optional modules are off."""

    return {option.key: option.default for option in THEATER_MODULE_OPTIONS}


def normalize_options(values: Mapping[str, Any] | None) -> dict[str, bool]:
    """Project stored values onto the declared keys, filling defaults for anything missing."""

    stored = values if isinstance(values, Mapping) else {}
    result: dict[str, bool] = {}
    for option in THEATER_MODULE_OPTIONS:
        value = stored.get(storage_key(option.key))
        result[option.key] = option.default if not isinstance(value, bool) else value
    return result


def option_keys() -> tuple[str, ...]:
    """Declared module keys, in call-chain order."""

    return tuple(option.key for option in THEATER_MODULE_OPTIONS)


def disabled_effects() -> dict[str, str]:
    """Map each module key to the consequence of turning it off."""

    return {option.key: option.disabled_effect for option in THEATER_MODULE_OPTIONS}


def storage_key_known(key: str) -> bool:
    """True when a storage key belongs to a declared module switch."""

    return key in {storage_key(option.key) for option in THEATER_MODULE_OPTIONS}


async def aload_theater_module_options() -> dict[str, bool]:
    """Read every switch at once; any failure falls back to declared defaults."""

    try:
        from utils.preferences import aload_global_entry_flags

        stored = await aload_global_entry_flags()
    except Exception as exc:
        logger.warning("Numeric v2 module switches unavailable; using defaults: %s", type(exc).__name__)
        return default_options()
    return normalize_options(stored)


async def asave_theater_module_options(values: Mapping[str, Any]) -> bool:
    """Persist the given module switches (unknown keys are ignored)."""

    payload = {
        storage_key(key): bool(value)
        for key, value in (values or {}).items()
        if key in _OPTION_BY_KEY and isinstance(value, bool)
    }
    if not payload:
        return True
    try:
        from utils.preferences import asave_global_entry_flags

        return await asave_global_entry_flags(payload)
    except Exception as exc:
        logger.warning("Numeric v2 module switches could not be saved: %s", type(exc).__name__)
        return False


__all__ = [
    "TheaterModuleOption",
    "THEATER_MODULE_OPTIONS",
    "aload_theater_module_options",
    "asave_theater_module_options",
    "default_options",
    "disabled_effects",
    "normalize_options",
    "option_keys",
    "storage_key",
    "storage_key_known",
]
