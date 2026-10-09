from __future__ import annotations

from typing import Any, NoReturn

from plugin.server.domain.errors import ServerDomainError


def http_exception(
    *,
    status_code: int,
    detail: object = None,
    headers: dict[str, str] | None = None,
) -> Exception:
    """Construct an HTTP error without loading the web stack for successful I/O."""
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def raise_http_from_domain(
    error: ServerDomainError,
    *,
    logger: Any,
    include_details: bool = False,
) -> NoReturn:
    log = getattr(logger, error.log_level, logger.warning)
    log(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise http_exception(
        status_code=error.status_code,
        detail=(
            {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            }
            if include_details
            else error.message
        ),
        headers={"X-Error-Code": error.code},
    )
