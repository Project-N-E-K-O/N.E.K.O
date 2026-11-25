from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from urllib.parse import quote

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.utils.http_imports import ensure_httpx

logger = get_logger("server.application.messages.memory_query")

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 60.0
DEADLINE_REPLY_MARGIN_SECONDS = 0.25
LOCALE_LOOKUP_SHARE = 0.5


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, (int, float)):
        timeout = float(value)
        if math.isfinite(timeout) and timeout > 0:
            return min(timeout, MAX_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS
    return DEFAULT_TIMEOUT_SECONDS


def _normalize_non_empty_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message=f"Invalid {field}",
            status_code=400,
            details={},
        )
    return value.strip()


def _memory_server_base_url() -> str:
    from config import MEMORY_SERVER_PORT

    return f"http://127.0.0.1:{MEMORY_SERVER_PORT}"


def _build_memory_query_url(*, lanlan_name: str) -> str:
    safe_lanlan = quote(lanlan_name, safe="")
    return f"{_memory_server_base_url()}/query_memory/{safe_lanlan}"


def _build_prompt_locale_url(*, lanlan_name: str) -> str:
    safe_lanlan = quote(lanlan_name, safe="")
    return f"{_memory_server_base_url()}/prompt-locale/{safe_lanlan}"


def _recall_budget_ms(timeout: float) -> int:
    """Server-side recall budget for this call.

    Past the budget the memory server drops the vector half and answers from
    keywords, so a slow embedding model degrades the result instead of turning
    the whole call into a timeout. Half of the caller's timeout leaves room
    for the locale lookup and local rendering that follow the recall.
    """
    from config.turn_context_settings import RECALL_MEMORY_TOOL_BUDGET_MS

    return max(0, min(RECALL_MEMORY_TOOL_BUDGET_MS, int(timeout * 500)))


def _upstream_error(*, upstream_code: str) -> ServerDomainError:
    return ServerDomainError(
        code="MEMORY_QUERY_UPSTREAM_ERROR",
        message="Memory server reported a recall failure",
        status_code=502,
        details={"upstream_code": upstream_code},
    )


def _extract_results(payload: object) -> list[Mapping[str, object]]:
    """Validate a ``/query_memory`` response and return its hits.

    The endpoint answers 200 even when recall itself failed, flagging it with
    ``error_code`` and an empty list. Treating that as "no memories" is the
    silent failure this service used to have, so it is surfaced as an error.
    """
    if not isinstance(payload, Mapping):
        raise _upstream_error(upstream_code="invalid_response")
    error_code = payload.get("error_code")
    if error_code is not None:
        raise _upstream_error(upstream_code=str(error_code))
    results = payload.get("results")
    if not isinstance(results, list):
        raise _upstream_error(upstream_code="invalid_response")
    # Every hit must be an object. Dropping malformed ones would turn a broken
    # response into an ordinary "no memories" answer, the same silent failure.
    if not all(isinstance(item, Mapping) for item in results):
        raise _upstream_error(upstream_code="invalid_response")
    return results


def _decode_upstream_json(response) -> object:
    """Decode the recall response body; a malformed body is an upstream fault.

    Kept local to the upstream response so a local rendering error is never
    reported as the memory server's fault.
    """
    try:
        return response.json()
    except ValueError as exc:
        raise _upstream_error(upstream_code="invalid_json") from exc


def _render_results(results: list[Mapping[str, object]], lang: object) -> str:
    """Render hits through the repo's single recall renderer.

    Synchronous on purpose: token counting can be slow on long entries, so the
    caller runs this in a worker thread.
    """
    from config.prompts.prompts_memory import (
        RECALL_MEMORY_TOOL_FOUND_HEADER,
        _loc,
        _normalize_memory_prompt_lang,
    )
    from memory.recall_render import render_recall_block

    normalized_lang = _normalize_memory_prompt_lang(lang if isinstance(lang, str) else None)
    block = render_recall_block(
        results,
        normalized_lang,
        header_template=_loc(RECALL_MEMORY_TOOL_FOUND_HEADER, normalized_lang),
    )
    # Hits whose text is empty are skipped by the renderer, which still emits
    # the header. A lone "found 0 memories" header is not a memory: keep the
    # documented empty-string contract for that case.
    if block.kept == 0:
        return ""
    return block.text


def _deadline_seconds(timeout: float) -> float:
    """Deadline for the whole call, a little inside the caller's timeout.

    The caller waits on the same timeout, so finishing right at it leaves no
    time to deliver the reply: the plugin would see a bare RPC timeout instead
    of the mapped ``MEMORY_QUERY_TIMEOUT``. Keep a small margin for the reply.
    """
    return timeout - min(DEADLINE_REPLY_MARGIN_SECONDS, timeout * 0.1)


def _locale_lookup_seconds(remaining: float) -> float:
    """Time the locale lookup may take out of what is left of the deadline.

    The lookup gets its own, shorter limit so a stalled lookup ends while
    there is still time to render the hits already in hand; the rest of the
    remaining time is kept for rendering and the reply.
    """
    return max(0.0, remaining * LOCALE_LOOKUP_SHARE)


async def _fetch_render_language(
    client, httpx, *, lanlan_name: str, timeout: float,
) -> object:
    """Read the memory server's resolved locale for rendering, failing soft.

    The recall has already succeeded when this runs. A locale lookup failure,
    including running out of its own ``timeout``, must not throw those hits
    away, mirroring the memory server's own resolver, which degrades instead
    of failing on an unreadable locale sidecar. ``None`` renders with the
    memory-prompt default locale.
    """
    try:
        # Only this inner limit is turned into a fallback. If the caller's
        # overall deadline fires instead, the task is cancelled and that
        # surfaces as the call's own timeout, not as a locale fallback.
        async with asyncio.timeout(timeout):
            response = await client.get(_build_prompt_locale_url(lanlan_name=lanlan_name))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
        logger.warning(
            "query_memory locale lookup failed, rendering with default locale: lanlan_name={}, err_type={}",
            lanlan_name,
            type(exc).__name__,
        )
        return None
    return payload.get("effective_language") if isinstance(payload, Mapping) else None


class MemoryQueryService:
    async def query_memory(
        self,
        *,
        lanlan_name: object,
        query: object,
        timeout: object,
    ) -> dict[str, object]:
        """Recall memories for ``query`` and return them as prompt-ready text.

        Returns ``{"result": text}``. ``text`` is a numbered, token-budgeted
        block of hits, or an empty string when nothing relevant was found.
        Plugins that need structured hits should use ``MemoryClient.search``.
        """
        normalized_lanlan_name = _normalize_non_empty_str(lanlan_name, field="lanlan_name")
        normalized_query = _normalize_non_empty_str(query, field="query")
        normalized_timeout = _coerce_timeout(timeout)
        query_len = len(normalized_query)
        httpx = await ensure_httpx()
        # Imported here, not at module level: the adapter imports httpx, and
        # the plugin server keeps httpx out of its startup import graph.
        from plugin.server.application.memory import memory_request_slot

        try:
            # No "language" field: the plugin server has no session locale to
            # contribute, and its process-level fallback can disagree with the
            # memory server's own resolution. The memory server resolves the
            # character locale for recall, and the same resolution is read back
            # from /prompt-locale for rendering.
            body = {
                "query": normalized_query,
                "budget_ms": _recall_budget_ms(normalized_timeout),
            }
            # One deadline for the whole call. The recall, the locale lookup
            # and the render each have their own wait; without a shared cap
            # their sum can outlive the caller's timeout, and the work keeps
            # running after the plugin has already given up on it.
            deadline = _deadline_seconds(normalized_timeout)
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(deadline) as call_scope:
                # Recall is expensive on the memory server (query embedding
                # plus a full candidate scan). Share the cap MemoryClient.search
                # uses, so this entry point is not an unthrottled bypass of it;
                # at the cap the call fails fast with BUSY instead of queueing.
                # Rendering is local work and runs after the slot is released.
                with memory_request_slot():
                    async with httpx.AsyncClient(timeout=deadline, proxy=None, trust_env=False) as client:
                        response = await client.post(
                            _build_memory_query_url(lanlan_name=normalized_lanlan_name),
                            json=body,
                        )
                        response.raise_for_status()
                        results = _extract_results(_decode_upstream_json(response))
                        if not results:
                            return {"result": ""}
                        remaining = call_scope.when() - loop.time()
                        lang = await _fetch_render_language(
                            client,
                            httpx,
                            lanlan_name=normalized_lanlan_name,
                            timeout=_locale_lookup_seconds(remaining),
                        )
                text = await asyncio.to_thread(_render_results, results, lang)
            return {"result": text}
        except ServerDomainError as exc:
            logger.warning(
                "query_memory failed with domain error: code={}, lanlan_name={}, query_len={}, details={}",
                exc.code,
                normalized_lanlan_name,
                query_len,
                exc.details,
            )
            raise
        # TimeoutError (from the overall deadline) subclasses OSError, so it
        # must be caught here, before the generic OSError branch below.
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.warning(
                "query_memory timeout: lanlan_name={}, query_len={}, timeout={}",
                normalized_lanlan_name,
                query_len,
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
                "query_memory upstream status error: status_code={}, lanlan_name={}, query_len={}",
                status_code,
                normalized_lanlan_name,
                query_len,
            )
            if status_code == 503:
                raise ServerDomainError(
                    code="MEMORY_QUERY_NOT_READY",
                    message="Memory server is not ready yet",
                    status_code=503,
                    details={"upstream_status": status_code},
                ) from exc
            raise ServerDomainError(
                code="MEMORY_QUERY_UPSTREAM_ERROR",
                message="Memory server returned an error response",
                status_code=502,
                details={"upstream_status": status_code},
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "query_memory connection error: lanlan_name={}, query_len={}, err_type={}, err={}",
                normalized_lanlan_name,
                query_len,
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
                "query_memory failed: lanlan_name={}, query_len={}, err_type={}, err={}",
                normalized_lanlan_name,
                query_len,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="MEMORY_QUERY_FAILED",
                message="Failed to query memory server",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc
