"""Unified bus namespace contract."""

from __future__ import annotations

from typing import Any

from .conversations import Conversations
from .events import Events
from .lifecycle import Lifecycle
from .memory import Memory
from .messages import Messages
from .records import Records
from .rev import Revision
from .watchers import Watchers


class Bus:
    """Aggregate bus clients under one namespace."""

    conversations: Conversations
    messages: Messages
    events: Events
    lifecycle: Lifecycle
    memory: Memory
    records: Records
    revision: Revision
    watchers: Watchers

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.bus_list not implemented")


__all__ = ["Bus"]
