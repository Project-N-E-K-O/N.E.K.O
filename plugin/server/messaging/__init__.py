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
