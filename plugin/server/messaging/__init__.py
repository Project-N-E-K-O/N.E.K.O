"""
Messaging module.

Provides messaging-plane, bus-subscription, and related messaging functionality.
"""
from plugin.server.messaging.plane_bridge import (
    build_frame_record,
    publish_frame,
    publish_record,
    publish_snapshot,
)
from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event

__all__ = [
    "build_frame_record",
    "publish_frame",
    "publish_record",
    "publish_snapshot",
    "BusSubscriptionManager",
    "emit_lifecycle_event",
]

# Complete pending-delete flush hook registration once both state and messaging
# packages have finished importing (importing messaging inside GlobalState.__init__
# hits a circular import back to plugin.core.state.state on normal server startup).
try:
    from plugin.core.state import state as _global_state

    _global_state._ensure_plane_bridge_flush_hook_registered()
except Exception:
    pass
