from __future__ import annotations

import asyncio
import collections
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from loguru import logger

from plugin.core.state import state
from plugin._types.models import RunCreateRequest, RunCreateResponse, RunStatus
from plugin.server.services import trigger_plugin
from plugin.server.messaging.plane_bridge import publish_record as _publish_record_impl
from plugin.settings import RUN_EXECUTION_TIMEOUT, RUN_STORE_MAX_COMPLETED


ExportType = Literal["text", "json", "url", "binary_url", "binary"]
ExportCategory = Literal["system", "user"]


class RunCancelRequest(BaseModel):
    reason: Optional[str] = None


class RunError(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ExportItem(BaseModel):
    model_config = {"populate_by_name": True}

    export_item_id: str
    run_id: str
    type: ExportType
    category: ExportCategory = "user"
    created_at: float
    label: Optional[str] = None
    description: Optional[str] = None
    text: Optional[str] = None
    json_data: Optional[Dict[str, Any]] = Field(default=None, alias="json")
    url: Optional[str] = None
    binary_url: Optional[str] = None
    binary: Optional[str] = None
    mime: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExportListResponse(BaseModel):
    items: List[ExportItem]
    next_after: Optional[str] = None


class RunRecord(BaseModel):
    # ── identity ──
    run_id: str
    plugin_id: str
    entry_id: str
    status: RunStatus
    created_at: float
    updated_at: float

    # ── correlation ──
    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    # ── lifecycle timestamps ──
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    # ── progress (meaningful only while status in {running, cancel_requested}) ──
    progress: Optional[float] = None
    stage: Optional[str] = None
    message: Optional[str] = None
    step: Optional[int] = None
    step_total: Optional[int] = None
    eta_seconds: Optional[float] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)

    # ── cancellation ──
    cancel_requested: bool = False
    cancel_reason: Optional[str] = None
    cancel_requested_at: Optional[float] = None

    # ── result ──
    error: Optional[RunError] = None
    result_refs: List[str] = Field(default_factory=list)


class ExportStore(Protocol):
    def append(self, item: ExportItem) -> None: ...

    def list_for_run(
        self, *, run_id: str, after: Optional[str], limit: int,
        category: Optional[ExportCategory] = None,
    ) -> Tuple[List[ExportItem], Optional[str]]: ...

    def remove_for_run(self, run_id: str) -> None: ...


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

    def list_for_run(
        self, *, run_id: str, after: Optional[str], limit: int,
        category: Optional[ExportCategory] = None,
    ) -> Tuple[List[ExportItem], Optional[str]]:
        with self._lock:
            ids = self._by_run.get(run_id, [])
            start = 0
            if after:
                try:
                    start = ids.index(after) + 1
                except ValueError:
                    start = 0
            # Apply category filter before pagination
            if category is not None:
                ids = [i for i in ids[start:] if i in self._items and self._items[i].category == category]
                start = 0
            page_size = max(1, int(limit))
            slice_ids = ids[start : start + page_size]
            items = [self._items[i] for i in slice_ids if i in self._items]
            next_after = None
            if start + len(slice_ids) < len(ids) and slice_ids:
                next_after = slice_ids[-1]
            return items, next_after

    def remove_for_run(self, run_id: str) -> None:
        with self._lock:
            ids = self._by_run.pop(run_id, [])
            for eid in ids:
                self._items.pop(eid, None)


_TERMINAL_STATUSES = frozenset(("succeeded", "failed", "canceled", "timeout"))


class InvalidRunTransition(RuntimeError):
    """Raised when an illegal Run status transition is attempted."""
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid transition: {current} -> {target}")


_ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    "queued":           frozenset(("running", "canceled")),
    "running":          frozenset(("succeeded", "failed", "timeout", "cancel_requested")),
    "cancel_requested": frozenset(("succeeded", "failed", "canceled", "timeout")),
    "succeeded":        frozenset(),
    "failed":           frozenset(),
    "canceled":         frozenset(),
    "timeout":          frozenset(),
}


def validate_run_transition(current: str, target: str) -> None:
    """Raise InvalidRunTransition if *current* → *target* is not a legal transition."""
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise InvalidRunTransition(current, target)


class InMemoryRunStore:
    def __init__(self, max_completed: int = 0) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, RunRecord] = {}
        self._completed_order: collections.deque[str] = collections.deque()
        self._max_completed = max_completed if max_completed > 0 else int(RUN_STORE_MAX_COMPLETED)

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

    def list_runs(self) -> List[RunRecord]:
        with self._lock:
            items = list(self._runs.values())
        out: List[RunRecord] = []
        for r in items:
            try:
                out.append(r.model_copy(deep=True))
            except Exception:
                pass
        return out

    def commit_terminal(self, run_id: str, *, status: RunStatus, error: Optional[RunError], result_refs: List[str]) -> Optional[RunRecord]:
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return None
            if r.status in ("succeeded", "failed", "canceled", "timeout"):
                return r.model_copy(deep=True)
            now = float(time.time())
            data = r.model_dump()

            if status == "succeeded":
                try:
                    pv = data.get("progress")
                    if pv is None or float(pv) < 1.0:
                        data["progress"] = 1.0
                except Exception:
                    data["progress"] = 1.0
                if not (isinstance(data.get("stage"), str) and str(data.get("stage") or "").strip()):
                    data["stage"] = "done"
                if not (isinstance(data.get("message"), str) and str(data.get("message") or "").strip()):
                    data["message"] = "done"

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
            self._completed_order.append(run_id)
            self._evict_completed_locked()
            return nr.model_copy(deep=True)

    def _evict_completed_locked(self) -> None:
        """Evict oldest terminal Runs when exceeding max_completed.

        Uses ``_completed_order`` (a deque appended in commit_terminal order)
        so each eviction is O(1) amortized instead of O(n) full-scan + sort.
        """
        if self._max_completed <= 0:
            return
        while len(self._completed_order) > self._max_completed:
            rid = self._completed_order.popleft()
            r = self._runs.get(rid)
            if r is not None and r.status in _TERMINAL_STATUSES:
                self._runs.pop(rid, None)
                try:
                    _export_store.remove_for_run(rid)
                except Exception:
                    pass
                try:
                    with _runs_emit_lock:
                        _runs_last_emit_at.pop(rid, None)
                except Exception:
                    pass


_run_store: RunStore = InMemoryRunStore()
_export_store: ExportStore = InMemoryExportStore()

_active_run_tasks: Dict[str, asyncio.Task] = {}

# NOTE: threading.Lock (not asyncio.Lock) because update_run_from_plugin is a
# sync function called from both asyncio coroutines and IPC handler threads.
_runs_emit_lock = threading.Lock()
_runs_last_emit_at: Dict[str, float] = {}
_runs_emit_min_interval_s: float = 0.2


def _publish_bus_record(*, store: str, record: Dict[str, Any]) -> None:
    try:
        _publish_record_impl(store=str(store), record=dict(record), topic="all")
    except Exception:
        pass


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
        "source": "runs",
        "kind": "runs",
        "type": str(op),
        "priority": 0,
        "timestamp": rec.updated_at,
        "id": rec.run_id,
        "run_id": rec.run_id,
        "status": rec.status,
        "progress": rec.progress,
        "stage": rec.stage,
        "message": rec.message,
        "step": rec.step,
        "step_total": rec.step_total,
        "eta_seconds": rec.eta_seconds,
        "metrics": rec.metrics,
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
    try:
        _publish_bus_record(store="runs", record=payload)
    except Exception:
        pass
    state.bus_change_hub.emit("runs", str(op), payload)


def _emit_export(op: str, item: ExportItem) -> None:
    try:
        rev = state._bump_bus_rev("export")
    except Exception:
        rev = None
    payload: Dict[str, Any] = {
        "source": "export",
        "kind": "export",
        "type": str(op),
        "priority": 0,
        "timestamp": item.created_at,
        "id": item.export_item_id,
        "run_id": item.run_id,
        "export_item_id": item.export_item_id,
        "export_type": item.type,
        "created_at": item.created_at,
        "rev": rev,
    }
    try:
        r = get_run(item.run_id)
        if r is not None:
            payload["plugin_id"] = r.plugin_id
    except Exception:
        pass
    try:
        _publish_bus_record(store="export", record=payload)
    except Exception:
        pass
    state.bus_change_hub.emit("export", str(op), payload)


def get_run(run_id: str) -> Optional[RunRecord]:
    return _run_store.get(str(run_id))


def list_runs(*, plugin_id: Optional[str] = None) -> List[RunRecord]:
    fn = getattr(_run_store, "list_runs", None)
    if fn is None:
        return []
    try:
        items = fn()
    except Exception:
        return []
    if plugin_id is None:
        return items
    pid = str(plugin_id)
    if not pid:
        return items
    out: List[RunRecord] = []
    for r in items:
        try:
            if r.plugin_id == pid:
                out.append(r)
        except Exception:
            continue
    return out


def list_export_for_run(
    *, run_id: str, after: Optional[str], limit: int,
    category: Optional[ExportCategory] = None,
) -> ExportListResponse:
    items, next_after = _export_store.list_for_run(
        run_id=str(run_id), after=after, limit=int(limit), category=category,
    )
    return ExportListResponse(items=items, next_after=next_after)


def append_export_item(item: ExportItem) -> None:
    _export_store.append(item)
    _emit_export("add", item)


def update_run_from_plugin(*, from_plugin: str, run_id: str, patch: Dict[str, Any]) -> Tuple[Optional[RunRecord], bool]:
    rid = str(run_id).strip()
    rec = _run_store.get(rid)
    if rec is None:
        return None, False
    if str(from_plugin) != rec.plugin_id:
        raise RuntimeError("forbidden")
    if rec.status not in ("running", "cancel_requested"):
        return rec, False

    patch2: Dict[str, Any] = {}

    status = patch.get("status")
    if isinstance(status, str) and status.strip():
        st = status.strip()
        if st != "running":
            raise RuntimeError("invalid status")
        patch2["status"] = st

    if "progress" in patch:
        v = patch.get("progress")
        if v is None:
            patch2["progress"] = None
        else:
            pv = float(v)
            if pv < 0.0 or pv > 1.0:
                raise RuntimeError("invalid progress")
            patch2["progress"] = pv

    for k in ("stage", "message"):
        vv = patch.get(k)
        if vv is None:
            continue
        if isinstance(vv, str):
            if k == "stage" and len(vv) > 128:
                raise RuntimeError("stage too long")
            if k == "message" and len(vv) > 512:
                raise RuntimeError("message too long")
            patch2[k] = vv

    for k in ("step", "step_total"):
        vv = patch.get(k)
        if vv is None:
            continue
        try:
            patch2[k] = int(vv)
        except Exception:
            raise RuntimeError(f"invalid {k}")

    step_v = patch2.get("step")
    step_total_v = patch2.get("step_total")
    if isinstance(step_v, int) and step_v < 0:
        raise RuntimeError("invalid step")
    if isinstance(step_total_v, int) and step_total_v < 0:
        raise RuntimeError("invalid step_total")
    if isinstance(step_v, int) and isinstance(step_total_v, int):
        if step_v > step_total_v:
            raise RuntimeError("invalid step")

    if "eta_seconds" in patch:
        vv = patch.get("eta_seconds")
        if vv is None:
            patch2["eta_seconds"] = None
        else:
            ev = float(vv)
            if ev < 0.0:
                raise RuntimeError("invalid eta_seconds")
            patch2["eta_seconds"] = ev

    metrics = patch.get("metrics")
    if metrics is not None:
        if not isinstance(metrics, dict):
            raise RuntimeError("invalid metrics")
        patch2["metrics"] = dict(metrics)

    if not patch2:
        return rec, False

    updated = _run_store.update(rid, **patch2)
    if updated is None:
        return None, False

    now = float(time.time())
    should_emit = True
    try:
        with _runs_emit_lock:
            last = float(_runs_last_emit_at.get(rid, 0.0))
            if last > 0.0 and (now - last) < float(_runs_emit_min_interval_s):
                should_emit = False
            if should_emit:
                _runs_last_emit_at[rid] = now
    except Exception:
        should_emit = True

    if should_emit:
        _emit_runs("change", updated)
    return updated, True


def cancel_run(run_id: str, *, reason: Optional[str]) -> Optional[RunRecord]:
    rid = str(run_id)
    now = float(time.time())
    rec = _run_store.get(rid)
    if rec is None:
        return None
    if rec.status == "queued":
        # Queued runs haven't started yet — cancel the pending task and
        # commit terminal state immediately.
        task = _active_run_tasks.get(rid)
        if task and not task.done():
            task.cancel()
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
        # Propagate cancellation to the child process so the running entry
        # can be interrupted (async / worker entries).
        _propagate_cancel_to_plugin(rec.plugin_id, rid)
        return updated
    return rec


def _propagate_cancel_to_plugin(plugin_id: str, run_id: str) -> None:
    """Best-effort: send CANCEL_RUN IPC to the plugin's child process."""
    try:
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=0.5)
        host = hosts_snapshot.get(plugin_id)
        if host is None:
            return
        loop = asyncio.get_running_loop()
        loop.create_task(host.cancel_run(run_id), name=f"cancel_run:{run_id}")
    except Exception:
        logger.debug("Failed to propagate cancel for run {} to plugin {}", run_id, plugin_id, exc_info=True)


async def shutdown_runs(*, timeout: float = 5.0) -> None:
    """Cancel and await all active run tasks.

    Should be called during server shutdown to prevent dangling coroutines.
    """
    tasks = dict(_active_run_tasks)
    if not tasks:
        return
    logger.info("Shutting down {} active run task(s)", len(tasks))
    for t in tasks.values():
        t.cancel()
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    _active_run_tasks.clear()


async def _execute_run(
    run_id: str,
    *,
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    task_id: Optional[str],
    client_host: Optional[str],
) -> None:
    """Execute a single Run: transition to running, trigger plugin, commit result.

    Extracted from ``create_run`` so it can be tested and reused independently.
    """
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
        trigger_args = dict(args or {})
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
            logger.debug("Failed to inject run_id into _ctx for run {}", run_id, exc_info=True)

        resp = await trigger_plugin(
            plugin_id=plugin_id,
            entry_id=entry_id,
            args=trigger_args,
            task_id=task_id,
            client_host=client_host,
        )

        # Extract the inner plugin_response from the TriggerResult.
        # The wrapper contains metadata (plugin_id, args, etc.) that is
        # redundant with the RunRecord; only the plugin_response
        # (the ok()/fail() envelope from the actual plugin) is stored as
        # the export payload.
        pr: Any = resp.plugin_response
        if not isinstance(pr, dict):
            pr = resp.model_dump()

        export_item_id = str(uuid.uuid4())
        item = ExportItem.model_validate({
            "export_item_id": export_item_id,
            "run_id": run_id,
            "type": "json",
            "category": "system",
            "created_at": float(time.time()),
            "label": "trigger_response",
            "description": "Structured response from trigger_plugin",
            "json": pr,
            "metadata": {"kind": "trigger_response"},
        })
        _export_store.append(item)
        _emit_export("add", item)

        is_ok = bool(pr.get("success")) if isinstance(pr, dict) else False
        if is_ok:
            term = _run_store.commit_terminal(run_id, status="succeeded", error=None, result_refs=[export_item_id])
        else:
            err_obj = pr.get("error") if isinstance(pr, dict) else None

            if isinstance(err_obj, dict):
                code = str(err_obj.get("code") or "PLUGIN_ERROR")
                msg = str(err_obj.get("message") or "plugin returned failure")
                details = err_obj.get("details")
                if details is not None and not isinstance(details, dict):
                    details = {"details": details}
                run_err = RunError(code=code, message=msg, details=details if isinstance(details, dict) else None)
            elif isinstance(err_obj, str) and err_obj:
                run_err = RunError(code="PLUGIN_ERROR", message=err_obj)
            else:
                run_err = RunError(code="PLUGIN_ERROR", message="plugin returned failure")

            term = _run_store.commit_terminal(
                run_id,
                status="failed",
                error=run_err,
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


async def _execute_run_guarded(
    run_id: str,
    *,
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    task_id: Optional[str],
    client_host: Optional[str],
) -> None:
    """Wrap ``_execute_run`` with a timeout guard."""
    timeout = float(RUN_EXECUTION_TIMEOUT)
    if timeout <= 0:
        await _execute_run(
            run_id, plugin_id=plugin_id, entry_id=entry_id,
            args=args, task_id=task_id, client_host=client_host,
        )
        return
    try:
        await asyncio.wait_for(
            _execute_run(
                run_id, plugin_id=plugin_id, entry_id=entry_id,
                args=args, task_id=task_id, client_host=client_host,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        term = _run_store.commit_terminal(
            run_id,
            status="timeout",
            error=RunError(code="TIMEOUT", message=f"Run exceeded {timeout}s timeout"),
            result_refs=[],
        )
        if term is not None:
            _emit_runs("change", term)


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

    task = asyncio.create_task(
        _execute_run_guarded(
            run_id,
            plugin_id=req.plugin_id,
            entry_id=req.entry_id,
            args=dict(req.args or {}),
            task_id=req.task_id,
            client_host=client_host,
        ),
        name=f"run:{run_id}",
    )
    _active_run_tasks[run_id] = task
    task.add_done_callback(lambda t, rid=run_id: _active_run_tasks.pop(rid, None))
    return RunCreateResponse(run_id=run_id, status="queued")
