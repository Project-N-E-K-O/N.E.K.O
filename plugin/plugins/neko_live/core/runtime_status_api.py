"""Runtime compatibility facade for status and projection APIs."""

from __future__ import annotations

from .runtime_dashboard_api import RuntimeDashboardApiMixin
from .runtime_live_status_api import RuntimeLiveStatusApiMixin
from .runtime_recent_context_api import RuntimeRecentContextApiMixin


class RuntimeStatusApiMixin(
    RuntimeDashboardApiMixin,
    RuntimeRecentContextApiMixin,
    RuntimeLiveStatusApiMixin,
):
    """Keep the historical runtime status mixin import path stable."""
