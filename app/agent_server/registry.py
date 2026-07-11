# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Task registry lifecycle helpers for the ``app.agent_server`` package.

Owns ``task_registry`` maintenance (cleanup / spawn / duplicate detection),
the tool-correction context accessors and the fire-and-forget cancel runner.
``_task_registry_last_cleanup`` is a rebindable module global owned here and
is deliberately NOT re-exported by the package facade (a facade snapshot
would go stale on every rebind).
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from . import _shared
from ._shared import logger, TASK_REGISTRY_CLEANUP_TTL

_task_registry_last_cleanup: float = 0.0

_LEGACY_CORRECTION_PUBLIC_KEYS = {
    "decision_reason",
    "task_description",
    "latest_user_request",
    "normalized_intent",
    "recent_context",
}


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _cleanup_task_registry() -> List[Dict[str, Any]]:
    """Clean up completed/failed/cancelled tasks older than 5 minutes from task_registry to prevent memory leaks; also check deferred task timeouts

    Returns the list of timed-out deferred tasks (a task_update notification must be sent to the frontend)
    """
    global _task_registry_last_cleanup
    now = time.time()
    timed_out: List[Dict[str, Any]] = []
    if now - _task_registry_last_cleanup < 60:  # 最多每 60 秒清理一次
        return timed_out
    _task_registry_last_cleanup = now
    to_remove = []
    for tid, info in _shared.Modules.task_registry.items():
        st = info.get("status")

        # 检查 deferred 任务是否超时（防止绑定失败导致任务永远卡在 running）
        if st == "running" and info.get("deferred_timeout"):
            if now > info.get("deferred_timeout", float('inf')):
                logger.warning("[TaskRegistry] Deferred task %s timed out, marking as failed", tid)
                info["status"] = "failed"
                info["end_time"] = _now_iso()
                info["error"] = "Deferred task timeout (callback not received)"
                # 收集超时任务，需要通知前端
                timed_out.append({
                    "id": tid,
                    "status": "failed",
                    "type": info.get("type"),
                    "start_time": info.get("start_time"),
                    "end_time": info.get("end_time"),
                    "error": info.get("error"),
                    "params": info.get("params", {}),
                    "lanlan_name": info.get("lanlan_name"),
                })
                continue

        if st not in ("completed", "failed", "cancelled"):
            continue
        end_time_str = info.get("end_time")
        if end_time_str:
            try:
                end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - end_dt).total_seconds()
                if age > TASK_REGISTRY_CLEANUP_TTL:
                    to_remove.append(tid)
            except Exception:
                to_remove.append(tid)  # 解析失败的旧条目直接清理
        else:
            # 没有 end_time 的终态任务，用 start_time 估算
            start_str = info.get("start_time", "")
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - start_dt).total_seconds()
                if age > TASK_REGISTRY_CLEANUP_TTL * 2:  # 宽松一点
                    to_remove.append(tid)
            except Exception:
                pass
    for tid in to_remove:
        del _shared.Modules.task_registry[tid]
    if to_remove:
        logger.debug("[TaskRegistry] Cleaned up %d completed tasks", len(to_remove))
    return timed_out


def _collect_existing_task_descriptions(lanlan_name: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (task_id, description) for queued/running tasks, optionally filtered by lanlan_name."""
    items: list[tuple[str, str]] = []
    for tid, info in _shared.Modules.task_registry.items():
        try:
            if info.get("status") in ("queued", "running"):
                if lanlan_name and info.get("lanlan_name") not in (None, lanlan_name):
                    continue
                params = info.get("params") or {}
                desc = params.get("query") or params.get("instruction") or ""
                if desc:
                    items.append((tid, desc))
        except Exception:
            continue
    return items


async def _is_duplicate_task(query: str, lanlan_name: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Use LLM to judge if query duplicates any existing queued/running task."""
    try:
        if not _shared.Modules.deduper:
            return False, None
        candidates = _collect_existing_task_descriptions(lanlan_name)
        res = await _shared.Modules.deduper.judge(query, candidates)
        return bool(res.get("duplicate")), res.get("matched_id")
    except Exception as e:
        logger.warning(f"[Agent] Deduper judge failed: {e}")
        return False, None


def _spawn_task(kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a computer_use task entry and enqueue it for exclusive execution."""
    task_id = str(uuid.uuid4())
    info = {
        "id": task_id,
        "type": kind,
        "status": "queued",
        "start_time": _now_iso(),
        "params": args,
        "result": None,
        "error": None,
    }
    if kind == "computer_use":
        _shared.Modules.task_registry[task_id] = info
        if _shared.Modules.computer_use_queue is None:
            _shared.Modules.computer_use_queue = asyncio.Queue()
        _shared.Modules.computer_use_queue.put_nowait({
            "task_id": task_id,
            "instruction": args.get("instruction", ""),
        })
        return info
    else:
        raise ValueError(f"Unknown task kind: {kind}")


def _set_internal_correction_context(task_info: Dict[str, Any], result: Any) -> None:
    task_info["_internal_corrections"] = {
        "decision_reason": getattr(result, "reason", "") or "",
        "task_description": getattr(result, "task_description", "") or "",
        "latest_user_request": getattr(result, "latest_user_request", "") or "",
        "normalized_intent": getattr(result, "normalized_intent", "") or "",
        "recent_context": getattr(result, "recent_context", None) or [],
    }


def _get_internal_correction_context(task_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    internal = task_info.get("_internal_corrections")
    if isinstance(internal, dict):
        return internal

    legacy = {key: task_info.get(key) for key in _LEGACY_CORRECTION_PUBLIC_KEYS if key in task_info}
    if legacy:
        return legacy

    params = task_info.get("params")
    if isinstance(params, dict):
        fallback_text = str(params.get("query") or params.get("instruction") or "").strip()
        if fallback_text:
            return {
                "task_description": fallback_text,
                "latest_user_request": fallback_text,
                "normalized_intent": "",
                "recent_context": [],
            }

    return None


def _tracker_desc_for_task_info(task_info: Dict[str, Any]) -> str:
    task_type = str(task_info.get("type") or "")
    params = task_info.get("params") if isinstance(task_info.get("params"), dict) else {}
    if task_type == "user_plugin":
        plugin_id = str(params.get("plugin_id") or "").strip()
        entry_id = str(params.get("entry_id") or "").strip()
        desc = str(params.get("description") or params.get("instruction") or params.get("query") or "").strip()
        prefix = ".".join(part for part in (plugin_id, entry_id) if part)
        return f"{prefix}: {desc}" if prefix and desc else (prefix or desc)
    return str(
        params.get("description")
        or params.get("instruction")
        or params.get("query")
        or task_info.get("task_description")
        or ""
    ).strip()


def _public_task_info(task_info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in task_info.items()
        if not key.startswith("_") and key not in _LEGACY_CORRECTION_PUBLIC_KEYS
    }


def _spawn_background_cancel(coro, *, label: str) -> None:
    """Fire-and-forget a long-running cancel/teardown coroutine.

    cancel_task must return quickly so the HUD button is responsive regardless
    of how long the underlying provider takes to actually stop (browser process
    tree teardown, remote /stop HTTP, etc.). We track the task in
    _background_tasks so it is not garbage-collected mid-run.
    """
    async def _runner():
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[Cancel:%s] background cleanup failed: %s", label, exc)

    t = asyncio.create_task(_runner())
    _shared.Modules._background_tasks.add(t)
    t.add_done_callback(_shared.Modules._background_tasks.discard)
