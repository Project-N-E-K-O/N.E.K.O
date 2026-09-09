from __future__ import annotations

import asyncio

import numpy as np
import pytest

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.asr_client.speaker_shadow.shared_host import (
    PhysicalScoreResponse,
    SharedHostScoreError,
    SpeakerScoringLane,
    SpeakerScoringMode,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import (
    SpeakerActivityReferenceContract,
    SpeakerProfile,
)
from main_logic.voice_identity.pvad.assets import (
    ECAPA_IDENTITY,
    ECAPA_PREPROCESSING_REVISION,
    ECAPA_REFERENCE_METHOD,
    ECAPA_RESOURCE_REVISION,
)
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.pvad_backend import PvadBackendFactory
from main_logic.voice_identity_service.shared_campplus_host import (
    CAMPPLUS_PVAD_SHARED_MODEL_GENERATION,
    CAMPPLUS_SHARED_MODEL_GENERATION,
    SharedCampPlusScoringHost,
)


class _FakeParentFactory:
    def __init__(
        self,
        embedding: np.ndarray,
        *,
        asset_dir,
        allow_short_input: bool,
        events: list[str],
    ) -> None:
        self.reference = np.array(embedding, dtype=np.float32, copy=True)
        self.asset_dir = asset_dir
        self.allow_short_input = allow_short_input
        self.events = events
        self.close_count = 0

    def close(self) -> None:
        if self.close_count:
            return
        self.close_count += 1
        self.reference.fill(0.0)
        self.events.append("factory_close")


class _FakeParentFactoryBuilder:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.factories: list[_FakeParentFactory] = []

    def __call__(self, embedding, *, asset_dir, allow_short_input):
        factory = _FakeParentFactory(
            embedding,
            asset_dir=asset_dir,
            allow_short_input=allow_short_input,
            events=self.events,
        )
        self.factories.append(factory)
        self.events.append("factory_create")
        return factory


class _FakeAdapter:
    def __init__(
        self,
        factory: _FakeParentFactory,
        events: list[str],
        *,
        load_succeeds: bool,
    ) -> None:
        self.factory = factory
        self.events = events
        self.load_succeeds = load_succeeds
        self._process_count = 1
        self.close_count = 0
        self.terminate_count = 0
        self.close_succeeds = True
        self.terminate_succeeds = True
        self.active = 0
        self.max_active = 0
        self.calls: list[SpeakerScoringMode] = []

    @property
    def process_count(self) -> int:
        return self._process_count

    async def load(self, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.events.append("adapter_load")
        return self.load_succeeds

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
        assert pcm16
        assert sample_rate_hz == 16_000
        assert timeout_seconds > 0
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(mode)
            await asyncio.sleep(0)
            return PhysicalScoreResponse(host_generation, request_id, 0.8)
        finally:
            self.active -= 1

    async def close(self, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.close_count += 1
        self.events.append("adapter_close")
        if self.close_succeeds:
            self._process_count = 0
        return self.close_succeeds

    async def terminate(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0
        self.terminate_count += 1
        if self.terminate_succeeds:
            self._process_count = 0
        self.events.append("adapter_terminate")


class _FakeAdapterStarter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.adapters: list[_FakeAdapter] = []
        self.fail_create = False
        self.next_load_succeeds = True
        self.max_live_hosts = 0

    async def __call__(self, *, factory, terminate_timeout_seconds):
        assert terminate_timeout_seconds > 0
        assert sum(item.process_count for item in self.adapters) == 0
        self.events.append("adapter_create")
        if self.fail_create:
            raise RuntimeError("fake create failure")
        adapter = _FakeAdapter(
            factory,
            self.events,
            load_succeeds=self.next_load_succeeds,
        )
        self.next_load_succeeds = True
        self.adapters.append(adapter)
        self.max_live_hosts = max(
            self.max_live_hosts,
            sum(item.process_count for item in self.adapters),
        )
        return adapter


def _profile(generation: str = "profile-1") -> SpeakerProfile:
    identity = SpeakerModelIdentity(
        CAMPPLUS_MODEL_ID,
        CAMPPLUS_MODEL_REVISION,
        CAMPPLUS_EMBEDDING_DIM,
    )
    embedding = np.zeros(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
    embedding[0] = 1.0
    reference = SpeakerReference(identity, embedding)
    try:
        return SpeakerProfile(generation, reference)
    finally:
        reference.close()
        embedding.fill(0.0)


def _profile_with_activity(generation: str = "profile-activity") -> SpeakerProfile:
    profile = _profile(generation)
    camp = profile.clone_reference()
    activity = SpeakerReference(
        ECAPA_IDENTITY,
        np.ones(ECAPA_IDENTITY.embedding_dimension, dtype=np.float32),
    )
    contract = SpeakerActivityReferenceContract(
        ECAPA_RESOURCE_REVISION,
        ECAPA_PREPROCESSING_REVISION,
        ECAPA_REFERENCE_METHOD,
        16_000,
        True,
    )
    try:
        return SpeakerProfile(
            generation,
            camp,
            activity_reference=activity,
            activity_reference_contract=contract,
        )
    finally:
        camp.close()
        activity.close()
        profile.close()


def _owner():
    events: list[str] = []
    builder = _FakeParentFactoryBuilder(events)
    starter = _FakeAdapterStarter(events)
    owner = SharedCampPlusScoringHost(
        max_outstanding_jobs=4,
        max_buffered_pcm_bytes=1024,
        close_timeout_seconds=0.05,
        backend_factory_builder=builder,
        adapter_starter=starter,
    )
    return owner, builder, starter, events


async def _activate(owner, profile, config: str = "config-1"):
    return await owner.activate(
        profile,
        config,
        absolute_deadline=asyncio.get_running_loop().time() + 1.0,
    )


def test_constructor_does_not_create_backend_or_process() -> None:
    owner, builder, starter, _events = _owner()
    assert owner.generation is None
    assert owner.snapshot()["physical_host_count"] == 0
    assert builder.factories == []
    assert starter.adapters == []


@pytest.mark.asyncio
async def test_dual_lanes_share_one_host_and_use_per_request_modes() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    generation = await _activate(owner, profile)
    prewire = owner.lease(
        lane=SpeakerScoringLane.PREWIRE,
        mode=SpeakerScoringMode.SHORT_PROBE,
        timeout_seconds=1.0,
    )
    shadow = owner.lease(
        lane=SpeakerScoringLane.SHADOW,
        mode=SpeakerScoringMode.STANDARD,
        timeout_seconds=1.0,
    )

    assert await asyncio.gather(
        prewire.score_async(b"\x01\x00" * 4, 16_000),
        shadow.score_async(b"\x02\x00" * 4, 16_000),
    ) == [0.8, 0.8]
    assert generation.identity.profile_generation == "profile-1"
    assert generation.identity.model_generation == CAMPPLUS_SHARED_MODEL_GENERATION
    assert generation.identity.config_generation == "config-1"
    assert starter.adapters[0].calls == [
        SpeakerScoringMode.SHORT_PROBE,
        SpeakerScoringMode.STANDARD,
    ]
    assert starter.adapters[0].max_active == 1
    assert len(starter.adapters) == 1
    assert builder.factories[0].allow_short_input is True
    assert profile.closed is False
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_activity_profile_wraps_the_same_shared_physical_host_with_pvad() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile_with_activity()
    generation = await _activate(owner, profile)
    binding = owner.composition_binding()

    assert generation.identity.model_generation == (
        CAMPPLUS_PVAD_SHARED_MODEL_GENERATION
    )
    assert binding.pvad_observe_enabled is True
    assert len(builder.factories) == 1
    assert len(starter.adapters) == 1
    assert isinstance(starter.adapters[0].factory, PvadBackendFactory)
    assert starter.max_live_hosts == 1
    await owner.close()
    assert builder.factories[0].close_count == 1
    profile.close()


@pytest.mark.asyncio
async def test_same_identity_reuses_generation_and_factory() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    first = await _activate(owner, profile)
    second = await _activate(owner, profile)

    assert second == first
    assert len(builder.factories) == 1
    assert len(starter.adapters) == 1
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_profile_and_config_reload_retire_before_create() -> None:
    owner, builder, starter, events = _owner()
    first_profile = _profile("profile-1")
    second_profile = _profile("profile-2")
    await _activate(owner, first_profile)
    await _activate(owner, first_profile, "config-2")
    await _activate(owner, second_profile, "config-2")

    assert len(starter.adapters) == 3
    assert starter.max_live_hosts == 1
    assert all(item.process_count == 0 for item in starter.adapters[:-1])
    create_positions = [
        index for index, event in enumerate(events) if event == "adapter_create"
    ]
    close_positions = [
        index for index, event in enumerate(events) if event == "adapter_close"
    ]
    assert close_positions[0] < create_positions[1]
    assert close_positions[1] < create_positions[2]
    assert [factory.close_count for factory in builder.factories[:-1]] == [1, 1]
    assert all(not np.any(factory.reference) for factory in builder.factories[:-1])
    await owner.close()
    first_profile.close()
    second_profile.close()


@pytest.mark.asyncio
async def test_adapter_create_failure_closes_and_wipes_parent_factory() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    starter.fail_create = True

    with pytest.raises(SharedHostScoreError):
        await _activate(owner, profile)
    assert len(builder.factories) == 1
    assert builder.factories[0].close_count == 1
    assert not np.any(builder.factories[0].reference)
    assert owner.generation is None
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_load_failure_closes_adapter_and_parent_factory() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    starter.next_load_succeeds = False

    with pytest.raises(SharedHostScoreError):
        await _activate(owner, profile)
    assert starter.adapters[0].close_count == 1
    assert builder.factories[0].close_count == 1
    assert not np.any(builder.factories[0].reference)
    assert owner.snapshot()["physical_host_count"] == 0
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_deactivate_reactivate_and_binding_staleness() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    first = await _activate(owner, profile)
    old_binding = owner.composition_binding()
    assert old_binding.manager.is_generation_current(old_binding.generation)
    assert old_binding.is_current() is True

    assert (
        await owner.deactivate(
            absolute_deadline=asyncio.get_running_loop().time() + 1.0
        )
        is True
    )
    assert (
        await owner.deactivate(
            absolute_deadline=asyncio.get_running_loop().time() + 1.0
        )
        is True
    )
    assert old_binding.is_current() is False
    assert owner.generation is None
    with pytest.raises(RuntimeError, match="not_active"):
        owner.composition_binding()

    second = await _activate(owner, profile)
    new_binding = owner.composition_binding()
    assert second.host_generation > first.host_generation
    assert new_binding.generation == second
    assert new_binding.is_current() is True
    assert old_binding.is_current() is False
    assert len(builder.factories) == 2
    assert starter.max_live_hosts == 1
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_deactivate_failure_fences_owner_generation() -> None:
    owner, _builder, starter, _events = _owner()
    profile = _profile()
    await _activate(owner, profile)
    binding = owner.composition_binding()
    starter.adapters[0].close_succeeds = False
    starter.adapters[0].terminate_succeeds = False

    assert (
        await owner.deactivate(
            absolute_deadline=asyncio.get_running_loop().time() + 0.02
        )
        is False
    )
    assert binding.is_current() is False
    assert owner.snapshot()["terminal_count"] == 1
    with pytest.raises(RuntimeError, match="not_active"):
        owner.composition_binding()
    await owner.close()
    profile.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_releases_factory_once() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    await _activate(owner, profile)

    await owner.close()
    await owner.close()
    assert starter.adapters[0].close_count == 1
    assert starter.adapters[0].terminate_count == 0
    assert builder.factories[0].close_count == 1
    assert owner.snapshot()["physical_host_count"] == 0
    with pytest.raises(RuntimeError, match="closed"):
        await _activate(owner, profile)
    profile.close()


@pytest.mark.asyncio
async def test_terminate_after_close_failure_still_closes_factory_once() -> None:
    owner, builder, starter, _events = _owner()
    profile = _profile()
    await _activate(owner, profile)
    starter.adapters[0].close_succeeds = False

    await owner.close()
    assert starter.adapters[0].close_count == 1
    assert starter.adapters[0].terminate_count == 1
    assert builder.factories[0].close_count == 1
    assert not np.any(builder.factories[0].reference)
    profile.close()


@pytest.mark.asyncio
async def test_rejects_non_campplus_profile_without_disturbing_active_host() -> None:
    owner, builder, starter, _events = _owner()
    valid = _profile()
    generation = await _activate(owner, valid)
    wrong_identity = SpeakerModelIdentity("other", "v1", CAMPPLUS_EMBEDDING_DIM)
    embedding = np.ones(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
    reference = SpeakerReference(wrong_identity, embedding)
    invalid = SpeakerProfile("profile-2", reference)
    reference.close()
    embedding.fill(0.0)

    with pytest.raises(ValueError, match="not compatible"):
        await _activate(owner, invalid)
    assert owner.generation == generation
    assert owner.snapshot()["physical_host_count"] == 1
    assert len(builder.factories) == 1
    assert len(starter.adapters) == 1
    await owner.close()
    valid.close()
    invalid.close()
