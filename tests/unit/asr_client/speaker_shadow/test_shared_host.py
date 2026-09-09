from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from main_logic.asr_client.speaker_shadow.shared_host import (
    HostGenerationReceipt,
    PhysicalScoreResponse,
    SharedHostCapacityError,
    SharedHostClosedError,
    SharedHostIdentityError,
    SharedHostResultStatus,
    SharedHostScoreError,
    SharedSpeakerScoringHostManager,
    SpeakerHostIdentity,
    SpeakerScoringLane,
    SpeakerScoringMode,
)


class _FakePhysicalHost:
    def __init__(self, factory: _FakeFactory, generation: int) -> None:
        self.factory = factory
        self.generation = generation
        self._process_count = 0
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[SpeakerScoringMode, bytes]] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.response_mutator: (
            Callable[[PhysicalScoreResponse], PhysicalScoreResponse] | None
        ) = None
        self.score_error = False
        self.close_succeeds = True
        self.terminate_succeeds = True

    @property
    def process_count(self) -> int:
        return self._process_count

    async def load(self, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self._process_count = 1
        self.factory.max_live_hosts = max(
            self.factory.max_live_hosts,
            sum(host.process_count for host in self.factory.hosts),
        )
        return True

    async def score(
        self,
        pcm16: bytearray,
        *,
        sample_rate_hz: int,
        mode: SpeakerScoringMode,
        host_generation: int,
        request_id: int,
        timeout_seconds: float,
    ) -> PhysicalScoreResponse:
        assert sample_rate_hz == 16_000
        assert timeout_seconds > 0
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((mode, bytes(pcm16)))
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if self.score_error:
                raise RuntimeError("fake score failure")
            response = PhysicalScoreResponse(
                host_generation,
                request_id,
                0.75,
            )
            if self.response_mutator is not None:
                response = self.response_mutator(response)
            return response
        finally:
            self.active -= 1

    async def close(self, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        if self.close_succeeds:
            self._process_count = 0
        return self.close_succeeds

    async def terminate(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0
        if self.terminate_succeeds:
            self._process_count = 0
        if self.release is not None and self.terminate_succeeds:
            self.release.set()


class _FakeFactory:
    def __init__(self) -> None:
        self.hosts: list[_FakePhysicalHost] = []
        self.max_live_hosts = 0

    def __call__(
        self,
        identity: SpeakerHostIdentity,
        host_generation: int,
    ) -> Awaitable[_FakePhysicalHost]:
        assert identity.profile_generation

        async def create() -> _FakePhysicalHost:
            assert sum(host.process_count for host in self.hosts) == 0
            host = _FakePhysicalHost(self, host_generation)
            self.hosts.append(host)
            return host

        return create()


def _identity(suffix: str = "1") -> SpeakerHostIdentity:
    return SpeakerHostIdentity(
        f"profile-{suffix}",
        f"model-{suffix}",
        f"config-{suffix}",
    )


def _manager(factory: _FakeFactory, **changes) -> SharedSpeakerScoringHostManager:
    values = {
        "max_outstanding_jobs": 4,
        "max_buffered_pcm_bytes": 32,
        "close_timeout_seconds": 0.05,
    }
    values.update(changes)
    return SharedSpeakerScoringHostManager(factory, **values)


async def _install(
    manager: SharedSpeakerScoringHostManager,
    identity: SpeakerHostIdentity | None = None,
) -> HostGenerationReceipt:
    return await manager.install(
        identity or _identity(),
        absolute_deadline=manager.now() + 1.0,
    )


def _submit(
    manager: SharedSpeakerScoringHostManager,
    generation: HostGenerationReceipt,
    *,
    lane: SpeakerScoringLane = SpeakerScoringLane.PREWIRE,
    mode: SpeakerScoringMode = SpeakerScoringMode.STANDARD,
    deadline_seconds: float = 1.0,
    pcm16: bytes = b"\x01\x00" * 4,
):
    return manager.submit(
        pcm16,
        sample_rate_hz=16_000,
        generation=generation,
        lane=lane,
        mode=mode,
        absolute_deadline=manager.now() + deadline_seconds,
    )


@pytest.mark.asyncio
async def test_serializes_lanes_and_preserves_per_request_mode() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()

    standard = _submit(manager, generation)
    short = _submit(
        manager,
        generation,
        lane=SpeakerScoringLane.SHADOW,
        mode=SpeakerScoringMode.SHORT_PROBE,
    )
    await host.started.wait()
    await asyncio.sleep(0)
    assert len(host.calls) == 1
    host.release.set()

    first, second = await asyncio.gather(
        manager.await_result(standard), manager.await_result(short)
    )
    assert first.status is SharedHostResultStatus.COMPLETED
    assert second.status is SharedHostResultStatus.COMPLETED
    assert [call[0] for call in host.calls] == [
        SpeakerScoringMode.STANDARD,
        SpeakerScoringMode.SHORT_PROBE,
    ]
    assert host.max_active == 1
    await manager.close()


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_cancel_shared_lane() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    lease = manager.lease(
        generation,
        lane=SpeakerScoringLane.PREWIRE,
        mode=SpeakerScoringMode.STANDARD,
        timeout_seconds=1.0,
    )

    cancelled_waiter = asyncio.create_task(lease.score_async(b"\x01\x00" * 4, 16_000))
    await host.started.wait()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    next_receipt = _submit(manager, generation)
    host.release.set()
    result = await manager.await_result(next_receipt)
    assert result.status is SharedHostResultStatus.COMPLETED
    assert len(host.calls) == 2
    assert host.max_active == 1
    await manager.close()


@pytest.mark.asyncio
async def test_host_like_lease_uses_fresh_deadline_and_reports_state() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    lease = manager.lease(
        generation,
        lane=SpeakerScoringLane.SHADOW,
        mode=SpeakerScoringMode.SHORT_PROBE,
        timeout_seconds=0.001,
    )

    assert lease.alive is True
    assert lease.loaded is True
    assert lease.process_count == 1
    assert lease.pcm_bytes_in_use == 0
    assert await lease.score(b"\x01\x00" * 4, timeout_seconds=1.0) == 0.75
    assert factory.hosts[0].calls[0][0] is SpeakerScoringMode.SHORT_PROBE
    assert await lease.close(timeout_seconds=0.01) is True
    assert lease.alive is False
    assert lease.loaded is False
    assert lease.process_count == 0
    assert manager.snapshot()["physical_host_count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_lease_close_cancels_only_its_queued_work() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    closing_lease = manager.lease(
        generation,
        lane=SpeakerScoringLane.PREWIRE,
        mode=SpeakerScoringMode.STANDARD,
        timeout_seconds=1.0,
    )
    other_lease = manager.lease(
        generation,
        lane=SpeakerScoringLane.SHADOW,
        mode=SpeakerScoringMode.STANDARD,
        timeout_seconds=1.0,
    )
    active = asyncio.create_task(other_lease.score_async(b"\x01\x00" * 4, 16_000))
    await host.started.wait()
    queued = asyncio.create_task(closing_lease.score_async(b"\x02\x00" * 4, 16_000))
    await asyncio.sleep(0)

    assert await closing_lease.close(timeout_seconds=0.01) is True
    with pytest.raises(SharedHostScoreError) as error:
        await queued
    assert error.value.status is SharedHostResultStatus.CANCELLED
    host.release.set()
    assert await active == 0.75
    assert len(host.calls) == 1
    assert other_lease.alive is True
    assert manager.snapshot()["cancelled_count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_expired_queued_job_never_reaches_physical_host() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    first = _submit(manager, generation)
    await host.started.wait()
    expired = _submit(manager, generation, deadline_seconds=0.01)
    await asyncio.sleep(0.05)
    host.release.set()

    assert (
        await manager.await_result(first)
    ).status is SharedHostResultStatus.COMPLETED
    expired_result = await manager.await_result(expired)
    assert expired_result.status is SharedHostResultStatus.TIMED_OUT
    assert expired_result.error_code == "deadline_expired_in_queue"
    assert len(host.calls) == 1
    await manager.close()


@pytest.mark.asyncio
async def test_capacity_limits_jobs_and_pcm_bytes() -> None:
    factory = _FakeFactory()
    manager = _manager(
        factory,
        max_outstanding_jobs=1,
        max_buffered_pcm_bytes=8,
    )
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    receipt = _submit(manager, generation)
    await host.started.wait()

    with pytest.raises(SharedHostCapacityError, match="outstanding_job_capacity"):
        _submit(manager, generation)
    assert manager.snapshot()["buffered_pcm_bytes"] == 8
    host.release.set()
    await manager.await_result(receipt)
    await manager.close()


@pytest.mark.asyncio
async def test_physical_response_identity_mismatch_fences_manager() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.response_mutator = lambda response: PhysicalScoreResponse(
        response.host_generation,
        response.request_id + 1,
        response.score,
    )
    receipt = _submit(manager, generation)

    result = await manager.await_result(receipt)
    assert result.status is SharedHostResultStatus.STALE
    assert result.error_code == "physical_response_identity_mismatch"
    assert manager.snapshot()["physical_host_count"] == 0
    assert manager.snapshot()["terminal_count"] == 1
    with pytest.raises(SharedHostClosedError):
        _submit(manager, generation)
    await manager.close()


@pytest.mark.asyncio
async def test_physical_failure_is_counted_and_fences_manager() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    factory.hosts[0].score_error = True
    receipt = _submit(manager, generation)

    result = await manager.await_result(receipt)
    assert result.status is SharedHostResultStatus.FAILED
    assert result.error_code == "physical_score_failed"
    snapshot = manager.snapshot()
    assert snapshot["failed_count"] == 1
    assert snapshot["physical_host_count"] == 0
    assert snapshot["terminal_count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_active_hang_terminates_and_fences_without_second_host() -> None:
    factory = _FakeFactory()
    manager = _manager(factory, close_timeout_seconds=0.02)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    receipt = _submit(manager, generation, deadline_seconds=0.01)

    result = await manager.await_result(receipt)
    assert result.status is SharedHostResultStatus.TIMED_OUT
    assert manager.snapshot()["host_termination_count"] == 1
    assert manager.snapshot()["terminal_count"] == 1
    assert len(factory.hosts) == 1
    with pytest.raises(SharedHostClosedError):
        await _install(manager, _identity("2"))
    await manager.close()


@pytest.mark.asyncio
async def test_late_physical_host_creation_is_terminated_after_deadline() -> None:
    release = asyncio.Event()
    created = asyncio.Event()
    terminated = asyncio.Event()

    class _LateHost(_FakePhysicalHost):
        def __init__(self) -> None:
            super().__init__(_FakeFactory(), 1)
            self._process_count = 1

        async def terminate(self, *, timeout_seconds: float) -> None:
            await super().terminate(timeout_seconds=timeout_seconds)
            terminated.set()

    async def create_late_host(
        _identity: SpeakerHostIdentity,
        _generation: int,
    ) -> _LateHost:
        host = _LateHost()
        created.set()
        await release.wait()
        return host

    manager = SharedSpeakerScoringHostManager(
        create_late_host,
        max_outstanding_jobs=1,
        max_buffered_pcm_bytes=8,
        close_timeout_seconds=0.05,
    )
    with pytest.raises(SharedHostScoreError) as error:
        await manager.install(
            _identity(),
            absolute_deadline=manager.now() + 0.01,
        )
    assert error.value.status is SharedHostResultStatus.TIMED_OUT
    assert created.is_set()

    release.set()
    await asyncio.wait_for(terminated.wait(), timeout=1.0)
    assert manager.snapshot()["physical_host_count"] == 0
    assert manager.snapshot()["terminal_count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_reload_closes_old_host_before_creating_new_host() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    first = await _install(manager)
    second = await _install(manager, _identity("2"))

    assert second.host_generation > first.host_generation
    assert len(factory.hosts) == 2
    assert factory.max_live_hosts == 1
    assert factory.hosts[0].process_count == 0
    assert factory.hosts[1].process_count == 1
    with pytest.raises(SharedHostIdentityError):
        _submit(manager, first)
    assert manager.snapshot()["reload_count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_reload_invalidates_active_and_queued_receipts() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    first_generation = await _install(manager)
    first_host = factory.hosts[0]
    first_host.release = asyncio.Event()
    active = _submit(manager, first_generation)
    await first_host.started.wait()
    queued = _submit(manager, first_generation)

    second_generation = await _install(manager, _identity("2"))
    assert (await manager.await_result(active)).status is SharedHostResultStatus.STALE
    assert (await manager.await_result(queued)).status is SharedHostResultStatus.STALE
    next_receipt = _submit(manager, second_generation)
    assert (
        await manager.await_result(next_receipt)
    ).status is SharedHostResultStatus.COMPLETED
    assert factory.max_live_hosts == 1
    await manager.close()


@pytest.mark.asyncio
async def test_deactivate_then_reactivate_without_terminal_fence() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    first = await _install(manager)
    old_lease = manager.lease(
        first,
        lane=SpeakerScoringLane.SHADOW,
        mode=SpeakerScoringMode.STANDARD,
        timeout_seconds=1.0,
    )

    assert await manager.deactivate(absolute_deadline=manager.now() + 1.0) is True
    assert manager.current_generation is None
    assert manager.snapshot()["physical_host_count"] == 0
    assert manager.snapshot()["terminal_count"] == 0
    assert old_lease.alive is False
    assert await manager.deactivate(absolute_deadline=manager.now() + 1.0) is True

    second = await _install(manager)
    assert second.host_generation > first.host_generation
    assert len(factory.hosts) == 2
    assert factory.max_live_hosts == 1
    await manager.close()


@pytest.mark.asyncio
async def test_deactivate_settles_active_and_queued_requests() -> None:
    factory = _FakeFactory()
    manager = _manager(factory)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    active = _submit(manager, generation)
    await host.started.wait()
    queued = _submit(manager, generation)

    deactivation = asyncio.create_task(
        manager.deactivate(absolute_deadline=manager.now() + 1.0)
    )
    assert (await manager.await_result(active)).status is SharedHostResultStatus.STALE
    assert (await manager.await_result(queued)).status is SharedHostResultStatus.STALE
    assert await deactivation is True
    assert len(host.calls) == 1
    assert manager.snapshot()["buffered_pcm_bytes"] == 0
    assert manager.snapshot()["terminal_count"] == 0
    await manager.close()


@pytest.mark.asyncio
async def test_deactivate_cancellation_still_finishes_bounded_retirement() -> None:
    factory = _FakeFactory()
    manager = _manager(factory, close_timeout_seconds=0.01)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.release = asyncio.Event()
    receipt = _submit(manager, generation)
    await host.started.wait()
    deactivation = asyncio.create_task(
        manager.deactivate(absolute_deadline=manager.now() + 1.0)
    )
    await asyncio.sleep(0)
    deactivation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await deactivation
    assert (await manager.await_result(receipt)).status is SharedHostResultStatus.STALE
    assert manager.current_generation is None
    assert manager.snapshot()["physical_host_count"] == 0
    assert manager.snapshot()["terminal_count"] == 0
    await manager.close()


@pytest.mark.asyncio
async def test_deactivate_failure_terminal_fences_manager() -> None:
    factory = _FakeFactory()
    manager = _manager(factory, close_timeout_seconds=0.01)
    generation = await _install(manager)
    host = factory.hosts[0]
    host.close_succeeds = False
    host.terminate_succeeds = False

    assert await manager.deactivate(absolute_deadline=manager.now() + 0.02) is False
    assert manager.snapshot()["terminal_count"] == 1
    assert manager.snapshot()["physical_host_count"] == 1
    with pytest.raises(SharedHostClosedError):
        _submit(manager, generation)
    with pytest.raises(SharedHostClosedError):
        await _install(manager, _identity("2"))
    await manager.close()


@pytest.mark.asyncio
async def test_receipts_are_manager_and_generation_bound() -> None:
    first_factory = _FakeFactory()
    second_factory = _FakeFactory()
    first_manager = _manager(first_factory)
    second_manager = _manager(second_factory)
    first_generation = await _install(first_manager)
    await _install(second_manager)
    receipt = _submit(first_manager, first_generation)

    with pytest.raises(SharedHostIdentityError, match="receipt_not_owned"):
        await second_manager.await_result(receipt)
    await first_manager.await_result(receipt)
    await first_manager.close()
    await second_manager.close()


def test_identity_and_configuration_validation() -> None:
    factory = _FakeFactory()
    with pytest.raises(ValueError):
        SpeakerHostIdentity("", "model", "config")
    with pytest.raises(ValueError):
        _manager(factory, max_outstanding_jobs=0)
    with pytest.raises(ValueError):
        _manager(factory, max_buffered_pcm_bytes=0)
    with pytest.raises(ValueError):
        _manager(factory, close_timeout_seconds=float("nan"))
