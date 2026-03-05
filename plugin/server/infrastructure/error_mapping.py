from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException

from plugin.server.domain.errors import ServerDomainError


def raise_http_from_domain(error: ServerDomainError, *, logger: Any) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)
