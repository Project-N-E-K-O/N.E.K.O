"""Fixed Numeric v2 pipeline. User-facing module switches have been removed."""

from __future__ import annotations

# Temporary speed-first pause for foreground audits; background ledger review is independent.
# Restore True to run retained foreground audits; legacy preferences no longer control execution.
MODEL_REVIEWS_ENABLED = False


def default_options() -> dict[str, bool]:
    """All pipeline modules are enabled; only the explicit audit pause can override them."""

    return dict.fromkeys((
        "evaluator", "review", "dispute", "review_delivery", "review_contract",
        "history_lookup", "actor_retry",
    ), True)


async def aload_theater_module_options() -> dict[str, bool]:
    """Return the fixed pipeline without reading or writing user preferences."""

    options = default_options()
    if not MODEL_REVIEWS_ENABLED:
        for key in ("review", "dispute", "review_contract"):
            options[key] = False
    return options
