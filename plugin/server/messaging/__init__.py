"""
Messaging module.

Provides messaging-plane, bus-subscription, and related messaging functionality.
"""
from plugin.server.messaging import plane_bridge as _plane_bridge_mod
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
# packages have finished importing. Pass the already-loaded plane_bridge module
# directly so hook registration only takes its leaf lock and never competes with
# package import locks or GlobalState._bus_store_lock.
try:
    from plugin.core.state import state as _global_state

    _global_state._ensure_plane_bridge_flush_hook_registered(
        plane_bridge_mod=_plane_bridge_mod
    )
except Exception:
    pass
