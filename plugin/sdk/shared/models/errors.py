"""SDK v2 error codes."""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    SUCCESS = 0
    VALIDATION_ERROR = 400
    NOT_FOUND = 404
    TIMEOUT = 408
    CONFLICT = 409
    INTERNAL = 500


__all__ = ["ErrorCode"]
