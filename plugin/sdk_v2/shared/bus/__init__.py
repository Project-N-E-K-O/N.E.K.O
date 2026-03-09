"""Shared bus building blocks for SDK v2.

Thin shared facades over `public.bus` implementations.
"""

from ._client_base import BusClientBase, BusTransportError
from .bus_list import Bus, BusListCore, BusListWatcherCore
from .conversations import Conversations
from .events import Events
from .lifecycle import Lifecycle
from .memory import Memory
from .messages import Messages
from .protocols import BusTransportProtocol
from .records import Records
from .rev import Revision
from .types import BusConversation, BusEvent, BusMessage, BusRecord
from .watchers import Watchers

__all__ = [
    "BusTransportProtocol",
    "BusClientBase",
    "BusTransportError",
    "Bus",
    "BusListCore",
    "BusListWatcherCore",
    "BusConversation",
    "BusMessage",
    "BusEvent",
    "BusRecord",
    "Conversations",
    "Messages",
    "Events",
    "Lifecycle",
    "Memory",
    "Records",
    "Revision",
    "Watchers",
]
