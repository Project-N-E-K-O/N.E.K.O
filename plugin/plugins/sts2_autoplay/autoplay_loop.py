from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class AutoplayLoopMixin:
    async def start_autoplay(self, objective: Optional[str] = None, stop_condition: str = "current_floor") -> Dict[str, Any]:
        replaced_existing_task = False
        previous_task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
        if self._autoplay_task and not self._autoplay_task.done():
            replaced_existing_task = True
            await self.stop_autoplay(reason="用户重新请求代打，停止旧的半自动任务")
        if self._autoplay_task and self._autoplay_task.done():
            self._autoplay_task = None
        self._last_error = ""

        try:
            await self.refresh_state()
        except Exception as exc:
            self.logger.warning(f"启动尖塔半自动前刷新状态失败，将延迟初始化起点: {exc}")
            self._snapshot = {}

        self._semi_auto_task = self._build_semi_auto_task(objective=objective, stop_condition=stop_condition)
        try:
            await self._notify_neko_task_event("started")
        except Exception as exc:
            self.logger.warning(f"任务启动通知失败，不影响任务执行: {exc}")

        self._paused = False
        self._auto_pause_reason = None
        self._autoplay_state = "running"
        self._autoplay_task = asyncio.create_task(self._autoplay_loop())
        self._emit_status()
        message = "尖塔半自动任务已重新启动，旧任务已停止，尚未代表已经执行游戏动作" if replaced_existing_task else "尖塔半自动任务已启动，尚未代表已经执行游戏动作"
        return {
            "status": "running",
            "message": message,
            "task": self._semi_auto_task,
            "previous_task": previous_task,
            "replaced_existing_task": replaced_existing_task,
            "task_started": True,
            "action_executed": False,
            "executed": False,
        }

    async def pause_autoplay(self, reason: str = "用户请求暂停") -> Dict[str, Any]:
        if self._autoplay_task is None or self._autoplay_task.done():
            return {"status": "idle", "message": "没有运行中的尖塔半自动任务", "executed": False}
        task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
        self._auto_pause_reason = reason
        self._paused = True
        if self._autoplay_state == "running":
            self._autoplay_state = "paused"
        self._emit_status()
        try:
            await self._notify_neko_task_event("paused", task=task, reason=reason)
        except Exception as exc:
            self.logger.warning(f"任务暂停通知失败: {exc}")
        return {"status": "paused", "message": "尖塔已暂停", "reason": reason}

    async def resume_autoplay(self) -> Dict[str, Any]:
        if self._autoplay_task is None or self._autoplay_task.done():
            self._paused = False
            self._autoplay_state = "idle"
            self._emit_status()
            return {"status": "idle", "message": "没有可恢复的尖塔半自动任务", "executed": False}
        self._paused = False
        self._auto_pause_reason = None
        self._autoplay_state = "running"
        self._emit_status()
        return {"status": "running", "message": "尖塔已恢复"}

    async def stop_autoplay(self, reason: str = "用户请求停止") -> Dict[str, Any]:
        task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
        self._paused = False
        self._auto_pause_reason = None
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
        self._last_error = ""
        self._emit_status()
        try:
            await self._notify_neko_task_event("stopped", task=task, reason=reason)
        except Exception as exc:
            self.logger.warning(f"任务停止通知失败: {exc}")
        return {"status": "idle", "message": "尖塔已停止", "reason": reason}

    async def get_history(self, limit: int = 20) -> Dict[str, Any]:
        try:
            limit = max(1, min(100, int(limit or 20)))
        except (ValueError, TypeError):
            limit = 20
        items = list(self._history)[:limit]
        message = f"最近 {len(items)} 条历史"
        return {"status": "ok", "message": message, "summary": message, "history": items}

    async def send_neko_guidance(self, guidance: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(guidance, dict):
            return {"status": "error", "message": "guidance 必须是字典"}
        if not guidance.get("content"):
            return {"status": "error", "message": "guidance.content 不能为空"}
        _guidance_hard_limit = getattr(self._neko_guidance_queue, "maxlen", None) or 50
        try:
            max_queue = min(_guidance_hard_limit, max(1, int(self._cfg.get("neko_guidance_max_queue", 50) or 50)))
        except (ValueError, TypeError):
            max_queue = _guidance_hard_limit
        step_value = guidance.get("step")
        try:
            guidance_step = self._step_count if step_value is None else int(step_value)
        except Exception:
            guidance_step = self._step_count
        while len(self._neko_guidance_queue) >= max_queue:
            self._neko_guidance_queue.popleft()
        self._neko_guidance_queue.append({
            "content": str(guidance.get("content", "")),
            "step": guidance_step,
            "type": str(guidance.get("type", "soft_guidance")),
            "received_at": time.time(),
        })
        return {"status": "ok", "message": "猫娘指导已入队", "queue_size": len(self._neko_guidance_queue)}

    async def _poll_loop(self) -> None:
        while not self._shutdown:
            try:
                await self.refresh_state()
                recovered = self._transport_state != "connected" or bool(self._poll_last_error) or bool(self._last_error)
                self._consecutive_errors = 0
                self._poll_last_error = ""
                self._poll_last_success_at = time.time()
                self._set_transport_state("connected", error="")
                if recovered:
                    self._emit_status()
            except Exception as exc:
                self._consecutive_errors += 1
                try:
                    max_errors = max(1, int(self._cfg.get("max_consecutive_errors", 3) or 3))
                except (ValueError, TypeError):
                    max_errors = 3
                next_state = "degraded" if self._consecutive_errors < max_errors else "disconnected"
                error_text = str(exc)
                self._poll_last_error = error_text
                self._poll_last_failure_at = time.time()
                self._set_transport_state(next_state, error=error_text)
                self._emit_status()
            try:
                interval = float(self._cfg.get("poll_interval_active_seconds", 1) if self._autoplay_state == "running" else self._cfg.get("poll_interval_idle_seconds", 3))
            except (ValueError, TypeError):
                interval = 1.0 if self._autoplay_state == "running" else 3.0
            await asyncio.sleep(max(0.1, interval))

    async def _autoplay_loop(self) -> None:
        prev_screen = None
        try:
            while not self._shutdown:
                if self._paused:
                    autonomous = self._assess_neko_autonomous_action(prev_screen)
                    if autonomous:
                        try:
                            await self._execute_autonomous_action(autonomous)
                        except Exception as exc:
                            self.logger.warning(f"猫娘暂停态自主动作失败，跳过本轮: {exc}")
                    await asyncio.sleep(0.2)
                    prev_screen = self._snapshot.get("screen") if self._snapshot else None
                    continue
                result = await self.step_once()
                if result.get("status") == "error":
                    self._last_error = str(result.get("error") or result.get("message") or "step_once failed")
                    self._emit_status()
                    await asyncio.sleep(0.2)
                    prev_screen = self._snapshot.get("screen") if self._snapshot else None
                    continue
                self._step_count += 1
                if result.get("status") == "idle":
                    try:
                        idle_sleep = max(0.2, float(self._cfg.get("poll_interval_active_seconds", 1) or 1))
                    except (ValueError, TypeError):
                        idle_sleep = 1.0
                    await asyncio.sleep(idle_sleep)
                if result.get("status") == "error":
                    self._last_error = str(result.get("error") or result.get("message") or "step_once failed")
                    self._emit_status()
                    await asyncio.sleep(0.2)
                    prev_screen = self._snapshot.get("screen") if self._snapshot else None
                    continue
                try:
                    report_interval = max(1, int(self._cfg.get("neko_report_interval_steps", 1) or 1))
                except (ValueError, TypeError):
                    report_interval = 1
                should_report = self._step_count - self._last_task_report_step >= report_interval
                if bool(self._cfg.get("neko_reporting_enabled", False)) and should_report:
                    try:
                        await self._push_neko_report(result)
                        self._last_task_report_step = self._step_count
                    except Exception as exc:
                        self.logger.warning(f"猫娘观察汇报失败，跳过本轮: {exc}")
                if self._is_semi_auto_task_complete():
                    await self._complete_semi_auto_task()
                    break
                autonomous = self._assess_neko_autonomous_action(prev_screen)
                if autonomous:
                    try:
                        await self._execute_autonomous_action(autonomous)
                    except Exception as exc:
                        self.logger.warning(f"猫娘自主动作失败，跳过本轮: {exc}")
                prev_screen = self._snapshot.get("screen") if self._snapshot else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            task = dict(self._semi_auto_task) if isinstance(self._semi_auto_task, dict) else None
            self._autoplay_state = "error"
            self._last_error = str(exc)
            self.logger.exception("尖塔半自动循环异常停止")
            try:
                await self._maybe_emit_frontend_message(event_type="error", detail=str(exc), snapshot=self._snapshot, priority=7, force=True)
            except Exception as notify_exc:
                self.logger.warning(f"终态前端通知失败: {notify_exc}")
            try:
                await self._notify_neko_task_event("error", task=task, reason=str(exc))
            except Exception as notify_exc:
                self.logger.warning(f"终态任务事件通知失败: {notify_exc}")
            self._emit_status()
        finally:
            if self._autoplay_task is asyncio.current_task():
                self._autoplay_task = None

    def _build_semi_auto_task(self, *, objective: Optional[str], stop_condition: str) -> Dict[str, Any]:
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        normalized_stop = str(stop_condition or "current_floor").strip() or "current_floor"
        start_floor = self._safe_int(snapshot.get("floor"), None) if snapshot.get("floor") is not None else None
        start_act = self._safe_int(snapshot.get("act"), None) if snapshot.get("act") is not None else None
        return {
            "mode": "semi_auto",
            "objective": str(objective or "用户请求猫娘帮忙处理当前关卡").strip(),
            "stop_condition": normalized_stop,
            "started_at": time.time(),
            "start_step": self._step_count,
            "start_screen": snapshot.get("screen"),
            "start_floor": start_floor,
            "start_act": start_act,
            "status": "running",
        }

    def _is_semi_auto_task_complete(self) -> bool:
        task = self._semi_auto_task
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        if not isinstance(task, dict) or not snapshot:
            return False
        stop_condition = str(task.get("stop_condition") or "current_floor")
        if task.get("start_floor") is None and snapshot.get("floor") is not None:
            task["start_floor"] = self._safe_int(snapshot.get("floor"))
            task["start_act"] = self._safe_int(snapshot.get("act"), None) if snapshot.get("act") is not None else None
            task["start_screen"] = task.get("start_screen") or snapshot.get("screen")
            return False
        start_floor = self._safe_int(task.get("start_floor"))
        current_floor = self._safe_int(snapshot.get("floor"))
        screen = self._normalized_screen_name(snapshot)
        in_combat = bool(snapshot.get("in_combat", False))
        if stop_condition in {"manual", "none"}:
            return False
        if stop_condition in {"combat", "current_combat"}:
            if in_combat or screen == "combat":
                task["has_entered_combat"] = True
                return False
            if task.get("has_entered_combat") and screen != "combat":
                return True
            if current_floor > start_floor:
                return True
            return False
        if current_floor > start_floor:
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
        try:
            await self._notify_neko_task_event("completed", task=task)
        except Exception as notify_exc:
            self.logger.warning(f"任务完成通知失败: {notify_exc}")
        self._paused = False
        self._autoplay_state = "idle"
        self._emit_status()
