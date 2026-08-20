from __future__ import annotations

from .entry_common import Any, StudyEvent, asyncio


_ACTIVE_POMODORO_STATES = {"focusing", "short_break", "long_break"}
_POMODORO_DEADLINE_EPSILON_SECONDS = 0.55


class _CommunicationPomodoroEventsMixin:
    """Drive pomodoro deadlines independently from the hosted UI lifecycle."""

    async def _on_command_loop_start(self) -> None:
        self._pomodoro_lock = asyncio.Lock()
        self._pomodoro_wakeup = asyncio.Event()
        self._pomodoro_watcher_task = None
        self._start_pomodoro_watcher()

    def _pomodoro_runtime_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_pomodoro_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._pomodoro_lock = lock
        return lock

    def _pomodoro_runtime_wakeup(self) -> asyncio.Event:
        wakeup = getattr(self, "_pomodoro_wakeup", None)
        if wakeup is None:
            wakeup = asyncio.Event()
            self._pomodoro_wakeup = wakeup
        return wakeup

    def _wake_pomodoro_watcher(self) -> None:
        self._pomodoro_runtime_wakeup().set()
        task = getattr(self, "_pomodoro_watcher_task", None)
        if task is None or task.done():
            self._start_pomodoro_watcher()

    def _resolve_pomodoro_target_lanlan(
        self, kwargs: dict[str, Any] | None = None
    ) -> str | None:
        context = kwargs.get("_ctx") if isinstance(kwargs, dict) else None
        if isinstance(context, dict):
            target = str(context.get("lanlan_name") or "").strip()
            return target or None
        shared_resolver = getattr(self, "_resolve_study_target_lanlan", None)
        if callable(shared_resolver) and hasattr(self, "ctx"):
            return shared_resolver(kwargs)
        target = str(
            getattr(getattr(self, "ctx", None), "_current_lanlan", "") or ""
        ).strip()
        return target or None

    def _start_pomodoro_watcher(self) -> None:
        task = getattr(self, "_pomodoro_watcher_task", None)
        if task is not None and not task.done():
            return
        self._pomodoro_runtime_wakeup()
        self._pomodoro_watcher_task = asyncio.create_task(self._run_pomodoro_watcher())
        self._pomodoro_watcher_task.add_done_callback(self._on_pomodoro_watcher_done)

    async def _cancel_pomodoro_watcher(self) -> None:
        task = getattr(self, "_pomodoro_watcher_task", None)
        self._pomodoro_watcher_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.warning("study pomodoro watcher cleanup failed: {}", exc)

    def _on_pomodoro_watcher_done(self, task: asyncio.Task[None]) -> None:
        if getattr(self, "_pomodoro_watcher_task", None) is task:
            self._pomodoro_watcher_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self.logger.warning("study pomodoro watcher failed: {}", exc)

    async def _tick_pomodoro_timer_locked(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        _, _, timer, supervision = self._require_habit_components()
        before_status = await asyncio.to_thread(timer.status)
        before_state = str(before_status.get("state") or "")
        status = await asyncio.to_thread(timer.tick)
        after_state = str(status.get("state") or "")
        transition: dict[str, Any] | None = None
        if before_state == "focusing" and after_state in {
            "short_break",
            "long_break",
        }:
            supervision.on_focus_end()
            session_id = str(
                status.get("current_focus_session", {}).get("id")
                or getattr(self, "_pomodoro_session_id", "")
                or ""
            )
            transition = {
                "name": "pomodoro_focus_completed",
                "payload": {
                    "session_id": session_id,
                    "break_type": after_state,
                    "target_lanlan": getattr(self, "_pomodoro_target_lanlan", None),
                },
            }
        elif (
            before_state in {"short_break", "long_break"} and after_state == "completed"
        ):
            session_id = str(
                status.get("current_focus_session", {}).get("id")
                or getattr(self, "_pomodoro_session_id", "")
                or ""
            )
            transition = {
                "name": "pomodoro_break_completed",
                "payload": {
                    "session_id": session_id,
                    "break_type": before_state,
                    "target_lanlan": getattr(self, "_pomodoro_target_lanlan", None),
                },
            }
            self._pomodoro_session_id = ""
            self._pomodoro_target_lanlan = None
        return status, transition

    async def _emit_pomodoro_transition(
        self, transition: dict[str, Any] | None
    ) -> bool:
        if not transition:
            return False
        bus = getattr(self, "_event_bus", None)
        if bus is None:
            return False
        try:
            await bus.emit(
                StudyEvent(
                    name=str(transition.get("name") or ""),
                    payload=dict(transition.get("payload") or {}),
                )
            )
        except Exception as exc:
            self.logger.warning("study pomodoro event delivery failed: {}", exc)
            return False
        return True

    async def _run_pomodoro_watcher(self) -> None:
        wakeup = self._pomodoro_runtime_wakeup()
        while True:
            wakeup.clear()
            async with self._pomodoro_runtime_lock():
                timer = getattr(self, "_pomodoro_timer", None)
                if timer is None:
                    delay: float | None = None
                else:
                    status = await asyncio.to_thread(timer.status)
                    state = str(status.get("state") or "")
                    delay = (
                        max(
                            _POMODORO_DEADLINE_EPSILON_SECONDS,
                            float(status.get("remaining_seconds") or 0.0)
                            + _POMODORO_DEADLINE_EPSILON_SECONDS,
                        )
                        if state in _ACTIVE_POMODORO_STATES
                        else None
                    )
            try:
                if delay is None:
                    await wakeup.wait()
                else:
                    await asyncio.wait_for(wakeup.wait(), timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass

            async with self._pomodoro_runtime_lock():
                _, transition = await self._tick_pomodoro_timer_locked()
            await self._emit_pomodoro_transition(transition)
