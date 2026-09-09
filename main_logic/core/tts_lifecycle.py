"""Provider-neutral ownership records for a main TTS worker and its queues."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class TtsCapacityError(RuntimeError):
    """The bounded main TTS worker pool cannot admit another runtime."""


@dataclass(eq=False)
class TtsRuntimeRecord:
    thread: Any
    request_queue: Any
    response_queue: Any
    handler: asyncio.Task | None = None
    retired: bool = False
    shutdown_sent: bool = False
    supports_runtime_overlap: bool = True
    cleanup_task: asyncio.Task | None = None
    cleanup_complete: asyncio.Event = field(default_factory=asyncio.Event)
    handoff_safe: asyncio.Event = field(default_factory=asyncio.Event)


# Task-local identity also follows notifications scheduled by a handler. A late
# task cannot acquire the replacement runtime simply by rereading the manager.
tts_output_runtime: ContextVar[TtsRuntimeRecord | None] = ContextVar(
    "tts_output_runtime", default=None
)


class TtsLifecycleMixin:
    """Own retirement independently from the caller requesting teardown."""

    def _init_tts_lifecycle_state(self):
        if not hasattr(self, "_tts_runtimes"):
            self._tts_runtimes = []
            self._tts_runtime = None
            self._tts_cleanup_tasks = set()

    def _snapshot_tts_runtime(self):
        self._init_tts_lifecycle_state()
        thread = getattr(self, "tts_thread", None)
        if thread is None:
            return None
        request_queue = getattr(self, "tts_request_queue", None)
        response_queue = getattr(self, "tts_response_queue", None)
        current = self._tts_runtime
        if (current is not None and current.thread is thread
                and current.request_queue is request_queue
                and current.response_queue is response_queue):
            return current
        for runtime in self._tts_runtimes:
            if (runtime.thread is thread
                    and runtime.request_queue is request_queue
                    and runtime.response_queue is response_queue):
                self._tts_runtime = runtime
                return runtime
        runtime = TtsRuntimeRecord(thread, request_queue, response_queue)
        runtime.handler = getattr(self, "tts_handler_task", None)
        self._tts_runtimes.append(runtime)
        self._tts_runtime = runtime
        return runtime

    def _tts_runtime_is_current(self, runtime):
        if runtime is None:
            return True  # Legacy facade-only callers have no installed worker.
        return bool(
            runtime is getattr(self, "_tts_runtime", None)
            and not runtime.retired
            and not runtime.shutdown_sent
            and runtime.thread is getattr(self, "tts_thread", None)
            and runtime.request_queue is getattr(self, "tts_request_queue", None)
            and runtime.response_queue is getattr(self, "tts_response_queue", None)
        )

    def _tts_output_is_current(self):
        return self._tts_runtime_is_current(tts_output_runtime.get())

    def _live_tts_runtime_count(self):
        self._snapshot_tts_runtime()
        return sum(bool(record.thread and record.thread.is_alive())
                   for record in self._tts_runtimes)

    def _tts_capacity_limit(self, worker=None):
        self._init_tts_lifecycle_state()
        if worker is not None and not getattr(worker, "supports_runtime_overlap", True):
            return 1
        if any(not record.supports_runtime_overlap and record.thread.is_alive()
               for record in self._tts_runtimes if record.thread is not None):
            return 1
        return 2

    def _schedule_tts_cleanup(self, runtime):
        if runtime.cleanup_task is not None:
            return runtime.cleanup_task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Synchronous startup integrations may retire before their loop
            # exists. Keep the record; the next capacity wait owns its cleanup.
            return None
        task = loop.create_task(self._finish_retired_tts_runtime(runtime))
        runtime.cleanup_task = task
        self._tts_cleanup_tasks.add(task)
        task.add_done_callback(self._tts_cleanup_tasks.discard)
        return task

    def _retire_tts_runtime(self, runtime=None, *, stop_handler=True):
        runtime = runtime if runtime is not None else self._snapshot_tts_runtime()
        if runtime is None:
            return None
        self._init_tts_lifecycle_state()
        if runtime not in self._tts_runtimes:
            self._tts_runtimes.append(runtime)
        if stop_handler and self._tts_runtime_is_current(runtime):
            # The completion slot belongs to the runtime at acceptance, not to
            # whichever runtime exists when a slow worker finally exits.
            cancel_completion = getattr(self, "_cancel_game_speech_completion_wait", None)
            clear_correlation = getattr(self, "_clear_game_speech_correlation", None)
            if cancel_completion:
                cancel_completion()
            if clear_correlation:
                clear_correlation()
        runtime.retired = True
        if not stop_handler:
            # Fallback transfers the sole consumer synchronously to new queues.
            runtime.handler = None
        if not runtime.shutdown_sent:
            runtime.shutdown_sent = True
            if runtime.request_queue is not None:
                runtime.request_queue.put(("__shutdown__", None))
        handler = runtime.handler
        if handler is not None and not handler.done():
            handler.cancel()
        self._schedule_tts_cleanup(runtime)
        return runtime

    async def _finish_retired_tts_runtime(self, runtime):
        handler = runtime.handler
        if handler is not None and handler is not asyncio.current_task():
            try:
                await asyncio.shield(handler)
            except asyncio.CancelledError:
                # Cancellation of the handler is expected. Cancellation of this
                # manager-owned task is not used as a cleanup protocol.
                if not handler.done():
                    raise
            except Exception:
                pass
        runtime.handoff_safe.set()
        # Join in short executor windows, retaining the record for as long as
        # the real worker remains alive. A timeout is never a capacity release.
        while runtime.thread is not None and runtime.thread.is_alive():
            await asyncio.to_thread(runtime.thread.join, 0.25)
        for queue in (runtime.request_queue, runtime.response_queue):
            if queue is not None:
                while True:
                    try:
                        queue.get_nowait()
                    except Exception:
                        break
        runtime.cleanup_complete.set()
        # Finished records may leave the capacity ledger, but the retirement
        # object itself remains valid for callers already waiting on its events.
        if runtime in self._tts_runtimes:
            self._tts_runtimes.remove(runtime)

    async def _wait_tts_capacity(self, deadline=None, *, worker=None):
        loop = asyncio.get_running_loop()
        if deadline is None:
            deadline_getter = getattr(self, "_current_start_deadline", None)
            deadline = deadline_getter() if deadline_getter else None
        if deadline is None:
            deadline = loop.time() + 15.0
        while self._live_tts_runtime_count() >= self._tts_capacity_limit(worker):
            for runtime in tuple(self._tts_runtimes):
                if runtime.retired:
                    self._schedule_tts_cleanup(runtime)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TtsCapacityError("TTS runtime capacity exhausted before startup deadline")
            await asyncio.sleep(min(0.02, remaining))
            check = getattr(self, "_check_start_operation", None)
            if check:
                check()
