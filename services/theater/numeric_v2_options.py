"""Optional Numeric v2 theater module switches.

Every model call other than the Actor reply can be disabled: preliminary evaluation defaults on,
while other optional calls default off. Disabling modules changes experience semantics; this table is the sole source
for those tradeoffs, read by runtime, HTTP APIs, and frontend.

Switches live beside global preference entries (``theaterModule...``); unset means default.
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TheaterModuleOption:
    """One optional module: storage key, default, and semantic cost of disabling it."""  # noqa: DOCSTRING_CJK

    key: str
    default: bool
    # Consequences of disabling modules, shared by APIs and documentation to avoid three divergent explanations.
    disabled_effect: str


# Order matches each turn's call chain: evaluation -> reply -> quick check -> dispute -> extra recommendation -> history lookup.
# Only preliminary evaluation (numeric changes/routes/transition intent) is enabled by default; other optional modules default off.
THEATER_MODULE_OPTIONS: tuple[TheaterModuleOption, ...] = (
    TheaterModuleOption(
        key="evaluator",
        default=True,
        # Numeric changes, route conditions, and transition intent share one evaluation call, so retain this module by default.
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
        # Programmatic validation only, with no extra model calls: on transition, verify author-required key props actually appeared.
        disabled_effect="不再核对作者声明必须保留的关键道具是否已交付，缺失也能继续换场。",
    ),
    TheaterModuleOption(
        key="review_contract",
        default=False,
        # One narrow transition evaluation (about +2 seconds): check author prohibitions for premature arrival / crossing phase boundaries.
        disabled_effect="不再核对作者禁令是否被违反，提前到达或越过阶段边界也不会被拦下改写。",
    ),
    TheaterModuleOption(
        key="suggestion_fill",
        default=False,
        disabled_effect="推荐为空或新换场邀请选项不足时不再补一次调用，按演员实际返回展示；复核删除后不再补写。",
    ),
    TheaterModuleOption(
        key="history_lookup",
        default=False,
        disabled_effect="不再按需查找 Session 原文，需要回忆旧事时保持未知。",
    ),
    TheaterModuleOption(
        key="actor_retry",
        default=False,
        # When enabled, each Actor generation gets at most four attempts (initial + three retries); see Workflow's output-retry loop.
        disabled_effect="演员输出不合格时不再重试（开启时每次生成最多调用演员 4 次），本轮原子回滚并由玩家重发；正式换场复用来源正文时仍保留一次窄重试。",
    ),
)

_OPTION_BY_KEY = {option.key: option for option in THEATER_MODULE_OPTIONS}
# Storage-key prefix shared by all theater module switches.
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
]
