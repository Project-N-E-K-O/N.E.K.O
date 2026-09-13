"""Numeric v2 固定上下文预算与旧存档别名。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Final


NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE: Final = "balanced"
# 用户取消档位选择，所有调用固定使用已确认的标准预算。
# 旧存档/旧客户端名称只作兼容别名，不改写存档，也不继续采用旧档的不同容量。
NUMERIC_V2_ACTOR_BUDGET_PROFILES: Final = {
    name: {
        "input_max_tokens": 10000,
        "history_max_tokens": 5200,
        "history_max_turns": 12,
        "continuity_max_tokens": 1600,
        "evaluator_input_max_tokens": 7000,
        "judge_input_max_tokens": 6000,
        "formal_judge_input_max_tokens": 8000,
        "evidence_max_tokens": 1500,
        "evidence_max_units": 12,
        "field_max_tokens": 360,
    }
    for name in ("economy", "balanced", "quality")
}


def numeric_v2_actor_budget(profile: object) -> dict[str, int]:
    """旧名称统一返回固定预算；调用方仍须显式处理非法值。"""  # noqa: DOCSTRING_CJK

    selected = NUMERIC_V2_ACTOR_BUDGET_PROFILES.get(str(profile or ""))
    if selected is None:
        raise ValueError("numeric_actor_budget_profile_invalid")
    return dict(selected)
