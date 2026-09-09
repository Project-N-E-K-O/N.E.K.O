"""Application owner for one shared CAMPPlus scoring process."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CampPlusBackendFactory,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    MAX_SPEAKER_SHADOW_BUFFERED_CANDIDATES,
    MAX_SPEAKER_SHADOW_PROCESS_TERMINATE_SECONDS,
    MAX_SPEAKER_SHADOW_RETAINED_PCM_BYTES,
)
from main_logic.asr_client.speaker_shadow.runtime import (
    BackendProcessPhysicalHostAdapter,
)
from main_logic.asr_client.speaker_shadow.shared_host import (
    HostGenerationReceipt,
    PhysicalScoreResponse,
    PhysicalSpeakerScoringHost,
    SharedSpeakerScoringHostManager,
    SharedSpeakerScoringLease,
    SpeakerHostIdentity,
    SpeakerScoringLane,
    SpeakerScoringMode,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile

from .pvad_backend import (
    PvadBackendFactory,
    is_compatible_activity_reference,
)


CAMPPLUS_SHARED_MODEL_GENERATION = f"{CAMPPLUS_MODEL_ID}@{CAMPPLUS_MODEL_REVISION}"
CAMPPLUS_PVAD_SHARED_MODEL_GENERATION = (
    f"{CAMPPLUS_SHARED_MODEL_GENERATION}+firered-pvad"
)
_EXPECTED_MODEL_IDENTITY = SpeakerModelIdentity(
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_EMBEDDING_DIM,
)


class _ParentBackendFactory(Protocol):
    def close(self) -> None: ...


BackendFactoryBuilder = Callable[..., _ParentBackendFactory]
AdapterStarter = Callable[..., Awaitable[PhysicalSpeakerScoringHost]]


@dataclass(frozen=True, slots=True)
class SharedCampPlusCompositionBinding:
    manager: SharedSpeakerScoringHostManager
    generation: HostGenerationReceipt
    pvad_observe_enabled: bool = False

    def is_current(self) -> bool:
        return self.manager.is_generation_current(self.generation)


def _wipe_array(value: np.ndarray | None) -> None:
    if value is None:
        return
    try:
        if not value.flags.writeable:
            value.setflags(write=True)
        value.fill(0.0)
    except Exception:
        pass


class _OwnedCampPlusPhysicalHost:
    """Pair one process adapter with its parent-side reference owner."""

    def __init__(
        self,
        adapter: PhysicalSpeakerScoringHost,
        parent_factory: _ParentBackendFactory,
    ) -> None:
        self._adapter = adapter
        self._parent_factory = parent_factory
        self._factory_closed = False

    @property
    def process_count(self) -> int:
        return self._adapter.process_count

    async def load(self, *, timeout_seconds: float) -> bool:
        return await self._adapter.load(timeout_seconds=timeout_seconds)

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
        return await self._adapter.score(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            mode=mode,
            host_generation=host_generation,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )

    async def close(self, *, timeout_seconds: float) -> bool:
        try:
            return bool(await self._adapter.close(timeout_seconds=timeout_seconds))
        finally:
            self._close_parent_factory_once()

    async def terminate(self, *, timeout_seconds: float) -> None:
        try:
            await self._adapter.terminate(timeout_seconds=timeout_seconds)
        finally:
            self._close_parent_factory_once()

    def _close_parent_factory_once(self) -> None:
        if self._factory_closed:
            return
        self._factory_closed = True
        self._parent_factory.close()


class SharedCampPlusScoringHost:
    """Own the sole shared CAMPPlus host for pre-wire and shadow lanes."""

    def __init__(
        self,
        *,
        asset_dir: Path | None = None,
        max_outstanding_jobs: int = MAX_SPEAKER_SHADOW_BUFFERED_CANDIDATES,
        max_buffered_pcm_bytes: int = MAX_SPEAKER_SHADOW_RETAINED_PCM_BYTES,
        close_timeout_seconds: float = (MAX_SPEAKER_SHADOW_PROCESS_TERMINATE_SECONDS),
        backend_factory_builder: BackendFactoryBuilder = CampPlusBackendFactory,
        adapter_starter: AdapterStarter = (
            BackendProcessPhysicalHostAdapter.create_started
        ),
    ) -> None:
        if not callable(backend_factory_builder):
            raise TypeError("backend_factory_builder must be callable")
        if not callable(adapter_starter):
            raise TypeError("adapter_starter must be callable")
        self._asset_dir = Path(asset_dir) if asset_dir is not None else None
        self._close_timeout_seconds = float(close_timeout_seconds)
        self._backend_factory_builder = backend_factory_builder
        self._adapter_starter = adapter_starter
        self._pending_identity: SpeakerHostIdentity | None = None
        self._pending_factory: _ParentBackendFactory | None = None
        self._activation_lock = asyncio.Lock()
        self._closed = False
        self._manager = SharedSpeakerScoringHostManager(
            self._create_physical_host,
            max_outstanding_jobs=max_outstanding_jobs,
            max_buffered_pcm_bytes=max_buffered_pcm_bytes,
            close_timeout_seconds=close_timeout_seconds,
        )

    @property
    def generation(self) -> HostGenerationReceipt | None:
        return self._manager.current_generation

    async def activate(
        self,
        profile: SpeakerProfile,
        config_generation: str,
        *,
        absolute_deadline: float,
    ) -> HostGenerationReceipt:
        if type(profile) is not SpeakerProfile:
            raise TypeError("profile must be SpeakerProfile")
        if type(config_generation) is not str or not config_generation.strip():
            raise ValueError("config_generation must be a non-empty string")
        if self._closed:
            raise RuntimeError("shared_campplus_host_closed")

        async with self._activation_lock:
            if self._closed:
                raise RuntimeError("shared_campplus_host_closed")
            identity, embedding, activity_embedding = self._clone_and_validate_profile(
                profile,
                config_generation,
            )
            try:
                current = self._manager.current_generation
                if (
                    current is not None
                    and current.identity == identity
                    and self._manager.is_generation_current(current)
                ):
                    return current
                parent_factory = self._build_parent_factory(
                    embedding,
                    activity_embedding,
                )
            finally:
                _wipe_array(embedding)
                _wipe_array(activity_embedding)

            self._pending_identity = identity
            self._pending_factory = parent_factory
            try:
                return await self._manager.install(
                    identity,
                    absolute_deadline=absolute_deadline,
                )
            finally:
                if self._pending_factory is parent_factory:
                    self._pending_factory = None
                    self._pending_identity = None
                    parent_factory.close()

    def lease(
        self,
        *,
        lane: SpeakerScoringLane,
        mode: SpeakerScoringMode,
        timeout_seconds: float,
    ) -> SharedSpeakerScoringLease:
        generation = self.generation
        if generation is None:
            raise RuntimeError("shared_campplus_host_not_active")
        return self._manager.lease(
            generation,
            lane=lane,
            mode=mode,
            timeout_seconds=timeout_seconds,
        )

    def composition_binding(self) -> SharedCampPlusCompositionBinding:
        generation = self.generation
        if generation is None or not self._manager.is_generation_current(generation):
            raise RuntimeError("shared_campplus_host_not_active")
        return SharedCampPlusCompositionBinding(
            self._manager,
            generation,
            pvad_observe_enabled=(
                generation.identity.model_generation
                == CAMPPLUS_PVAD_SHARED_MODEL_GENERATION
            ),
        )

    def snapshot(self) -> dict[str, int]:
        return self._manager.snapshot()

    async def deactivate(self, *, absolute_deadline: float) -> bool:
        async with self._activation_lock:
            if self._closed:
                raise RuntimeError("shared_campplus_host_closed")
            return await self._manager.deactivate(absolute_deadline=absolute_deadline)

    async def close(self) -> None:
        async with self._activation_lock:
            if self._closed:
                return
            self._closed = True
            pending_factory, self._pending_factory = self._pending_factory, None
            self._pending_identity = None
            if pending_factory is not None:
                pending_factory.close()
            await self._manager.close()

    def _clone_and_validate_profile(
        self,
        profile: SpeakerProfile,
        config_generation: str,
    ) -> tuple[SpeakerHostIdentity, np.ndarray, np.ndarray | None]:
        owned_profile = copy.copy(profile)
        reference = None
        embedding: np.ndarray | None = None
        activity_reference = None
        activity_embedding: np.ndarray | None = None
        try:
            if owned_profile.model_identity != _EXPECTED_MODEL_IDENTITY:
                raise ValueError("speaker profile is not compatible with CAMPPlus")
            profile_generation = owned_profile.generation
            reference = owned_profile.clone_reference()
            embedding = reference.copy_embedding()
            activity_reference = owned_profile.clone_activity_reference()
            activity_contract = owned_profile.activity_reference_contract
            if activity_reference is not None and is_compatible_activity_reference(
                activity_reference.model_identity,
                activity_contract,
            ):
                activity_embedding = activity_reference.copy_embedding()
            return (
                SpeakerHostIdentity(
                    profile_generation,
                    (
                        CAMPPLUS_PVAD_SHARED_MODEL_GENERATION
                        if activity_embedding is not None
                        else CAMPPLUS_SHARED_MODEL_GENERATION
                    ),
                    config_generation,
                ),
                embedding,
                activity_embedding,
            )
        except BaseException:
            _wipe_array(embedding)
            _wipe_array(activity_embedding)
            raise
        finally:
            if activity_reference is not None:
                activity_reference.close()
            if reference is not None:
                reference.close()
            owned_profile.close()

    def _build_parent_factory(
        self,
        embedding: np.ndarray,
        activity_embedding: np.ndarray | None,
    ) -> _ParentBackendFactory:
        campplus_factory = self._backend_factory_builder(
            embedding,
            asset_dir=self._asset_dir,
            allow_short_input=True,
        )
        if activity_embedding is None:
            return campplus_factory
        return PvadBackendFactory(campplus_factory, activity_embedding)

    async def _create_physical_host(
        self,
        identity: SpeakerHostIdentity,
        host_generation: int,
    ) -> PhysicalSpeakerScoringHost:
        del host_generation
        parent_factory = self._pending_factory
        if parent_factory is None or identity != self._pending_identity:
            raise RuntimeError("prepared CAMPPlus factory identity mismatch")
        self._pending_factory = None
        self._pending_identity = None
        try:
            adapter = await self._adapter_starter(
                factory=parent_factory,
                terminate_timeout_seconds=self._close_timeout_seconds,
            )
        except BaseException:
            parent_factory.close()
            raise
        return _OwnedCampPlusPhysicalHost(adapter, parent_factory)


__all__ = [
    "CAMPPLUS_PVAD_SHARED_MODEL_GENERATION",
    "CAMPPLUS_SHARED_MODEL_GENERATION",
    "SharedCampPlusCompositionBinding",
    "SharedCampPlusScoringHost",
]
