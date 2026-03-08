"""Shared SDK v2 model primitives."""

from .result import (
    Err,
    Ok,
    Result,
    ResultError,
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

__all__ = [
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "is_ok",
    "is_err",
    "map_result",
    "map_err_result",
    "bind_result",
    "unwrap",
    "unwrap_or",
    "raise_for_err",
    "must",
    "match_result",
    "capture",
]
