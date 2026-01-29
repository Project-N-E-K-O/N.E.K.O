from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


PROTOCOL_VERSION = 1


RpcOp = Literal[
    "ping",
    "health",
    "bus.list_topics",
    "bus.publish",
    "bus.get_recent",
    "bus.get_since",
    "bus.query",
    "bus.replay",
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
    priority_min: Optional[int] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None


class BusQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str
    topic: str
    items: List[Dict[str, Any]]
    light: bool


class BusReplayArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str = Field(min_length=1, max_length=32)
    plan: Dict[str, Any]
    limit: Optional[int] = Field(default=None, ge=1, le=10000)
    topic: Optional[str] = Field(default=None, max_length=128)
    light: Optional[bool] = None


class BusReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    store: str
    topic: Optional[str] = None
    items: List[Dict[str, Any]]
    diag: Optional[Dict[str, Any]] = None


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
        "bus_query_args": BusQueryArgs.model_json_schema(),
        "bus_query_result": BusQueryResult.model_json_schema(),
        "bus_replay_args": BusReplayArgs.model_json_schema(),
        "bus_replay_result": BusReplayResult.model_json_schema(),
        "ingest_delta_batch": IngestDeltaBatch.model_json_schema(),
        "ingest_snapshot": IngestSnapshot.model_json_schema(),
    }
