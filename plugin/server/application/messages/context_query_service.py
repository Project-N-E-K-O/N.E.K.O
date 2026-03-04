from __future__ import annotations

import asyncio

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.messages.context_query")


def _coerce_limit(value: object) -> int:
    if value is None:
        return 20
    if isinstance(value, bool):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="limit must be an integer",
            status_code=400,
            details={},
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="limit must be an integer",
            status_code=400,
            details={},
        ) from exc
    if parsed <= 0:
        return 1
    if parsed > 500:
        return 500
    return parsed


def _get_user_context_sync(bucket_id: str, limit: int) -> list[dict[str, object]]:
    history_obj = state.get_user_context(bucket_id=bucket_id, limit=limit)
    if not isinstance(history_obj, list):
        return []

    history: list[dict[str, object]] = []
    for item in history_obj:
        if not isinstance(item, dict):
            continue
        normalized: dict[str, object] = {}
        for key, value in item.items():
            if isinstance(key, str):
                normalized[key] = value
        history.append(normalized)
    return history


class UserContextQueryService:
    async def get_user_context(self, *, bucket_id: str, limit: object) -> dict[str, object]:
        if not isinstance(bucket_id, str) or not bucket_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="bucket_id is required",
                status_code=400,
                details={},
            )
        normalized_limit = _coerce_limit(limit)
        try:
            history = await asyncio.to_thread(_get_user_context_sync, bucket_id, normalized_limit)
            return {"bucket_id": bucket_id, "history": history}
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_user_context failed: bucket_id={}, err_type={}, err={}",
                bucket_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="USER_CONTEXT_QUERY_FAILED",
                message="Failed to query user context",
                status_code=500,
                details={"bucket_id": bucket_id, "error_type": type(exc).__name__},
            ) from exc
