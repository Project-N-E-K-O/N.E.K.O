from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Union


ErrorPayload = Union[str, Dict[str, Any]]


class SendResponse(Protocol):
    def __call__(
        self,
        to_plugin: str,
        request_id: str,
        result: Any,
        error: Optional[ErrorPayload],
        timeout: float = 10.0,
    ) -> None: ...

Request = Dict[str, Any]
RequestHandler = Callable[[Request, SendResponse], Awaitable[None]]
