from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.type_adapter import TypeAdapter

from .protocol import PROTOCOL_VERSION


class _RpcEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    v: Optional[int] = None
    op: str = Field(min_length=1, max_length=128)
    req_id: str = Field(min_length=0, max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)


_ENVELOPE_ADAPTER: TypeAdapter[_RpcEnvelope] = TypeAdapter(_RpcEnvelope)


def validate_rpc_envelope(
    req: Any,
    *,
    mode: str,
) -> Tuple[Optional[_RpcEnvelope], Optional[str]]:
    if mode == "off":
        return None, None

    try:
        env = _ENVELOPE_ADAPTER.validate_python(req)
    except ValidationError as e:
        if mode == "warn":
            logger.warning("[message_plane] invalid rpc envelope: {}", e)
        return None, "invalid rpc envelope"

    if env.v is not None and env.v != PROTOCOL_VERSION:
        return None, f"unsupported protocol version: {env.v!r}"

    return env, None
