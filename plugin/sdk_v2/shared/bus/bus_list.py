"""Unified bus namespace facade and list/watcher cores."""

from __future__ import annotations

from plugin.sdk_v2.public.bus._state import ensure_transport

from .conversations import Conversations
from .events import Events
from .lifecycle import Lifecycle
from .memory import Memory
from .messages import Messages
from .records import Records
from .rev import Revision
from .watchers import BusListWatcher, Watchers


from ._core import BusListCore

class BusListWatcherCore(BusListWatcher):
    """Compatibility-oriented watcher core."""

    def start(self):
        super().start()
        return self

    async def start_async(self):
        return self.start()

    def stop(self):
        super().stop()
        return self

    async def stop_async(self):
        return self.stop()


class Bus:
    """Aggregate shared bus facades under one namespace."""

    def __init__(self, _transport=None):
        transport = ensure_transport(_transport)
        self.conversations = Conversations(transport)
        self.messages = Messages(transport)
        self.events = Events(transport)
        self.lifecycle = Lifecycle(transport)
        self.memory = Memory(transport)
        self.records = Records(transport)
        self.revision = Revision(transport)
        self.watchers = Watchers(transport)


__all__ = ["Bus", "BusListCore", "BusListWatcherCore"]
