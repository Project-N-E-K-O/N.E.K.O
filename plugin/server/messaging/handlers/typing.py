"""Host operations return values; the RPC dispatcher owns their replies."""

from __future__ import annotations

from typing import Awaitable, Callable

from plugin.core.rpc import RpcContext

Request = dict[str, object]
RequestHandler = Callable[[RpcContext, Request], Awaitable[object]]
