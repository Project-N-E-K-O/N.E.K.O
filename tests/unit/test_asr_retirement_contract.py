"""A session must report resource retirement truthfully, including after cancellation."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy


pytestmark = pytest.mark.asyncio


class PhysicalSocket:
    """Expose both a close attempt and independent evidence of resource release."""

    def __init__(self, *, fail=False, abort_releases=False, blocked=False):
        self.fail = fail
        self.abort_releases = abort_releases
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.released = asyncio.Event()
        self.close_calls = 0
        self.abort_calls = 0
        self.transport = self
        if not blocked:
            self.allow_close.set()

    @property
    def closed(self):
        return self.released.is_set()

    def is_closing(self):
        return self.closed

    async def close(self):
        self.close_calls += 1
        self.close_entered.set()
        await self.allow_close.wait()
        if self.fail:
            raise OSError("injected close failure")
        self.released.set()

    async def wait_closed(self):
        await self.released.wait()

    def abort(self):
        self.abort_calls += 1
        if self.abort_releases:
            self.released.set()


class RegisteredWorker:
    def __init__(self, socket, *, final=False):
        self.socket = socket
        self.final = final

    async def __call__(self, requests, responses, api_key, config):
        from main_logic.asr_client.connection_cleanup import connection_registry

        registry = connection_registry(requests)
        registry.register(self.socket, worker_identity="contract-test")
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        try:
            while True:
                request = await requests.get()
                try:
                    if request.kind == "finish":
                        identity = dict(
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            utterance_id=1,
                        )
                        if self.final:
                            await responses.put(
                                _AsrWorkerEvent(kind="utterance_started", **identity)
                            )
                            await responses.put(
                                _AsrWorkerEvent(
                                    kind="final", text="accepted", **identity
                                )
                            )
                        await responses.put(
                            _AsrWorkerEvent(kind="finished", **identity)
                        )
                        return
                    if request.kind == "shutdown":
                        return
                finally:
                    requests.task_done()
        finally:
            await registry.retire_all()


async def make_session(worker):
    callback, errors = AsyncMock(), AsyncMock()
    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=callback,
        on_connection_error=errors,
        provider_policy=resolve_provider_policy("qwen", "provider"),
    )
    await session.connect()
    return session, callback, errors


@pytest.fixture
def short_retirement(monkeypatch):
    from main_logic.asr_client import connection_cleanup

    monkeypatch.setattr(connection_cleanup, "CLOSE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(connection_cleanup, "TASK_EXIT_TIMEOUT_SECONDS", 0.05)


async def test_failed_retirement_is_shared_by_concurrent_and_repeated_close(
    short_retirement,
):
    socket = PhysicalSocket(fail=True)
    session, callback, _ = await make_session(RegisteredWorker(socket))
    results = await asyncio.wait_for(
        asyncio.gather(session.close(), session.close(), return_exceptions=True),
        1,
    )
    assert all(isinstance(result, RuntimeError) for result in results)
    assert not socket.released.is_set()
    assert not session.is_ready
    calls = socket.close_calls
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(session.close(), 0.2)
    assert socket.close_calls == calls
    assert session._worker_task.done()
    callback.assert_not_called()


async def test_cancelled_close_waiter_does_not_cancel_physical_retirement(
    short_retirement,
):
    socket = PhysicalSocket(blocked=True)
    session, callback, errors = await make_session(RegisteredWorker(socket))
    waiter = asyncio.create_task(session.close())
    await asyncio.wait_for(socket.close_entered.wait(), 0.5)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not socket.released.is_set()
    assert not session.is_ready
    socket.allow_close.set()
    await asyncio.wait_for(session.close(), 0.5)
    assert socket.released.is_set()
    assert socket.close_calls == 1
    assert session._worker_task.done()
    callback.assert_not_called()
    errors.assert_not_called()


async def test_accepted_final_survives_resource_retirement_failure_once(
    short_retirement,
):
    socket = PhysicalSocket(fail=True)
    session, callback, _ = await make_session(RegisteredWorker(socket, final=True))
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(
            session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 0.8),
            1,
        )
    callback.assert_awaited_once_with("accepted")
    assert not socket.released.is_set()
    assert not session.is_ready
    with pytest.raises(RuntimeError):
        await session.close()
    callback.assert_awaited_once_with("accepted")


async def test_close_error_with_confirmed_abort_is_retired_successfully(
    short_retirement,
):
    socket = PhysicalSocket(fail=True, abort_releases=True)
    session, callback, errors = await make_session(RegisteredWorker(socket))
    await asyncio.wait_for(session.close(), 0.5)
    assert socket.released.is_set()
    assert socket.abort_calls == 1
    assert session._worker_task.done()
    assert not session.is_ready
    callback.assert_not_called()
    errors.assert_not_called()


async def test_released_socket_with_stubborn_child_is_bounded_retirement_failure(
    short_retirement,
):
    from main_logic.asr_client.connection_cleanup import connection_registry

    release_child, child_started = asyncio.Event(), asyncio.Event()
    socket = PhysicalSocket()
    children = []

    async def child():
        child_started.set()
        while not release_child.is_set():
            try:
                await release_child.wait()
            except asyncio.CancelledError:
                # Model a provider task that does not cooperate with cancellation.
                continue

    async def worker(requests, responses, api_key, config):
        task = asyncio.create_task(child())
        children.append(task)
        connection_registry(requests).register_tasks(task)
        await child_started.wait()
        await RegisteredWorker(socket)(requests, responses, api_key, config)

    session, callback, _ = await make_session(worker)
    try:
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(session.close(), 0.5)
        assert socket.released.is_set()
        assert not children[0].done()
        assert not session.is_ready
        callback.assert_not_called()
    finally:
        release_child.set()
        await asyncio.wait_for(asyncio.gather(*children), 0.5)
    # Later task completion cannot turn an earlier untrusted retirement into success.
    with pytest.raises(RuntimeError):
        await session.close()


async def test_aborted_socket_with_live_close_coroutine_is_not_retirement_success(
    short_retirement,
):
    from main_logic.asr_client.connection_cleanup import connection_registry

    release_close = asyncio.Event()

    class StubbornCloseSocket(PhysicalSocket):
        async def close(self):
            self.close_calls += 1
            self.close_entered.set()
            while not release_close.is_set():
                try:
                    await release_close.wait()
                except asyncio.CancelledError:
                    continue

    socket = StubbornCloseSocket(abort_releases=True)
    session, callback, _ = await make_session(RegisteredWorker(socket))
    try:
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(session.close(), 0.5)
        assert socket.released.is_set()
        assert socket.abort_calls == 1
        assert not session.is_ready
        callback.assert_not_called()
    finally:
        release_close.set()
        owner = connection_registry(session._request_queue).connections[0]
        await asyncio.wait_for(asyncio.gather(*owner._operations), 0.5)
    with pytest.raises(RuntimeError):
        await session.close()


async def test_closed_status_callback_can_reenter_close_without_self_wait(
    short_retirement,
):
    socket = PhysicalSocket()
    session, callback, errors = await make_session(RegisteredWorker(socket))
    statuses = []
    reentered = asyncio.Event()

    async def status_changed(status):
        statuses.append(status)
        if status == "ASR_CLOSED":
            await session.close()
            reentered.set()

    session._on_status_message = status_changed
    await asyncio.wait_for(session.close(), 0.5)
    assert reentered.is_set()
    assert statuses == ["ASR_CLOSED"]
    assert socket.released.is_set()
    assert socket.close_calls == 1
    assert session._worker_task.done()
    callback.assert_not_called()
    errors.assert_not_called()


async def test_second_worker_cancellation_cannot_skip_owned_physical_close(
    short_retirement,
):
    from main_logic.asr_client.connection_cleanup import connection_registry

    socket = PhysicalSocket(blocked=True)
    session, callback, _ = await make_session(RegisteredWorker(socket))
    session._worker_task.cancel()
    await asyncio.wait_for(socket.close_entered.wait(), 0.5)
    owner = connection_registry(session._request_queue).connections[0]
    session._worker_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session._worker_task
    assert not owner.cleanup_task.done()
    assert not socket.released.is_set()
    socket.allow_close.set()
    await asyncio.wait_for(session.close(), 0.5)
    assert owner.cleanup_task.done() and not owner.cleanup_task.cancelled()
    assert socket.released.is_set()
    assert socket.close_calls == 1
    assert not session.is_ready
    callback.assert_not_called()


async def test_step_reset_retires_socket_before_bounded_join_of_stubborn_sender(
    monkeypatch,
    short_retirement,
):
    from main_logic.asr_client.connection_cleanup import (
        ConnectionRetirementError,
        connection_registry,
    )
    from main_logic.asr_client.workers import step

    socket = PhysicalSocket()
    socket.send = AsyncMock()
    connect = AsyncMock(return_value=socket)
    started, release_sender = asyncio.Event(), asyncio.Event()

    async def resistant_sender(*args):
        started.set()
        while not release_sender.is_set():
            try:
                await release_sender.wait()
            except asyncio.CancelledError:
                continue
        return "shutdown", None

    async def reset_receiver(*args):
        await started.wait()
        return "reset"

    monkeypatch.setattr(step.websockets, "connect", connect)
    monkeypatch.setattr(step, "_step_sender", resistant_sender)
    monkeypatch.setattr(step, "_step_receiver", reset_receiver)
    requests, responses = asyncio.Queue(), asyncio.Queue()
    worker = asyncio.create_task(
        step.step_asr_worker(
            requests,
            responses,
            "test-key",
            AsrSessionConfig(endpointing_mode="provider"),
        )
    )
    try:
        done, _ = await asyncio.wait({worker}, timeout=0.5)
        assert worker in done, (
            "reset must not block forever joining the sender before close"
        )
        with pytest.raises(ConnectionRetirementError):
            worker.result()
        assert socket.released.is_set()
        assert socket.close_calls == 1
        connect.assert_awaited_once()
        assert any(not task.done() for task in connection_registry(requests).tasks)
    finally:
        release_sender.set()
        if not worker.done():
            worker.cancel()
        await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 0.5)
        await asyncio.wait_for(
            asyncio.gather(
                *connection_registry(requests).tasks, return_exceptions=True
            ),
            0.5,
        )


async def test_late_connection_registration_cannot_hide_retirement_failure(
    monkeypatch,
    short_retirement,
):
    from main_logic.asr_client import _infra
    from main_logic.asr_client.connection_cleanup import (
        ConnectionRetirementError,
        connection_registry,
    )

    monkeypatch.setattr(_infra, "_WORKER_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(_infra, "TASK_EXIT_TIMEOUT_SECONDS", 0.2)
    release_connect = asyncio.Event()
    cancelled_connect = asyncio.Event()
    socket = PhysicalSocket(blocked=True)

    async def late_worker(requests, responses, api_key, config):
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        await requests.get()
        requests.task_done()
        while not release_connect.is_set():
            try:
                await release_connect.wait()
            except asyncio.CancelledError:
                cancelled_connect.set()
        try:
            connection_registry(requests).register(socket, worker_identity="late-test")
        except ConnectionRetirementError:
            # Match the worker's generic setup-error path, which reports then returns.
            return

    session, callback, _ = await make_session(late_worker)
    registry = connection_registry(session._request_queue)
    registry_scanned = asyncio.Event()
    original_join = registry.join_tasks

    async def observed_join():
        await original_join()
        registry_scanned.set()

    monkeypatch.setattr(registry, "join_tasks", observed_join)
    waiter = asyncio.create_task(session.close())
    try:
        await asyncio.wait_for(cancelled_connect.wait(), 0.5)
        await asyncio.wait_for(registry_scanned.wait(), 0.5)
        assert registry.sealed and not registry.connections
        release_connect.set()
        with pytest.raises(ConnectionRetirementError):
            await asyncio.wait_for(waiter, 0.5)
        assert not socket.released.is_set()
        assert not session.is_ready
        callback.assert_not_called()
    finally:
        release_connect.set()
        socket.allow_close.set()
        await asyncio.gather(waiter, return_exceptions=True)
        registry = connection_registry(session._request_queue)
        if registry.connections:
            await asyncio.wait_for(registry.connections[0].cleanup_task, 0.5)
    assert socket.released.is_set()
    with pytest.raises(ConnectionRetirementError):
        await session.close()
