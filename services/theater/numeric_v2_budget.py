"""Numeric v2 fixed context budget and legacy save aliases."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Final


NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE: Final = "balanced"
# The user removed tier selection; all calls use the confirmed fixed standard budget.
# Old save/client names are compatibility aliases only; do not rewrite saves or retain old tiers' different capacities.
NUMERIC_V2_ACTOR_BUDGET_PROFILES: Final = {
    name: {
        "input_max_tokens": 10000,
        "history_max_tokens": 5200,
        "history_max_turns": 12,
        "continuity_max_tokens": 1600,
        "evaluator_input_max_tokens": 7000,
        "judge_input_max_tokens": 8500,
        "formal_judge_input_max_tokens": 8500,
        "evidence_max_tokens": 1500,
        "evidence_max_units": 12,
        "field_max_tokens": 360,
    }
    for name in ("economy", "balanced", "quality")
}


def numeric_v2_actor_budget(profile: object) -> dict[str, int]:
    """Return the fixed budget for every legacy name; callers must still handle invalid values explicitly."""  # noqa: DOCSTRING_CJK

    selected = NUMERIC_V2_ACTOR_BUDGET_PROFILES.get(str(profile or ""))
    if selected is None:
        raise ValueError("numeric_actor_budget_profile_invalid")
    return dict(selected)
