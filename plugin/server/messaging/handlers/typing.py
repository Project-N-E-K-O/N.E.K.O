"""Host operations return values; the RPC dispatcher owns their replies."""

from __future__ import annotations

from typing import Awaitable, Callable

Request = dict[str, object]
RequestHandler = Callable[[Request], Awaitable[object]]
