from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from loguru import logger
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

from .protocol import PROTOCOL_VERSION, RpcEnvelope


_ENVELOPE_ADAPTER: TypeAdapter[RpcEnvelope] = TypeAdapter(RpcEnvelope)


def validate_rpc_envelope(
    req: Any,
    *,
    mode: str,
) -> Tuple[Optional[RpcEnvelope], Optional[str]]:
    if mode == "off":
        return None, None

    # Progressive freeze: in warn mode, tolerate legacy clients that omitted protocol version.
    # We normalize the request before validation to avoid changing the authoritative schema.
    if mode == "warn" and isinstance(req, dict) and "v" not in req:
        try:
            req = dict(req)
            req["v"] = PROTOCOL_VERSION
        except Exception:
            pass

    try:
        env = _ENVELOPE_ADAPTER.validate_python(req)
    except ValidationError as e:
        if mode == "warn":
            logger.warning("invalid rpc envelope: {}", e)
        return None, "invalid rpc envelope"

    if env.v is not None and env.v != PROTOCOL_VERSION:
        return None, f"unsupported protocol version: {env.v!r}"

    if mode == "strict":
        if env.v is None:
            return None, "missing protocol version"

    return env, None
