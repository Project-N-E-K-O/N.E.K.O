"""
Unified error-code definitions.

Combines sdk/errors.py and typedefs/result.py definitions.
Provides HTTP-aligned IntEnum error codes and string aliases.

Usage:
    from plugin._types import ErrorCode, ERROR_NAMES
    
    # Use an error code.
    code = ErrorCode.VALIDATION_ERROR  # 400
    
    # Get its name.
    name = ERROR_NAMES[code]  # \"VALIDATION_ERROR\"
    
    # Use in a response.
    return fail(ErrorCode.NOT_FOUND, \"资源不存在\")
"""
from __future__ import annotations

from enum import IntEnum
from typing import Dict


class ErrorCode(IntEnum):
    """Unified error-code enumeration.
    
    Aligned with HTTP status codes for API responses.
    
    Categories:
    - 0: Success
    - 4xx: Client errors
    - 5xx: Server errors
    - 1xxx: Plugin-specific errors
    
    Attributes:
        SUCCESS: Success (0)
        VALIDATION_ERROR: Parameter validation failed (400)
        UNAUTHORIZED: Unauthorized (401)
        FORBIDDEN: Forbidden (403)
        NOT_FOUND: Resource not found (404)
        CONFLICT: Resource conflict (409)
        RATE_LIMITED: Rate limited (429)
        INTERNAL: Internal error (500)
        NOT_IMPLEMENTED: Not implemented (501)
        DEPENDENCY_MISSING: Missing dependency (502)
        NOT_READY: Service not ready (503)
        TIMEOUT: Timeout (504)
        INVALID_RESPONSE: Invalid response format (422)
        PLUGIN_NOT_RUNNING: Plugin not running (1001)
        PLUGIN_CRASHED: Plugin crashed (1002)
        CIRCULAR_CALL: Circular call (1003)
        PLUGIN_RATE_LIMITED: Plugin rate limited (1004)
    """
    # Success.
    SUCCESS = 0
    
    # Client errors (4xx).
    VALIDATION_ERROR = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    INVALID_RESPONSE = 422
    RATE_LIMITED = 429

    # Server errors (5xx).
    INTERNAL = 500
    NOT_IMPLEMENTED = 501
    DEPENDENCY_MISSING = 502
    NOT_READY = 503
    TIMEOUT = 504
    
    # Plugin-specific errors (1xxx).
    PLUGIN_NOT_RUNNING = 1001
    PLUGIN_CRASHED = 1002
    CIRCULAR_CALL = 1003
    PLUGIN_RATE_LIMITED = 1004
    
    @classmethod
    def from_string(cls, name: str) -> "ErrorCode":
        """Get error code from string name.
        
        Args:
            name: Error-code name (e.g. \"VALIDATION_ERROR\")
        
        Returns:
            Corresponding ErrorCode
        
        Raises:
            ValueError: Invalid name
        """
        name_upper = name.upper()
        try:
            return cls[name_upper]
        except KeyError as err:
            raise ValueError(f"Unknown error code: {name}") from err


# Error-code name map for serialization.
ERROR_NAMES: Dict[ErrorCode, str] = {member: member.name for member in ErrorCode}


def get_error_name(code: ErrorCode) -> str:
    """Get string name for an error code."""
    return ERROR_NAMES.get(code, code.name)


def get_http_status(code: ErrorCode) -> int:
    """Get HTTP status corresponding to error code.
    
    Plugin-specific errors (1xxx) return 500.
    """
    if code.value >= 1000:
        return 500
    return code.value if code.value > 0 else 200


__all__ = [
    "ErrorCode",
    "ERROR_NAMES",
    "get_error_name",
    "get_http_status",
]
