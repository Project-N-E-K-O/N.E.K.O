from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.live_vision_service import LiveVisionQueryService
from plugin.server.messaging.handlers.common import coerce_bool, resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.live_vision")
live_vision_query_service = LiveVisionQueryService()
_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)


async def handle_live_vision_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields
    try:
        payload = await live_vision_query_service.get_live_vision(
            role=request.get("role"),
            include_frame=coerce_bool(request.get("include_frame")),
            timeout=timeout,
        )
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except _RUNTIME_ERRORS as error:
        # The service already swallows transport failures into an inactive
        # answer, so reaching here means something unexpected. Still answer
        # rather than error out: a probe on a timer must not make the caller
        # handle exceptions for a question that has a safe default.
        logger.error(
            "LIVE_VISION_GET unexpected failure: err_type={}, err={}",
            type(error).__name__,
            str(error),
        )
        send_response(
            from_plugin,
            request_id,
            {"active": False, "source": "", "age_seconds": None, "native_vision": False, "role": ""},
            None,
            timeout=timeout,
        )
