"""Common runtime exports shared by all sdk_v2 facades.

This module centralizes the SDK-wide runtime vocabulary used by
`plugin.runtime`, `extension.runtime`, and `adapter.runtime`:
- version and error code
- `Result`/`Ok`/`Err` and helpers
- logger helpers
- call-chain helpers

Keeping these symbols in one place reduces drift between facade runtimes and
makes their differences easier to reason about.
"""

from __future__ import annotations

from plugin.sdk_v2.shared.logging import (
    LogLevel,
    LoggerLike,
    build_component_name,
    configure_sdk_default_logger,
    format_log_text,
    get_sdk_logger,
    intercept_standard_logging,
    setup_sdk_logging,
)
from plugin.sdk_v2.shared.models import (
    AuthorizationError,
    CapabilityUnavailableError,
    Err,
    InvalidArgumentError,
    Ok,
    Result,
    ResultError,
    SdkError,
    TransportError,
    bind_result,
    capture,
    is_err,
    is_ok,
    map_err_result,
    map_result,
    match_result,
    must,
    raise_for_err,
    unwrap,
    unwrap_or,
)
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.constants import SDK_VERSION
from plugin.sdk_v2.shared.runtime.call_chain import (
    AsyncCallChain,
    CallChain,
    CallChainTooDeepError,
    CircularCallError,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)

__all__ = [
    "SDK_VERSION",
    "LogLevel",
    "build_component_name",
    "LoggerLike",
    "get_sdk_logger",
    "setup_sdk_logging",
    "configure_sdk_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "ErrorCode",
    "SdkError",
    "InvalidArgumentError",
    "CapabilityUnavailableError",
    "AuthorizationError",
    "TransportError",
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "is_ok",
    "is_err",
    "map_result",
    "map_err_result",
    "bind_result",
    "match_result",
    "unwrap",
    "unwrap_or",
    "raise_for_err",
    "must",
    "capture",
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]
