# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Subscriptions & plans: route chosen text tiers through a subscription.

The state lives in ``<state>/subscriptions.json`` on this machine only, apart
from ``core_config.json``: turning a subscription on or off never rewrites the
user's own tier settings. ``get_core_config`` puts the resolved overlay into
its snapshot and ``get_model_api_config`` applies it (see ``overlay``).
"""

from .plans import TEXT_TIERS, get_plan, get_plans
from .store import SubscriptionError, SubscriptionStore, get_store

__all__ = [
    "TEXT_TIERS",
    "SubscriptionError",
    "SubscriptionStore",
    "get_plan",
    "get_plans",
    "get_store",
]
