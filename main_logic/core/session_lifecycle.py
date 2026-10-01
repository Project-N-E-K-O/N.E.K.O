"""Ownership and retained retirement records for the conversation lifecycle.

An operation owns publication; a connection owns callbacks; a retirement owns
cleanup.  The handoff event deliberately precedes physical resource release.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
from functools import wraps

from ._shared import FRONTEND_START_SESSION_TIMEOUT_SECONDS, logger
from .session_records import (
    MAX_LIVE_LLM_CONNECTIONS, ConnectionRecord, Retirement, StartOperation, _start_context,
)


class SessionOwnershipMixin:
    def _init_session_lifecycle_state(self):
        if "_session_retirements" in self.__dict__:
            return
        self._session_generation = 0
        self._start_operation = None
        self._session_retirements = []
        self._connection_records = []
        self._session_cleanup_tasks = set()
        self._idle_memory_barriers = set()

    def _current_start_request(self):
        operation = _start_context.get()
        if operation is None or operation.manager is not self or operation.finished.is_set():
            return None
        return operation

    def _check_start_operation(self, operation=None):
        operation = operation or self._current_start_request()
        if operation is not None and (
            not operation.valid or getattr(self, "_start_operation", None) is not operation
        ):
            raise asyncio.CancelledError("session start operation retired")

    def _current_start_deadline(self):
        operation = self._current_start_request()
        # Long-lived handlers inherit the start context, but startup's budget
        # stops governing recovery once publication has finished.
        return operation.deadline if operation is not None and not operation.finished.is_set() else (
            asyncio.get_running_loop().time() + FRONTEND_START_SESSION_TIMEOUT_SECONDS
        )

    async def _wait_session_end(self, task):
        """Bound the caller's wait while the manager retains physical cleanup."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FRONTEND_START_SESSION_TIMEOUT_SECONDS
        record = next((item for item in self._session_retirements if item.task is task), None)
        if record is not None and callable(record.memory_callback):
            deadline += max(0.0, record.retry_kwargs.get("memory_settlement_timeout", 15.0))
        handoff = asyncio.create_task(record.handoff_finished.wait()) if record is not None else None
        try:
            watched = (task, handoff) if handoff is not None else (task,)
            done, _ = await asyncio.wait(
                watched, timeout=max(0, deadline - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return task.result()  # Preserve close errors and cancellation.
            if handoff is None or not handoff.done():
                raise TimeoutError("Session end did not reach safe handoff")
            if record.handoff_error is not None:
                raise RuntimeError("Session end handoff failed") from record.handoff_error
            # Preserve prompt physical cleanup/error reporting, without making
            # existing end-then-start callers depend on an uncooperative worker.
            done, _ = await asyncio.wait(
                (task,), timeout=min(2.0, max(0, deadline - loop.time())),
            )
            if task in done:
                return task.result()
        finally:
            if handoff is not None:
                handoff.cancel()
                await asyncio.gather(handoff, return_exceptions=True)

    async def _wait_session_handoff(self, deadline):
        self._init_session_lifecycle_state()
        async with asyncio.timeout_at(deadline):
            for record in tuple(self._session_retirements):
                self._retry_session_retirement(record)
                await record.handoff_finished.wait()
                if record.handoff_error is not None and not record.handoff_safe.is_set():
                    raise RuntimeError("Session handoff failed") from record.handoff_error
                # Timeout fallback is not memory settlement. A late isolation
                # callback must run before another conversation can produce.
                if record.memory_completion is not None:
                    try:
                        await asyncio.shield(record.memory_completion)
                    except Exception as exc:
                        record.memory_boundary_sent = False
                        record.handoff_safe.clear()
                        record.handoff_error = exc
                        raise RuntimeError("Session memory settlement failed") from exc
            for completion in tuple(self._idle_memory_barriers):
                await asyncio.shield(completion)

    def _claim_start_operation(self, websocket, request_id, input_mode, deadline, *, advance_generation=True):
        self._init_session_lifecycle_state()
        generation = self._session_generation + 1
        if advance_generation:
            self._session_generation = generation
        operation = StartOperation(
            self, generation, websocket, request_id,
            input_mode, deadline, asyncio.current_task(),
        )
        self._start_operation = operation
        operation.pending_inputs = tuple(self.pending_input_data)
        self._starting_session_count = 1
        self._starting_input_mode = input_mode
        return operation, _start_context.set(operation)

    async def _discard_start_reservation_inputs(self, operation):
        async with self.input_cache_lock:
            if self._start_operation is operation:
                previous_ids = {id(item) for item in operation.pending_inputs}
                self.pending_input_data[:] = [item for item in self.pending_input_data if id(item) in previous_ids]
                self._clear_pending_context_appends()

    def _consume_start_retirement_cancellation(self, error):
        task = asyncio.current_task()
        if error.args == ("session start operation retired",) and not task.cancelling():
            return True
        if error.args != ("session start retired",) or task.cancelling() > 1:
            return False
        task.uncancel()
        return True

    def _finish_start_operation(self, operation, token):
        operation.finished.set()
        if self._start_operation is operation:
            self._starting_session_count = 0
            self._starting_input_mode = None
        _start_context.reset(token)

    def _own_cleanup_task(self, coro):
        self._init_session_lifecycle_state()
        # A cleanup is not a child start phase and must not inherit publication
        # permission from the caller whose cancellation it survives.
        context = contextvars.copy_context()
        context.run(_start_context.set, None)
        task = asyncio.create_task(coro, context=context)
        self._session_cleanup_tasks.add(task)
        def completed(done):
            self._session_cleanup_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.error("Session cleanup failed: %s", done.exception())
        task.add_done_callback(completed)
        return task

    def _connection_record(self, session):
        self._init_session_lifecycle_state()
        return next((record for record in self._connection_records
                     if record.session is session), None)

    def _register_connection(self, session):
        record = self._connection_record(session)
        if record is not None:
            return record
        record = ConnectionRecord(session, session.close, self._current_start_request())
        self._connection_records.append(record)
        return record

    async def _close_owned_session(self, session):
        """Retire a manager-owned client without changing provider close/reconnect."""
        record = self._register_connection(session)
        await asyncio.shield(self._close_connection_record(record))

    def _schedule_session_input_flush(self, reservation):
        """Replay inputs as work of the installed connection, after startup."""
        session = self.session
        record = self._register_connection(session)

        async def flush():
            if self.session is session and not record.retired:
                await self._flush_pending_input_data()

        context = contextvars.copy_context()
        context.run(_start_context.set, None)
        task = asyncio.create_task(flush(), context=context)
        record.callbacks.add(task)
        self._bg_tasks.add(task)

        def finished(done):
            record.callbacks.discard(done)
            self._bg_tasks.discard(done)
            should_retry = False
            if getattr(self, '_pending_input_flush_scheduled', None) is reservation:
                self._pending_input_flush_scheduled = None
                should_retry = (
                    not done.cancelled()
                    and done.exception() is None
                    and self.session is record.session
                    and not record.retired
                    and bool(self.pending_input_data)
                    and not getattr(self, '_deferred_pending_input_flush_count', 0)
                )
                if should_retry:
                    self._pending_input_flush_scheduled = reservation
                    self._schedule_session_input_flush(reservation)
            if not done.cancelled() and done.exception() is not None:
                logger.error('Session input flush failed: %s', done.exception())

        task.add_done_callback(finished)
        return task

    def _close_connection_record(self, record, *, initiating_task=None):
        record.retired = True
        # A failed close leaves the physical state unknown and therefore keeps
        # capacity reserved. A later retirement attempt may retry the provider
        # close and release that slot only after a confirmed success.
        if record.close_task is not None and record.close_task.done() and not record.closed:
            record.close_task = None
        if record.close_task is None:
            initiating_task = initiating_task or asyncio.current_task()
            async def close():
                callbacks = tuple(record.callbacks - {initiating_task})
                for callback in callbacks:
                    if not callback.done():
                        callback.cancel()
                close_error = None
                # A cancelled handshake can still own network work. Keep its
                # slot until it has stopped, and close once more if it finished
                # after the first close attempt.
                try:
                    connecting = record.connect_task
                    if connecting is not None and not connecting.done():
                        connecting.cancel()
                        try:
                            await record.close()
                        except Exception as error:
                            # The join and the authoritative close below still
                            # have to run, or a cancelled handshake keeps
                            # writing past this boundary.
                            logger.warning('Session close before handshake join failed: %s', error)
                        await asyncio.gather(connecting, return_exceptions=True)
                    await record.close()
                except BaseException as error:
                    close_error = error
                finally:
                    # Hot-swap uses this close boundary before promotion.
                    # Provider output may run outside the receive loop, so
                    # stopping only that loop does not stop all old writes.
                    # Keep the resource registered until every other owned
                    # callback has unwound, even when provider close failed.
                    if callbacks:
                        await asyncio.gather(*callbacks, return_exceptions=True)
                    # ``closed`` is the physical-release acknowledgement used
                    # by capacity counting and pruning. If the authoritative
                    # close raised, the provider may still own a live socket;
                    # keep this record occupying its slot rather than claiming
                    # a safe handoff we cannot prove.
                    record.closed = close_error is None
                if close_error is not None:
                    raise close_error
            record.close_task = self._own_cleanup_task(close())
        return record.close_task

    async def _connect_owned_session(self, session, *args, **kwargs):
        self._init_session_lifecycle_state()
        # Include old/prewarmed objects supplied by integrations before the
        # registry was initialized, as well as candidates still connecting.
        for existing in (getattr(self, "session", None), getattr(self, "pending_session", None)):
            if existing is not None and existing is not session:
                self._register_connection(existing)
        deadline = self._current_start_deadline()
        # Providers can opt into serialization without adding provider names
        # or routing policy to the conversation manager.
        async with asyncio.timeout_at(deadline):
            existing_record = self._connection_record(session)
            if existing_record is not None and existing_record.retired:
                raise RuntimeError('Cannot reconnect a manager-retired session')
            retried = set()
            while True:
                live = [record for record in self._connection_records
                        if not record.closed and record is not existing_record]
                for stale in live:
                    if (stale.retired and stale not in retried
                            and (stale.close_task is None or stale.close_task.done())):
                        # Retry once per admission, not every 20ms. Failure
                        # still occupies capacity and the startup budget bounds
                        # waiting; a subsequent request may make a fresh attempt.
                        retried.add(stale)
                        self._close_connection_record(stale)
                serial = not getattr(session, 'supports_session_overlap', True) or any(
                    not getattr(record.session, 'supports_session_overlap', True) for record in live
                )
                if len(live) < (1 if serial else MAX_LIVE_LLM_CONNECTIONS):
                    break
                self._check_start_operation()
                await asyncio.sleep(0.02)
            self._check_start_operation()
            record = self._register_connection(session)
            record.connect_task = asyncio.create_task(session.connect(*args, **kwargs))
            try:
                done, _ = await asyncio.wait(
                    {record.connect_task}, timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
                )
                if not done:
                    raise TimeoutError('LLM connection exceeded startup deadline')
                record.connect_task.result()
                self._check_start_operation()
            except BaseException:
                self._close_connection_record(record)
                raise

    def _bind_owned_output_callbacks(self, session):
        """Track real callback tasks so retirement can drain in-flight writes.

        Pending clients retain their preparation/control callbacks. Audible and
        memory-producing callbacks only run for the installed connection.
        """
        # Binding occurs before connect. Registration (and capacity reservation)
        # happens immediately before the actual external connect operation.
        for name in (
            "on_text_delta", "on_audio_delta", "on_audio_done", "on_new_message",
            "on_input_transcript", "on_input_transcript_with_route",
            "on_output_transcript", "on_response_done", "on_response_discarded",
            "on_repetition_detected", "on_status_message", "on_proactive_done",
            "on_thinking_active", "on_sid_rotate",
        ):
            callback = getattr(session, name, None)
            if not callable(callback) or getattr(callback, "_session_owner", None) is session:
                continue
            @wraps(callback)
            async def guarded(*args, _callback=callback, **kwargs):
                record = self._connection_record(session)
                if session is not self.session or (record is not None and record.retired):
                    return
                task = asyncio.current_task()
                registered_here = record is not None and task not in record.callbacks
                if registered_here:
                    record.callbacks.add(task)
                # A message listener created during activation inherits the
                # start context. Output belongs to the installed connection.
                token = _start_context.set(None)
                try:
                    result = _callback(*args, **kwargs)
                    return await result if inspect.isawaitable(result) else result
                finally:
                    _start_context.reset(token)
                    if registered_here:
                        record.callbacks.discard(task)
            guarded._session_owner = session
            setattr(session, name, guarded)
        for name in ('get_host_turn_id',):
            callback = getattr(session, name, None)
            if not callable(callback) or getattr(callback, '_session_owner', None) is session:
                continue
            @wraps(callback)
            def guarded_sync(*args, _callback=callback, **kwargs):
                record = self._connection_record(session)
                if session is not self.session or (record is not None and record.retired):
                    return None
                return _callback(*args, **kwargs)
            guarded_sync._session_owner = session
            setattr(session, name, guarded_sync)

    async def _run_owned_lifecycle_callback(self, session, callback, *args, **kwargs):
        record = self._connection_record(session)
        if record is not None and record.retired:
            return
        task = asyncio.current_task()
        registered_here = record is not None and task not in record.callbacks
        if registered_here:
            record.callbacks.add(task)
        try:
            return await callback(*args, **kwargs)
        finally:
            if registered_here:
                record.callbacks.discard(task)

    def request_end_session(
        self, by_server=False, *, expected_session=None, reset_starting_count=True,
        after_memory_settlement=None, memory_settlement_timeout=15.0,
        preserve_pending_input=False,
    ):
        """Accept and bind an end request without a scheduling or lock gap."""
        self._init_session_lifecycle_state()
        session = getattr(self, "session", None)
        operation = getattr(self, "_start_operation", None)
        if expected_session is not None and expected_session is not session:
            return self._own_cleanup_task(asyncio.sleep(0))
        # Accept user intent even when teardown of these resources is already
        # owned by another end request. Starts waiting for that handoff must stop.
        if not by_server and reset_starting_count:
            self._user_session_abandon_epoch = getattr(self, "_user_session_abandon_epoch", 0) + 1
        tts = self._snapshot_tts_runtime()
        for previous in reversed(self._session_retirements):
            if previous.generation == self._session_generation and (
                previous.session is session or session is None
            ) and previous.tts is tts and (
                not reset_starting_count or previous.resets_operation
            ) and (
                not callable(after_memory_settlement)
                or previous.memory_callback is after_memory_settlement
            ):
                self._retry_session_retirement(previous)
                return previous.task
        caller = asyncio.current_task()
        # A server-side cleanup callback may be stale while a new start is
        # still preparing. Only an explicit user end, or a server end that has
        # a live session target, is allowed to revoke that start operation.
        if reset_starting_count and operation is not None and (
            not by_server or session is not None
        ):
            operation.valid = False
        record = Retirement(
            self._session_generation, operation if reset_starting_count else None,
            session, getattr(self, "websocket", None),
            getattr(self, "message_handler_task", None),
            tts, caller, bool(getattr(self, "is_active", False)),
        )
        record.resets_operation = reset_starting_count
        record.pending_inputs = tuple(self.pending_input_data) + tuple(
            getattr(self, "_pending_input_flush_batch", ())
        )
        record.memory_callback = after_memory_settlement
        record.preparation = getattr(self, 'background_preparation_task', None)
        record.swap = getattr(self, 'final_swap_task', None)
        self._session_retirements.append(record)
        if session is not None:
            connection = self._register_connection(session)
            connection.retired = True
        if record.tts is not None:
            self._retire_tts_runtime(record.tts)
        record.retry_kwargs = dict(
            by_server=by_server, reset_starting_count=reset_starting_count,
            after_memory_settlement=after_memory_settlement,
            memory_settlement_timeout=memory_settlement_timeout,
            preserve_pending_input=preserve_pending_input,
        )
        record.task = self._own_cleanup_task(self._retire_session_resources(record, **record.retry_kwargs))
        return record.task

    def _retry_session_retirement(self, record):
        # A failed isolation remains owned, but is retryable. Never pretend
        # that an exception proves physical release or a safe handoff.
        if (record.task is not None and record.task.done()
                and not record.handoff_safe.is_set()):
            record.handoff_error = None
            record.handoff_finished.clear()
            record.task = self._own_cleanup_task(self._retire_session_resources(record, **record.retry_kwargs))

    async def _retire_session_resources(
        self, record, **kwargs,
    ):
        try:
            return await self._retire_session_resources_owned(record, **kwargs)
        except BaseException as exc:
            if not record.handoff_safe.is_set() and record.handoff_error is None:
                record.handoff_error = exc
            raise
        finally:
            record.handoff_finished.set()

    async def _retire_session_resources_owned(
        self, record, *, by_server, reset_starting_count, after_memory_settlement,
        memory_settlement_timeout, preserve_pending_input,
    ):
        # Internal replacement and a user cancel can target different parts of
        # the same startup. Their state handoffs remain strictly ordered.
        for predecessor in tuple(self._session_retirements):
            if predecessor is record:
                break
            self._retry_session_retirement(predecessor)
            await predecessor.handoff_finished.wait()
        close_tasks = []
        operation = record.operation
        # Cancellation is requested before waiting. The manager retains this
        # worker even when an end/cleanup caller itself gets cancelled.
        producers = set()
        if operation is not None and not operation.finished.is_set():
            producers.update(operation.children)
            # The caller may be a receive loop whose finally waits for this
            # retirement. Fence the operation and join its phases, not that
            # long-lived caller's unrelated cleanup.
            if operation.task is not record.initiating_task:
                operation.task.cancel("session start retired")
        for task in (record.listener, record.preparation, record.swap):
            if task is not None and task is not record.initiating_task:
                producers.add(task)
        connection = self._connection_record(record.session) if record.session is not None else None
        if connection is not None:
            producers.update(connection.callbacks - {record.initiating_task})
        for task in producers:
            if not task.done():
                task.cancel()
        async with self.lock:
            owns_state = self._session_generation == record.generation and (
                self.session is record.session or (record.state_detached and self.session is None)
            )
            if owns_state:
                self.session = None
                record.state_detached = True
                self.is_active = False
                if self.message_handler_task is record.listener:
                    self.message_handler_task = None
        if connection is not None:
            close_tasks.append(self._close_connection_record(
                connection, initiating_task=record.initiating_task,
            ))
        if producers:
            # A cancellation-resistant writer is NOT handoff-safe. New starts
            # time out on their own shared deadline while this owner keeps it.
            await asyncio.gather(*producers, return_exceptions=True)
        if operation is not None and operation.task is not record.initiating_task:
            await operation.finished.wait()
        if operation is not None:
            for candidate in self._connection_records:
                if candidate.operation is operation and not candidate.closed:
                    close_tasks.append(self._close_connection_record(candidate))
        if owns_state and not record.asr_detached:
            self._reset_proactive_gate()
            self.clear_speech_playback_gains()
            try:
                await self._close_independent_asr(next_route_mode="blocked")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record.handoff_error = exc
                logger.exception("Session ASR teardown failed before handoff: %s", exc)
                # _close_independent_asr detaches manager-owned callbacks
                # before awaiting provider cleanup. A post-detach cleanup
                # error is reported to the current end request, but it must
                # not pin the next session behind a historical failure.
            record.asr_detached = True
            owns_state = self.session is None and self._session_generation == record.generation
        if owns_state:
            if record.was_active and not record.renewal_complete:
                try:
                    await self._init_renew_status()
                    record.renewal_complete = True
                except Exception as exc:
                    record.handoff_error = exc
                    logger.exception("Session renewal failed during retirement")
                owns_state = self.session is None and self._session_generation == record.generation
        if owns_state and not record.stream_state_cleared:
            if record.was_active:
                self._activity_tracker.on_voice_mode(False)
            self._audio_stream_epoch += 1
            self._clear_audio_stream_queue("end_session")
            self._cancel_audio_stream_worker("end_session")
            self._reset_voice_echo_suppression_cache()
            async with self.input_cache_lock:
                if self._session_generation == record.generation and self.session is None:
                    if reset_starting_count or record.was_active:
                        self.session_ready = False
                        if not preserve_pending_input:
                            # Inputs queued for a waiting successor arrived
                            # after this end was accepted and belong to it.
                            retired_input_ids = {id(item) for item in record.pending_inputs}
                            self.pending_input_data[:] = [
                                item for item in self.pending_input_data
                                if id(item) not in retired_input_ids
                            ]
                        self._clear_pending_context_appends()
            async with self.lock:
                if reset_starting_count and self._start_operation is operation:
                    self._starting_session_count = 0
                    self._starting_input_mode = None
            self._reset_tts_retry_state()
            self.last_time = None
            record.stream_state_cleared = True
        # Release ledger entries that pointed into the retired session's queue.
        # Prune rather than clear: a replacement session may already have
        # staged (and recorded) attachments while this teardown was awaiting.
        # That replacement case is why the prune exists, so it stays outside the
        # ``owns_state`` guard, which by this point requires ``session is None``.
        self._prune_request_staged_images()
        # TTS owns and drains its handler; physical worker exit is independent.
        if record.tts is not None:
            handler = record.tts.handler
            if handler is not None and handler is not record.initiating_task and not handler.done():
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)
        if owns_state:
            if callable(after_memory_settlement) and not record.memory_boundary_sent:
                try:
                    if record.memory_settled:
                        # The connector already wrote this session. Retry only
                        # the failed local isolation, not terminal settlement.
                        result = after_memory_settlement()
                        if inspect.isawaitable(result):
                            await result
                        record.memory_completion = None
                    else:
                        if record.memory_completion is None or record.memory_completion.done():
                            def settled_callback():
                                record.memory_settled = True
                                return after_memory_settlement()

                            record.memory_completion = self._queue_session_end_memory_barrier(settled_callback)
                        await self._wait_for_session_end_memory_barrier(
                            record.memory_completion, after_memory_settlement,
                            timeout_seconds=memory_settlement_timeout,
                        )
                    record.memory_boundary_sent = True
                except Exception as exc:
                    record.handoff_error = exc
                    logger.exception("Session memory settlement failed during retirement")
            elif record.was_active and not callable(after_memory_settlement) and not record.memory_boundary_sent:
                self.sync_message_queue.put({'type': 'system', 'data': 'session end'})
                record.memory_boundary_sent = True
            if not by_server and not record.departure_notified:
                try:
                    await self.send_status(json.dumps({
                        "code": "CHARACTER_LEFT", "details": {"name": self.lanlan_name},
                    }))
                    record.departure_notified = True
                except Exception as exc:
                    record.handoff_error = exc
                    logger.exception("Session departure notification failed")
        if not owns_state or (
            (not record.was_active or record.renewal_complete)
            and (not callable(after_memory_settlement) or record.memory_boundary_sent)
        ):
            record.handoff_safe.set()
        record.handoff_finished.set()
        if record.tts is not None and record.tts.cleanup_task is not None:
            close_tasks.append(record.tts.cleanup_task)
        cleanup_errors = []
        if close_tasks:
            results = await asyncio.gather(*close_tasks, return_exceptions=True)
            cleanup_errors = [
                result for result in results if isinstance(result, BaseException)
            ]
        record.cleanup_complete.set()
        # Retain unresolved isolation and live resources, not a lifetime-long
        # history of closed sockets and completed operations.
        self._connection_records[:] = [item for item in self._connection_records if not item.closed]
        self._session_retirements[:] = [
            item for item in self._session_retirements
            if not item.cleanup_complete.is_set()
            or not item.handoff_safe.is_set()
            or (item.memory_completion is not None and not item.memory_completion.done())
        ]
        if record.handoff_error is not None:
            raise RuntimeError("Session end handoff failed") from record.handoff_error
        if cleanup_errors:
            raise cleanup_errors[0]
