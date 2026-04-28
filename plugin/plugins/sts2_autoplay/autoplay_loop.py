from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class AutoplayLoopMixin:
    async def start_autoplay(self, objective: Optional[str] = None, stop_condition: str = "current_floor") -> Dict[str, Any]:
        if objective or bool(self._cfg.get("semi_auto_autoplay", True)):
            self._semi_auto_task = self._build_semi_auto_task(objective=objective, stop_condition=stop_condition)
            await self._notify_neko_task_event("started")
        if self._autoplay_task and not self._autoplay_task.done():
            return {"status": "running", "message": "尖塔半自动任务已在运行", "task": self._semi_auto_task}
        self._paused = False
        self._autoplay_state = "running"
        self._autoplay_task = asyncio.create_task(self._autoplay_loop())
        self._emit_status()
        return {"status": "running", "message": "尖塔半自动任务已启动", "task": self._semi_auto_task}

    async def pause_autoplay(self, reason: str = "用户请求暂停") -> Dict[str, Any]:
        task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
        self._paused = True
        if self._autoplay_state == "running":
            self._autoplay_state = "paused"
        self._emit_status()
        await self._notify_neko_task_event("paused", task=task, reason=reason)
        return {"status": "paused", "message": "尖塔已暂停", "reason": reason}

    async def resume_autoplay(self) -> Dict[str, Any]:
        if self._autoplay_task is None or self._autoplay_task.done():
            return await self.start_autoplay()
        self._paused = False
        self._autoplay_state = "running"
        self._emit_status()
        return {"status": "running", "message": "尖塔已恢复"}

    async def stop_autoplay(self, reason: str = "用户请求停止") -> Dict[str, Any]:
        task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
        self._paused = False
        self._semi_auto_task = None
        if self._autoplay_task is not None:
            self._autoplay_task.cancel()
            try:
                await self._autoplay_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._autoplay_task = None
        self._autoplay_state = "idle"
        self._emit_status()
        await self._notify_neko_task_event("stopped", task=task, reason=reason)
        return {"status": "idle", "message": "尖塔已停止", "reason": reason}

    async def get_history(self, limit: int = 20) -> Dict[str, Any]:
        limit = max(1, min(100, int(limit or 20)))
        items = list(self._history)[:limit]
        message = f"最近 {len(items)} 条历史"
        return {"status": "ok", "message": message, "summary": message, "history": items}

    async def send_neko_guidance(self, guidance: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(guidance, dict):
            return {"status": "error", "message": "guidance 必须是字典"}
        if not guidance.get("content"):
            return {"status": "error", "message": "guidance.content 不能为空"}
        max_queue = int(self._cfg.get("neko_guidance_max_queue", 50) or 50)
        if len(self._neko_guidance_queue) >= max_queue:
            self._neko_guidance_queue.popleft()
        self._neko_guidance_queue.append({
            "content": str(guidance.get("content", "")),
            "step": int(guidance.get("step", self._step_count)),
            "type": str(guidance.get("type", "soft_guidance")),
            "received_at": time.time(),
        })
        return {"status": "ok", "message": "猫娘指导已入队", "queue_size": len(self._neko_guidance_queue)}

    async def _poll_loop(self) -> None:
        while not self._shutdown:
            try:
                await self.refresh_state()
                self._consecutive_errors = 0
            except Exception as exc:
                self._consecutive_errors += 1
                self._server_state = "degraded" if self._consecutive_errors < int(self._cfg.get("max_consecutive_errors", 3) or 3) else "disconnected"
                self._last_error = str(exc)
                self._emit_status()
            interval = float(self._cfg.get("poll_interval_active_seconds", 1) if self._autoplay_state == "running" else self._cfg.get("poll_interval_idle_seconds", 3))
            await asyncio.sleep(max(0.1, interval))

    async def _autoplay_loop(self) -> None:
        prev_screen = None
        try:
            while not self._shutdown:
                if self._paused:
                    await asyncio.sleep(0.2)
                    continue
                result = await self.step_once()
                self._step_count += 1
                if result.get("status") == "idle":
                    await asyncio.sleep(max(0.2, float(self._cfg.get("poll_interval_active_seconds", 1) or 1)))
                report_interval = max(1, int(self._cfg.get("neko_report_interval_steps", 1) or 1))
                should_report = self._step_count - self._last_task_report_step >= report_interval
                if bool(self._cfg.get("neko_reporting_enabled", False)) and should_report:
                    await self._push_neko_report(result)
                    self._last_task_report_step = self._step_count
                if self._is_semi_auto_task_complete():
                    await self._complete_semi_auto_task()
                    break
                autonomous = self._assess_neko_autonomous_action(prev_screen)
                if autonomous:
                    await self._execute_autonomous_action(autonomous)
                prev_screen = self._snapshot.get("screen") if self._snapshot else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
            self._autoplay_state = "error"
            self._last_error = str(exc)
            await self._maybe_emit_frontend_message(event_type="error", detail=str(exc), snapshot=self._snapshot, priority=7, force=True)
            await self._notify_neko_task_event("error", task=task, reason=str(exc))
            self._emit_status()

    def _build_semi_auto_task(self, *, objective: Optional[str], stop_condition: str) -> Dict[str, Any]:
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        normalized_stop = str(stop_condition or "current_floor").strip() or "current_floor"
        return {
            "mode": "semi_auto",
            "objective": str(objective or "用户请求猫娘帮忙处理当前关卡").strip(),
            "stop_condition": normalized_stop,
            "started_at": time.time(),
            "start_step": self._step_count,
            "start_screen": snapshot.get("screen"),
            "start_floor": self._safe_int(snapshot.get("floor")),
            "start_act": self._safe_int(snapshot.get("act") or 1),
            "status": "running",
        }

    def _is_semi_auto_task_complete(self) -> bool:
        task = self._semi_auto_task
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        if not isinstance(task, dict) or not snapshot:
            return False
        stop_condition = str(task.get("stop_condition") or "current_floor")
        start_floor = self._safe_int(task.get("start_floor"))
        current_floor = self._safe_int(snapshot.get("floor"))
        screen = self._normalized_screen_name(snapshot)
        in_combat = bool(snapshot.get("in_combat", False))
        if stop_condition in {"manual", "none"}:
            return False
        if stop_condition in {"combat", "current_combat"}:
            return bool(task.get("start_screen") == "combat" and not in_combat and screen != "combat")
        if current_floor > start_floor:
            return True
        if task.get("start_screen") == "combat" and not in_combat and screen in {"reward", "map", "event", "shop", "rest", "treasure"}:
            return True
        return False

    async def _complete_semi_auto_task(self) -> None:
        task = self._semi_auto_task
        if not isinstance(task, dict):
            return
        task = dict(task)
        task["status"] = "completed"
        task["completed_at"] = time.time()
        task["completed_step"] = self._step_count
        self._semi_auto_task = None
        await self._notify_neko_task_event("completed", task=task)
        self._paused = False
        self._autoplay_state = "idle"
        self._emit_status()
