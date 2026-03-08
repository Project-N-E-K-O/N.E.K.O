from __future__ import annotations

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.bus.mutation")


class BusMutationService:
    def delete_message(self, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="message_id is required",
                status_code=400,
                details={},
            )
        try:
            return bool(state.delete_message(message_id))
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "delete_message failed: message_id={}, err_type={}, err={}",
                message_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="MESSAGE_DELETE_FAILED",
                message="Failed to delete message",
                status_code=500,
                details={"message_id": message_id, "error_type": type(exc).__name__},
            ) from exc

    def delete_event(self, event_id: str) -> bool:
        if not isinstance(event_id, str) or not event_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="event_id is required",
                status_code=400,
                details={},
            )
        try:
            return bool(state.delete_event(event_id))
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "delete_event failed: event_id={}, err_type={}, err={}",
                event_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="EVENT_DELETE_FAILED",
                message="Failed to delete event",
                status_code=500,
                details={"event_id": event_id, "error_type": type(exc).__name__},
            ) from exc

    def delete_lifecycle(self, lifecycle_id: str) -> bool:
        if not isinstance(lifecycle_id, str) or not lifecycle_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="lifecycle_id is required",
                status_code=400,
                details={},
            )
        try:
            return bool(state.delete_lifecycle(lifecycle_id))
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "delete_lifecycle failed: lifecycle_id={}, err_type={}, err={}",
                lifecycle_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="LIFECYCLE_DELETE_FAILED",
                message="Failed to delete lifecycle record",
                status_code=500,
                details={"lifecycle_id": lifecycle_id, "error_type": type(exc).__name__},
            ) from exc
