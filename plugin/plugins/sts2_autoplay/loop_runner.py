from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from time import time
from typing import Any

from .decision_payload import DecisionPayload
from .instructions import instruction_summary, normalize_strategy_instruction
from .snapshot_normalizer import normalize_snapshot


class STS2LoopRunner:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._poll_task: asyncio.Task[Any] | Future[Any] | None = None
        self._autoplay_task: asyncio.Task[Any] | Future[Any] | None = None
        self._shutdown = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread: threading.Thread | None = None
        self._owner_thread_ready = threading.Event()

    async def tick(self) -> dict[str, Any]:
        client = self._service._require_client()
        raw_state = await client.get_state()
        raw_actions = await client.get_available_actions()
        snapshot = normalize_snapshot(raw_state, raw_actions)
        action_registry = self._service._action_registry.build(snapshot)
        classification = self._service._state_machine.classify(snapshot)
        snapshot_with_context = {**snapshot, "classification": classification, "polled_at": time()}
        snapshot_summary = self._service._summary_builder.build_snapshot_summary(snapshot_with_context)
        previous_summary = self._service._state.latest_snapshot_summary
        continuous_delta = self._service._summary_engine.compute_delta(
            previous_summary if isinstance(previous_summary, dict) else None,
            snapshot_summary,
            source="continuous_snapshot",
        )
        self._service._state.latest_snapshot_summary = dict(snapshot_summary)
        self._service._state.remember_continuous_delta(continuous_delta)
        summary_context = self._service._summary_builder.build(
            snapshot_with_context,
            runtime_state=self._service._state,
        )
        strategy_context = self._service._strategy_repository.build_context(
            {**snapshot, "classification": classification, "summary_context": summary_context}
        )
        tactical_signals = self._service._summary_builder.build_tactical_signals(
            snapshot_with_context,
            summary_context,
            strategy_context,
        )
        mode_info = self._service._mode_controller.describe(self._service._state.control_mode)
        candidate_actions = self._service._candidate_generator.generate(
            {
                "snapshot": {**snapshot, "action_registry": action_registry},
                "classification": classification,
                "summary_context": summary_context,
                "strategy_context": strategy_context,
            },
            mode="program",
        )
        instructions = list(self._service._state.pending_guidance)
        instructions.append(
            normalize_strategy_instruction(
                str(strategy_context.get("strategy_name") or "unknown"),
                dict(strategy_context.get("strategy_constraints") if isinstance(strategy_context.get("strategy_constraints"), dict) else {}),
            )
        )
        decision_payload = DecisionPayload(
            mode=self._service._state.control_mode,
            screen_type=str(classification.get("screen_class") or "unknown"),
            state_name=str(classification.get("state_name") or snapshot.get("screen") or "unknown"),
            summary_kind=str(classification.get("summary_kind") or "general"),
            state_signature=str(self._service._state.snapshot_signature or ""),
            strategy_directives={
                "strategy_name": strategy_context.get("strategy_name"),
                "strategy_prompt": strategy_context.get("strategy_prompt"),
                "constraints": strategy_context.get("strategy_constraints"),
                **(strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}),
            },
            guidance={
                "pending": list(self._service._state.pending_guidance),
                "generation": self._service._state.guidance_generation,
                "summary": instruction_summary(instructions),
            },
            instructions=instructions,
            run_state={
                "floor": snapshot.get("floor"),
                "act": snapshot.get("act"),
                "in_combat": snapshot.get("in_combat"),
                "current_hp": snapshot_summary.get("player", {}).get("current_hp") if isinstance(snapshot_summary.get("player"), dict) else None,
                "max_hp": snapshot_summary.get("player", {}).get("max_hp") if isinstance(snapshot_summary.get("player"), dict) else None,
            },
            tactical_signals=tactical_signals,
            legal_actions=[dict(action) for action in action_registry if isinstance(action, dict)],
            candidate_actions=[dict(item) for item in candidate_actions if isinstance(item, dict)],
            policy={
                "allows_planner": bool(mode_info.get("allows_planner")),
                "allows_game_llm": bool(mode_info.get("allows_game_llm")),
                "prefers_heuristic": bool(mode_info.get("prefers_heuristic")),
                "prefers_model": bool(mode_info.get("prefers_model")),
            },
        ).as_dict()
        decision_payload["recent_decision_memory"] = list(self._service._state.recent_decision_memory)
        decision_payload["run_intent"] = dict(self._service._state.run_intent)
        summary_context["decision_payload"] = decision_payload
        strategy_context["decision_payload"] = decision_payload
        planning_context = {
            "snapshot": {**snapshot, "action_registry": action_registry},
            "classification": classification,
            "summary_context": summary_context,
            "strategy_context": strategy_context,
            "mode": mode_info,
        }
        planned_operation = self._service._planner.plan(planning_context) if mode_info.get("allows_planner") else None
        planned_operation_dict = planned_operation.as_dict() if planned_operation is not None else None

        agent_operation = planned_operation_dict

        executable_operation = self._service._action_engine.validate({**snapshot, "action_registry": action_registry}, agent_operation)
        situation_summary = self._service._summary_engine.summarize(summary_context)
        companion_evaluation = self._service._companion_evaluator.evaluate(
            summary_context=summary_context,
            situation_summary=situation_summary,
            strategy_context=strategy_context,
            runtime_state=self._service._state,
        )
        action_frame = self._service._state.latest_action_frame if isinstance(self._service._state.latest_action_frame, dict) else {}
        if action_frame:
            companion_evaluation["action_frame"] = dict(action_frame)
        catgirl_sync = self._service._catgirl_bridge.build_sync_packet(
            {
                **snapshot,
                "classification": classification,
                "situation_summary": situation_summary,
                "companion_evaluation": companion_evaluation,
            },
            standby=self._service._state.standby,
        )
        catgirl_sync["payload"]["agent_summary"] = {
            "standby": self._service._state.standby,
            "text": str(situation_summary.get("text") or ""),
            "kind": situation_summary.get("kind", "general"),
            "source": situation_summary.get("source", "snapshot"),
            "delta": dict(situation_summary.get("delta") if isinstance(situation_summary.get("delta"), dict) else {}),
            "before": dict(situation_summary.get("before") if isinstance(situation_summary.get("before"), dict) else {}),
            "after": dict(situation_summary.get("after") if isinstance(situation_summary.get("after"), dict) else {}),
            "recent_guidance": list(self._service._state.pending_guidance),
            "companion_evaluation": companion_evaluation,
        }

        return {
            "raw_state": raw_state,
            "raw_actions": raw_actions,
            "snapshot": {
                **snapshot,
                "action_registry": action_registry,
                "classification": classification,
                "summary_context": summary_context,
                "strategy_context": strategy_context,
                "mode": mode_info,
                "planned_operation": planned_operation_dict,
                "agent_operation": agent_operation,
                "executable_operation": executable_operation,
                "agent_packet": {
                    "screen": snapshot.get("screen", "unknown"),
                    "classification": classification,
                    "strategy_context": strategy_context,
                    "summary_context": summary_context,
                    "standby": self._service._state.standby,
                    "recent_guidance": list(self._service._state.pending_guidance),
                    "available_action_ids": [str(action.get("id") or "") for action in action_registry if isinstance(action, dict)],
                    "companion_evaluation": companion_evaluation,
                },
                "situation_summary": situation_summary,
                "companion_evaluation": companion_evaluation,
                "catgirl_sync": catgirl_sync,
                "polled_at": snapshot_with_context["polled_at"],
            },
        }

    def start_background(self) -> None:
        self._shutdown = False
        if self._poll_task is None or self._task_done(self._poll_task):
            self._ensure_owner_loop()
            self._poll_task = self._create_task(self._poll_loop(), name="sts2-poll-loop")

    def start_autoplay(self) -> None:
        self._shutdown = False
        if self._autoplay_task is None or self._task_done(self._autoplay_task):
            self._ensure_owner_loop()
            self._autoplay_task = self._create_task(self._autoplay_loop(), name="sts2-autoplay-loop")

    async def stop_background(self) -> None:
        self.stop_background_sync()

    def is_polling(self) -> bool:
        return self._poll_task is not None and not self._task_done(self._poll_task)

    def is_autoplaying(self) -> bool:
        return self._autoplay_task is not None and not self._task_done(self._autoplay_task)

    def _ensure_owner_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._owner_loop
        thread = self._owner_thread
        if loop is not None and thread is not None and thread.is_alive() and not loop.is_closed():
            return loop

        ready = self._owner_thread_ready
        ready.clear()
        holder: dict[str, asyncio.AbstractEventLoop] = {}

        def _run_loop() -> None:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            holder["loop"] = worker_loop
            ready.set()
            try:
                worker_loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(worker_loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    worker_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                worker_loop.close()

        thread = threading.Thread(target=_run_loop, name="sts2-companion-poll", daemon=True)
        thread.start()
        if not ready.wait(timeout=2.0):
            loop = holder.get("loop")
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
            if thread.is_alive():
                thread.join(timeout=3.0)
            return None
        self._owner_loop = holder.get("loop")
        self._owner_thread = thread
        return self._owner_loop

    def _stop_owner_loop(self) -> None:
        loop = self._owner_loop
        thread = self._owner_thread
        self._owner_loop = None
        self._owner_thread = None
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def _task_done(self, task: asyncio.Task[Any] | Future[Any] | None) -> bool:
        return task is None or task.done()

    async def _poll_loop(self) -> None:
        while not self._shutdown:
            try:
                await self._service.refresh_state(trigger_sync=True)
            except Exception as exc:
                self._service._mark_loop_error(exc)
            interval = self._service._cfg_poll_interval(active=self._service._state.autoplay_state == "running")
            await asyncio.sleep(interval)

    async def _autoplay_loop(self) -> None:
        while not self._shutdown:
            if self._service._state.autoplay_state != "running" or self._service._state.standby:
                await asyncio.sleep(0.25)
                continue
            try:
                await self._service.run_autoplay_step()
            except Exception as exc:
                self._service._mark_loop_error(exc)
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(self._service._cfg_action_interval())

    def _create_task(self, coro: Any, *, name: str) -> asyncio.Task[Any] | Future[Any] | None:
        try:
            current_loop = asyncio.get_running_loop()
            if self._owner_loop is None or self._owner_loop.is_closed() or not self._owner_loop.is_running():
                self._owner_loop = current_loop
            loop = self._owner_loop
            if loop is current_loop:
                task = loop.create_task(coro, name=name)
            else:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                task = asyncio.wrap_future(future)
            task.add_done_callback(lambda t: self._log_task_done(name, t))
            return task
        except RuntimeError:
            coro.close()
            return None

    def _log_task_done(self, name: str, task: asyncio.Task[Any] | Future[Any]) -> None:
        return


    def stop_background_sync(self) -> None:
        self._shutdown = True
        for task in (self._autoplay_task, self._poll_task):
            if task is None or task.done():
                continue
            task.cancel()
        self._autoplay_task = None
        self._poll_task = None
        self._stop_owner_loop()


__all__ = ["STS2LoopRunner"]
