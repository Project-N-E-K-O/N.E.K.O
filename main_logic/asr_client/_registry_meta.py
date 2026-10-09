# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Single source of truth for Core-to-ASR routing and provider metadata."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


AsrProviderCategory = Literal["dummy", "ws_streaming", "segmented_request"]
AsrEndpointingMode = Literal["manual", "provider"]
AsrImplementationStatus = Literal[
    "implemented",
    "planned",
    "blocked_credentials",
    "blocked_backend",
]
AsrReplayPolicy = Literal["none", "preconnect_only", "provider_managed"]


class AsrProviderAvailability(str, Enum):
    """Provider capability exposed without parsing exception messages."""

    IMPLEMENTED = "implemented"
    BLOCKED_BACKEND = "blocked_backend"
    MISSING_CREDENTIALS = "missing_credentials"
    MISSING_DEPENDENCY = "missing_dependency"


@dataclass(frozen=True, slots=True)
class AsrCoreCapabilities:
    """Feature capabilities owned by one Core-to-ASR route."""

    supports_independent_asr: bool = True


@dataclass(frozen=True, slots=True)
class AsrCoreRoute:
    """Bind one Core to its ASR provider, credential slot, and region."""

    provider_key: str
    credential_field: str
    region: Literal["cn", "intl"] | None = None
    default_endpointing_mode: AsrEndpointingMode = "manual"
    capabilities: AsrCoreCapabilities = field(
        default_factory=AsrCoreCapabilities,
    )


@dataclass(frozen=True, slots=True)
class AsrFailureRule:
    """Trusted fault semantics registered alongside their provider."""

    recover_on_provider_failure: bool = False
    retry_connect: bool = False
    use_delivery_notice: bool = False


@dataclass(frozen=True, slots=True)
class AsrProviderMeta:
    """Architectural metadata for one ASR provider implementation."""

    provider_key: str
    category: AsrProviderCategory
    worker_input_sample_rate_hz: int
    wire_sample_rate_hz: int
    supported_endpointing_modes: frozenset[AsrEndpointingMode]
    implementation_status: AsrImplementationStatus
    requires_smart_turn: bool = False
    max_segment_ms: int | None = None
    warm_transport_ms: int = 25_000
    replay_policy: AsrReplayPolicy = "preconnect_only"
    provider_final_timeout_ms: int = 10_000
    # Upper bound on one-off provider preparation (a local model load or first
    # download) that the provider-final watchdog waits out before it starts
    # counting ``provider_final_timeout_ms``. Zero: the provider never warms up.
    provider_warmup_timeout_ms: int = 0
    connect_max_attempts: int = 1
    connect_retry_base_seconds: float = 0.25
    connect_retry_cap_seconds: float = 1.0
    # Local providers run without an API key; cloud providers must never be
    # constructed without their credential slot.
    requires_credential: bool = True
    # Only user-selectable providers may be chosen through the persisted
    # ``independentAsrProviderPreference`` conversation setting. Everything
    # else is reached through ``CORE_ASR_ROUTES``.
    user_selectable: bool = False
    # Import name of an optional package the worker needs. Selection probes it
    # with ``importlib.util.find_spec`` (no import) and reports
    # ``MISSING_DEPENDENCY`` instead of starting a worker that cannot run.
    optional_dependency: str | None = None
    # Qwen provider VAD remains authoritative; local activity only arms its
    # bounded session.finish recovery path.
    observes_local_activity: bool = False
    supports_result_preserving_finish: bool = False
    failure_rules: tuple[tuple[str, AsrFailureRule], ...] = ()

    @property
    def availability(self) -> AsrProviderAvailability:
        if self.implementation_status == "implemented":
            return AsrProviderAvailability.IMPLEMENTED
        if self.implementation_status == "blocked_credentials":
            return AsrProviderAvailability.MISSING_CREDENTIALS
        return AsrProviderAvailability.BLOCKED_BACKEND

    def __post_init__(self) -> None:
        if self.category == "segmented_request" and self.max_segment_ms is None:
            raise ValueError("segmented providers require max_segment_ms")
        if self.max_segment_ms is not None and self.max_segment_ms <= 0:
            raise ValueError("max_segment_ms must be positive")
        if self.warm_transport_ms < 0:
            raise ValueError("warm_transport_ms must not be negative")
        if self.provider_final_timeout_ms <= 0:
            raise ValueError("provider_final_timeout_ms must be positive")
        if self.provider_warmup_timeout_ms < 0:
            raise ValueError("provider_warmup_timeout_ms must not be negative")
        if self.connect_max_attempts <= 0:
            raise ValueError("connect_max_attempts must be positive")
        if self.connect_retry_base_seconds <= 0:
            raise ValueError("connect_retry_base_seconds must be positive")
        if self.connect_retry_cap_seconds < self.connect_retry_base_seconds:
            raise ValueError(
                "connect_retry_cap_seconds must cover the retry base"
            )
        if self.user_selectable and self.requires_credential:
            raise ValueError(
                "user-selectable providers must not require a Core credential"
            )


# Business code must route through this table rather than scattering
# ``if core_type == ...`` branches. qwen and qwen_intl intentionally share
# workers/qwen.py while keeping region and credential selection explicit.
CORE_ASR_ROUTES: dict[str, AsrCoreRoute] = {
    "qwen": AsrCoreRoute(
        provider_key="qwen",
        credential_field="ASSIST_API_KEY_QWEN",
        region="cn",
        default_endpointing_mode="provider",
    ),
    "qwen_intl": AsrCoreRoute(
        provider_key="qwen",
        credential_field="ASSIST_API_KEY_QWEN_INTL",
        region="intl",
        # The separate credential slot prevents cross-region key reuse; real
        # Qwen Intl permission/scope acceptance is still required before release.
        default_endpointing_mode="provider",
    ),
    "openai": AsrCoreRoute(
        provider_key="openai",
        credential_field="ASSIST_API_KEY_OPENAI",
        default_endpointing_mode="provider",
    ),
    "step": AsrCoreRoute(
        provider_key="step",
        credential_field="ASSIST_API_KEY_STEP",
        default_endpointing_mode="provider",
    ),
    "grok": AsrCoreRoute(
        provider_key="grok",
        credential_field="ASSIST_API_KEY_GROK",
        default_endpointing_mode="provider",
    ),
    "glm": AsrCoreRoute(
        provider_key="glm",
        credential_field="ASSIST_API_KEY_GLM",
    ),
    "gemini": AsrCoreRoute(
        provider_key="gemini",
        credential_field="ASSIST_API_KEY_GEMINI",
    ),
    # Free Core owns microphone transcription natively. Keep that product
    # capability separate from the provider's implementation status: callers
    # deciding between native and independent routing must never attempt this
    # provider, while direct independent-ASR construction continues to fail
    # closed if it is called incorrectly. An empty credential field also makes
    # it impossible to accidentally borrow AUDIO_API_KEY in the future.
    "free": AsrCoreRoute(
        provider_key="free",
        credential_field="",
        capabilities=AsrCoreCapabilities(supports_independent_asr=False),
    ),
}


ASR_PROVIDER_REGISTRY: dict[str, AsrProviderMeta] = {
    "dummy": AsrProviderMeta(
        provider_key="dummy",
        category="dummy",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
        max_segment_ms=27_000,
        warm_transport_ms=0,
        replay_policy="none",
    ),
    "qwen": AsrProviderMeta(
        provider_key="qwen",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual", "provider"}),
        implementation_status="implemented",
        observes_local_activity=True,
        supports_result_preserving_finish=True,
        failure_rules=(
            ("ASR_QWEN_READ_DISCONNECTED", AsrFailureRule(recover_on_provider_failure=True)),
            ("ASR_QWEN_CONNECTION_FAILED", AsrFailureRule(retry_connect=True)),
            ("ASR_QWEN_CONNECTION_CLOSED", AsrFailureRule(use_delivery_notice=True)),
            ("ASR_QWEN_WORKER_FAILED", AsrFailureRule(use_delivery_notice=True)),
        ),
    ),
    "openai": AsrProviderMeta(
        provider_key="openai",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=24_000,
        supported_endpointing_modes=frozenset({"provider"}),
        implementation_status="implemented",
        failure_rules=(
            ("ASR_OPENAI_WORKER_FAILED", AsrFailureRule(use_delivery_notice=True)),
        ),
    ),
    "step": AsrProviderMeta(
        provider_key="step",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"provider"}),
        implementation_status="implemented",
        failure_rules=(
            ("ASR_STEP_CONNECTION_CLOSED", AsrFailureRule(use_delivery_notice=True)),
            ("ASR_STEP_WORKER_FAILED", AsrFailureRule(use_delivery_notice=True)),
        ),
    ),
    "grok": AsrProviderMeta(
        provider_key="grok",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"provider"}),
        implementation_status="implemented",
    ),
    "glm": AsrProviderMeta(
        provider_key="glm",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
        max_segment_ms=27_000,
        warm_transport_ms=0,
        replay_policy="none",
        # The final HTTP request starts at the logical commit (turn seal), so
        # the provider-final watchdog must outlast the worker's 35 s request
        # timeout; otherwise ordinary slow responses fail deterministically.
        provider_final_timeout_ms=40_000,
    ),
    "gemini": AsrProviderMeta(
        provider_key="gemini",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
        max_segment_ms=27_000,
        warm_transport_ms=0,
        replay_policy="none",
        # Same contract as glm: cover the worker's 35 s request timeout.
        provider_final_timeout_ms=40_000,
    ),
    "soniox": AsrProviderMeta(
        provider_key="soniox",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual", "provider"}),
        implementation_status="implemented",
        replay_policy="provider_managed",
        connect_max_attempts=3,
        failure_rules=(
            ("ASR_SONIOX_RETRYABLE", AsrFailureRule(retry_connect=True)),
            ("ASR_SONIOX_CONNECTION_LIMIT", AsrFailureRule(retry_connect=True)),
            ("ASR_RATE_LIMITED", AsrFailureRule(retry_connect=True)),
            ("ASR_SONIOX_PROTECTED_REPLAY_DISABLED", AsrFailureRule(use_delivery_notice=True)),
            ("ASR_SONIOX_REPLAY_INCOMPLETE", AsrFailureRule(use_delivery_notice=True)),
        ),
    ),
    # Local faster-whisper. It is never a Core route: users opt in through the
    # voice-recognition settings, and Core capability (``free`` disables
    # independent ASR entirely) is still checked before this preference.
    "faster_whisper": AsrProviderMeta(
        provider_key="faster_whisper",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
        max_segment_ms=27_000,
        warm_transport_ms=0,
        replay_policy="none",
        # Decoding only: model preparation is excluded (see below). CPU decoding
        # of a long utterance with a larger model can take tens of seconds.
        provider_final_timeout_ms=120_000,
        # The worker reports ready before its model is loaded, and the first
        # turn may be sealed while the model is still loading or downloading
        # (medium is ~1.5 GB). The final watchdog waits this long for that
        # preparation, then applies provider_final_timeout_ms from the moment
        # the model became available.
        provider_warmup_timeout_ms=900_000,
        requires_credential=False,
        user_selectable=True,
        optional_dependency="faster_whisper",
    ),
    "free": AsrProviderMeta(
        provider_key="free",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="blocked_backend",
        max_segment_ms=27_000,
        warm_transport_ms=0,
        replay_policy="none",
    ),
}


_EMPTY_FAILURE_RULE = AsrFailureRule()


def resolve_provider_failure_rule(code: str) -> AsrFailureRule:
    """Resolve an exact machine code without importing any provider worker.

    Fault provenance is checked by the caller. Lookup remains independent of
    the active route so legacy delivery notices keep their existing meaning.
    Unknown faults grant no recovery or notification override.
    """
    for meta in ASR_PROVIDER_REGISTRY.values():
        for registered_code, rule in meta.failure_rules:
            if code == registered_code:
                return rule
    return _EMPTY_FAILURE_RULE
