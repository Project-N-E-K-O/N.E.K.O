from __future__ import annotations

from urllib.parse import quote

import httpx

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.messages.memory_query")


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        if timeout > 0:
            return timeout
        return 5.0
    return 5.0


def _normalize_non_empty_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message=f"Invalid {field}",
            status_code=400,
            details={},
        )
    return value


def _build_memory_search_url(*, lanlan_name: str, query: str) -> str:
    from config import MEMORY_SERVER_PORT

    safe_lanlan = quote(lanlan_name, safe="")
    safe_query = quote(query, safe="")
    return f"http://127.0.0.1:{MEMORY_SERVER_PORT}/search_for_memory/{safe_lanlan}/{safe_query}"


class MemoryQueryService:
    async def query_memory(
        self,
        *,
        lanlan_name: object,
        query: object,
        timeout: object,
    ) -> dict[str, object]:
        normalized_lanlan_name = _normalize_non_empty_str(lanlan_name, field="lanlan_name")
        normalized_query = _normalize_non_empty_str(query, field="query")
        normalized_timeout = _coerce_timeout(timeout)
        url = _build_memory_search_url(
            lanlan_name=normalized_lanlan_name,
            query=normalized_query,
        )

        try:
            async with httpx.AsyncClient(timeout=normalized_timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                result = response.json()
            return {"result": result}
        except httpx.TimeoutException as exc:
            logger.warning(
                "query_memory timeout: lanlan_name={}, query={}, timeout={}",
                normalized_lanlan_name,
                normalized_query,
                normalized_timeout,
            )
            raise ServerDomainError(
                code="MEMORY_QUERY_TIMEOUT",
                message="Memory query timed out",
                status_code=504,
                details={"timeout": normalized_timeout},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning(
                "query_memory upstream status error: status_code={}, lanlan_name={}, query={}",
                status_code,
                normalized_lanlan_name,
                normalized_query,
            )
            raise ServerDomainError(
                code="MEMORY_QUERY_UPSTREAM_ERROR",
                message="Memory server returned an error response",
                status_code=502,
                details={"upstream_status": status_code},
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "query_memory connection error: lanlan_name={}, query={}, err_type={}, err={}",
                normalized_lanlan_name,
                normalized_query,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="MEMORY_QUERY_UNAVAILABLE",
                message="Memory server is unavailable",
                status_code=503,
                details={"error_type": type(exc).__name__},
            ) from exc
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, ImportError) as exc:
            logger.error(
                "query_memory failed: lanlan_name={}, query={}, err_type={}, err={}",
                normalized_lanlan_name,
                normalized_query,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="MEMORY_QUERY_FAILED",
                message="Failed to query memory server",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc
