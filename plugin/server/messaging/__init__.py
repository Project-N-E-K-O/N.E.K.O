"""
消息系统模块

提供消息平面、总线订阅等消息传递功能。
"""
from plugin.server.messaging.plane_bridge import publish_record, publish_snapshot
from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event

__all__ = ["publish_record", "publish_snapshot", "BusSubscriptionManager", "emit_lifecycle_event"]
