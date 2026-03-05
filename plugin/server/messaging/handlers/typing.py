from __future__ import annotations

from typing import Awaitable, Callable, Protocol


ErrorPayload = str | dict[str, object]


class SendResponse(Protocol):
    def __call__(
        self,
        to_plugin: str,
        request_id: str,
        result: object,
        error: ErrorPayload | None,
        timeout: float = 10.0,
    ) -> None: ...

Request = dict[str, object]
RequestHandler = Callable[[Request, SendResponse], Awaitable[None]]
