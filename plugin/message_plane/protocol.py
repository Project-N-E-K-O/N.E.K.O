from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


PROTOCOL_VERSION = 1


RpcOp = Literal[
    "ping",
    "health",
    "bus.list_topics",
    "bus.publish",
    "bus.get_recent",
    "bus.query",
    "bus.conversations.get",
    "bus.conversations.get_message",
]


class RpcError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=0, max_length=256)
    details: Optional[Dict[str, Any]] = None


class RpcResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int = Field(ge=1)
    req_id: str = Field(min_length=1, max_length=64)
    ok: bool
    result: Optional[Any] = None
    error: Optional[Union[str, RpcError]] = None


class RpcEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int = Field(ge=1)
    op: RpcOp
    req_id: str = Field(min_length=1, max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)
    from_plugin: Optional[str] = Field(default=None, max_length=128)


class BusGetRecentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str = Field(min_length=1, max_length=32)
    topic: str = Field(min_length=1, max_length=128)
    limit: int = Field(ge=1, le=10000)
    light: bool = False


class BusGetRecentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str
    topic: str
    items: List[Dict[str, Any]]
    light: bool


class BusQueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str = Field(min_length=1, max_length=32)
    topic: str = Field(min_length=1, max_length=128)
    limit: int = Field(ge=1, le=10000)
    light: bool = False

    plugin_id: Optional[str] = Field(default=None, max_length=128)
    source: Optional[str] = Field(default=None, max_length=128)
    kind: Optional[str] = Field(default=None, max_length=64)
    type: Optional[str] = Field(default=None, max_length=64)
    # ``conversation_id`` is not a payload top-level field like its neighbours
    # here: the writer puts it in ``metadata`` and TopicStore._extract_index
    # projects it into the index. It is declared as a filter because
    # ConversationClient.get_by_id sends it -- and this model is extra="forbid",
    # so an undeclared field is not "ignored", it is a BAD_ARGS rejection.
    conversation_id: Optional[str] = Field(default=None, max_length=128)
    priority_min: Optional[int] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None


class BusQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str
    topic: str
    items: List[Dict[str, Any]]
    light: bool


class ConversationGetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    character: Optional[str] = Field(default=None, min_length=1, max_length=512)
    role: Optional[Literal["user", "assistant", "instruction", "unknown"]] = None
    kind: Optional[str] = Field(default=None, min_length=1, max_length=512)
    input_source: Optional[Literal["text", "voice", "unknown"]] = None
    origin_kind: Optional[str] = Field(default=None, min_length=1, max_length=512)
    max_count: int = Field(default=50, ge=1, le=2**63 - 1)
    order: Literal["asc", "desc"] = "asc"
    since_ts: Optional[float] = None

    @field_validator("character", "kind", "origin_kind")
    @classmethod
    def nonblank_filter(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("filter must not be blank")
        return value


class ConversationGetMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: str = Field(min_length=1, max_length=256)

    @field_validator("message_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message_id must not be blank")
        return value


class IngestDeltaItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: Optional[str] = Field(default=None, max_length=32)
    bus: Optional[str] = Field(default=None, max_length=32)
    topic: str = Field(min_length=1, max_length=128)
    payload: Dict[str, Any]


class IngestDeltaBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int = Field(ge=1)
    kind: Literal["delta_batch"] = "delta_batch"
    items: List[IngestDeltaItem]


class IngestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int = Field(ge=1)
    kind: Literal["snapshot"] = "snapshot"
    store: Optional[str] = Field(default=None, max_length=32)
    bus: Optional[str] = Field(default=None, max_length=32)
    topic: str = Field(min_length=1, max_length=128)
    mode: Optional[str] = Field(default=None, max_length=16)
    items: List[Dict[str, Any]]


@dataclass(frozen=True)
class RpcRequest:
    v: int
    op: str
    req_id: str
    args: Dict[str, Any]
    from_plugin: Optional[str] = None


def ok_response(req_id: str, result: Any) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "req_id": req_id, "ok": True, "result": result, "error": None}


def err_response(
    req_id: str,
    error: Any,
    *,
    code: str = "ERR",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Progressive freeze: keep accepting string error payloads from legacy call sites,
    # but standardize new errors to structured {code,message,details}.
    if isinstance(error, dict) and ("code" in error or "message" in error):
        err_obj: Any = error
    else:
        err_obj = {"code": str(code or "ERR"), "message": str(error), "details": details}
    return {"v": PROTOCOL_VERSION, "req_id": req_id, "ok": False, "result": None, "error": err_obj}


def export_json_schemas() -> Dict[str, Any]:
    return {
        "rpc_envelope": RpcEnvelope.model_json_schema(),
        "rpc_response": RpcResponse.model_json_schema(),
        "rpc_error": RpcError.model_json_schema(),
        "bus_get_recent_args": BusGetRecentArgs.model_json_schema(),
        "bus_get_recent_result": BusGetRecentResult.model_json_schema(),
        "conversation_get_args": ConversationGetArgs.model_json_schema(),
        "conversation_get_message_args": ConversationGetMessageArgs.model_json_schema(),
        "bus_query_args": BusQueryArgs.model_json_schema(),
        "bus_query_result": BusQueryResult.model_json_schema(),
        "ingest_delta_batch": IngestDeltaBatch.model_json_schema(),
        "ingest_snapshot": IngestSnapshot.model_json_schema(),
    }
