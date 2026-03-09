from __future__ import annotations

from typing import Any

from ._state import ensure_transport
from .conversations import Conversations
from .events import Events
from .lifecycle import Lifecycle
from .memory import Memory
from .messages import Messages
from .records import Records
from .rev import Revision
from .watchers import Watchers


class Bus:
    def __init__(self, _transport=None, *args: Any, **kwargs: Any):
        transport = ensure_transport(_transport)
        self.conversations = Conversations(transport)
        self.messages = Messages(transport)
        self.events = Events(transport)
        self.lifecycle = Lifecycle(transport)
        self.memory = Memory(transport)
        self.records = Records(transport)
        self.revision = Revision(transport)
        self.watchers = Watchers(transport)


__all__ = ["Bus"]
