from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from plugin.core.state import state
from plugin.server.services import trigger_plugin


RunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "timeout",
    "cancel_requested",
]


ExportType = Literal["text", "url", "binary_url", "binary"]


class RunCreateRequest(BaseModel):
    plugin_id: str
    entry_id: str
    args: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunCancelRequest(BaseModel):
    reason: Optional[str] = None


class RunError(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ExportItem(BaseModel):
    export_item_id: str
    run_id: str
    type: ExportType
    created_at: float
    description: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    binary_url: Optional[str] = None
    binary: Optional[str] = None
    mime: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExportListResponse(BaseModel):
    items: List[ExportItem]
    next_after: Optional[str] = None


class RunRecord(BaseModel):
    run_id: str
    plugin_id: str
    entry_id: str
    status: RunStatus
    created_at: float
    updated_at: float

    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    progress: Optional[float] = None

    cancel_requested: bool = False
    cancel_reason: Optional[str] = None
    cancel_requested_at: Optional[float] = None

    error: Optional[RunError] = None
    result_refs: List[str] = Field(default_factory=list)


class ExportStore(Protocol):
    def append(self, item: ExportItem) -> None: ...

    def list_for_run(
        self, *, run_id: str, after: Optional[str], limit: int
    ) -> Tuple[List[ExportItem], Optional[str]]: ...


class RunStore(Protocol):
    def create(self, rec: RunRecord) -> None: ...

    def get(self, run_id: str) -> Optional[RunRecord]: ...

    def update(self, run_id: str, **patch: Any) -> Optional[RunRecord]: ...

    def commit_terminal(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: Optional[RunError],
        result_refs: List[str],
    ) -> Optional[RunRecord]: ...


class InMemoryExportStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_run: Dict[str, List[str]] = {}
        self._items: Dict[str, ExportItem] = {}

    def append(self, item: ExportItem) -> None:
        with self._lock:
            self._items[item.export_item_id] = item
            self._by_run.setdefault(item.run_id, []).append(item.export_item_id)

    def list_for_run(self, *, run_id: str, after: Optional[str], limit: int) -> Tuple[List[ExportItem], Optional[str]]:
        with self._lock:
            ids = list(self._by_run.get(run_id, []))
            start = 0
            if after:
                try:
                    start = ids.index(after) + 1
                except ValueError:
                    start = 0
            slice_ids = ids[start : start + max(1, int(limit))]
            items = [self._items[i] for i in slice_ids if i in self._items]
            next_after = None
            if start + len(slice_ids) < len(ids) and slice_ids:
                next_after = slice_ids[-1]
            return items, next_after


class InMemoryRunStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, RunRecord] = {}

    def create(self, rec: RunRecord) -> None:
        with self._lock:
            self._runs[rec.run_id] = rec

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            r = self._runs.get(run_id)
            return r.model_copy(deep=True) if r is not None else None

    def update(self, run_id: str, **patch: Any) -> Optional[RunRecord]:
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return None
            if r.status in ("succeeded", "failed", "canceled", "timeout"):
                return r.model_copy(deep=True)
            data = r.model_dump()
            data.update(patch)
            data["updated_at"] = float(time.time())
            nr = RunRecord.model_validate(data)
            self._runs[run_id] = nr
            return nr.model_copy(deep=True)

    def commit_terminal(self, run_id: str, *, status: RunStatus, error: Optional[RunError], result_refs: List[str]) -> Optional[RunRecord]:
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return None
            if r.status in ("succeeded", "failed", "canceled", "timeout"):
                return r.model_copy(deep=True)
            now = float(time.time())
            data = r.model_dump()
            data.update(
                {
                    "status": status,
                    "finished_at": now,
                    "updated_at": now,
                    "error": error.model_dump() if isinstance(error, RunError) else None,
                    "result_refs": list(result_refs or []),
                }
            )
            nr = RunRecord.model_validate(data)
            self._runs[run_id] = nr
            return nr.model_copy(deep=True)


_run_store: RunStore = InMemoryRunStore()
_export_store: ExportStore = InMemoryExportStore()


def set_run_store(store: RunStore) -> None:
    global _run_store
    _run_store = store


def set_export_store(store: ExportStore) -> None:
    global _export_store
    _export_store = store


def _emit_runs(op: str, rec: RunRecord) -> None:
    try:
        rev = state._bump_bus_rev("runs")
    except Exception:
        rev = None
    payload: Dict[str, Any] = {
        "run_id": rec.run_id,
        "status": rec.status,
        "progress": rec.progress,
        "updated_at": rec.updated_at,
        "rev": rev,
    }
    try:
        if rec.task_id:
            payload["task_id"] = rec.task_id
        payload["plugin_id"] = rec.plugin_id
        payload["entry_id"] = rec.entry_id
    except Exception:
        pass
    state.bus_change_hub.emit("runs", str(op), payload)


def _emit_export(op: str, item: ExportItem) -> None:
    try:
        rev = state._bump_bus_rev("export")
    except Exception:
        rev = None
    payload: Dict[str, Any] = {
        "run_id": item.run_id,
        "export_item_id": item.export_item_id,
        "type": item.type,
        "created_at": item.created_at,
        "rev": rev,
    }
    state.bus_change_hub.emit("export", str(op), payload)


def get_run(run_id: str) -> Optional[RunRecord]:
    return _run_store.get(str(run_id))


def list_export_for_run(*, run_id: str, after: Optional[str], limit: int) -> ExportListResponse:
    items, next_after = _export_store.list_for_run(run_id=str(run_id), after=after, limit=int(limit))
    return ExportListResponse(items=items, next_after=next_after)


def append_export_item(item: ExportItem) -> None:
    _export_store.append(item)
    _emit_export("add", item)


def cancel_run(run_id: str, *, reason: Optional[str]) -> Optional[RunRecord]:
    rid = str(run_id)
    now = float(time.time())
    rec = _run_store.get(rid)
    if rec is None:
        return None
    if rec.status == "queued":
        updated = _run_store.commit_terminal(rid, status="canceled", error=RunError(code="CANCELED", message="canceled"), result_refs=[])
        if updated is not None:
            _emit_runs("change", updated)
        return updated
    if rec.status in ("running", "cancel_requested"):
        updated = _run_store.update(
            rid,
            cancel_requested=True,
            cancel_reason=str(reason) if isinstance(reason, str) and reason else rec.cancel_reason,
            cancel_requested_at=now,
            status="cancel_requested",
        )
        if updated is not None:
            _emit_runs("change", updated)
        return updated
    return rec


async def create_run(req: RunCreateRequest, *, client_host: Optional[str]) -> RunCreateResponse:
    run_id = str(uuid.uuid4())
    now = float(time.time())
    rec = RunRecord(
        run_id=run_id,
        plugin_id=req.plugin_id,
        entry_id=req.entry_id,
        status="queued",
        created_at=now,
        updated_at=now,
        task_id=req.task_id,
        trace_id=req.trace_id,
        idempotency_key=req.idempotency_key,
        cancel_requested=False,
        result_refs=[],
    )
    _run_store.create(rec)
    _emit_runs("add", rec)

    async def _runner() -> None:
        started = _run_store.update(run_id, status="running", started_at=float(time.time()))
        if started is None:
            return
        _emit_runs("change", started)

        if started.cancel_requested:
            term = _run_store.commit_terminal(
                run_id,
                status="canceled",
                error=RunError(code="CANCELED", message="canceled"),
                result_refs=[],
            )
            if term is not None:
                _emit_runs("change", term)
            return

        try:
            trigger_args = dict(req.args or {})
            try:
                ctx_obj = trigger_args.get("_ctx")
                if not isinstance(ctx_obj, dict):
                    ctx_obj = {}
                else:
                    ctx_obj = dict(ctx_obj)
                if "run_id" not in ctx_obj:
                    ctx_obj["run_id"] = run_id
                trigger_args["_ctx"] = ctx_obj
            except Exception:
                pass

            resp = await trigger_plugin(
                plugin_id=req.plugin_id,
                entry_id=req.entry_id,
                args=trigger_args,
                task_id=req.task_id,
                client_host=client_host,
            )
            payload = resp.model_dump() if hasattr(resp, "model_dump") else json.loads(json.dumps(resp, default=str))
            text = json.dumps(payload, ensure_ascii=False)
            export_item_id = str(uuid.uuid4())
            item = ExportItem(
                export_item_id=export_item_id,
                run_id=run_id,
                type="text",
                created_at=float(time.time()),
                description="trigger_plugin response",
                text=text,
                metadata={"kind": "trigger_response"},
            )
            _export_store.append(item)
            _emit_export("add", item)

            ok = bool(getattr(resp, "success", False)) if resp is not None else False
            if ok:
                term = _run_store.commit_terminal(run_id, status="succeeded", error=None, result_refs=[export_item_id])
            else:
                term = _run_store.commit_terminal(
                    run_id,
                    status="failed",
                    error=RunError(code="PLUGIN_ERROR", message="plugin returned failure"),
                    result_refs=[export_item_id],
                )
            if term is not None:
                _emit_runs("change", term)
        except Exception as e:
            term = _run_store.commit_terminal(
                run_id,
                status="failed",
                error=RunError(code="INTERNAL", message=str(e)),
                result_refs=[],
            )
            if term is not None:
                _emit_runs("change", term)

    asyncio.create_task(_runner(), name=f"run:{run_id}")
    return RunCreateResponse(run_id=run_id, status="queued")
