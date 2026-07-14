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

from dataclasses import dataclass
from typing import Literal


AsrProviderCategory = Literal["dummy", "ws_streaming", "segmented_request"]
AsrEndpointingMode = Literal["manual", "provider"]
AsrImplementationStatus = Literal[
    "implemented",
    "planned",
    "blocked_credentials",
    "blocked_backend",
]


@dataclass(frozen=True, slots=True)
class AsrCoreRoute:
    """Bind one Core to its ASR provider, credential slot, and region."""

    provider_key: str
    credential_field: str
    region: Literal["cn", "intl"] | None = None
    default_endpointing_mode: AsrEndpointingMode = "manual"


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


# Business code must route through this table rather than scattering
# ``if core_type == ...`` branches. qwen and qwen_intl intentionally share
# workers/qwen.py while keeping region and credential selection explicit.
CORE_ASR_ROUTES: dict[str, AsrCoreRoute] = {
    "qwen": AsrCoreRoute(
        provider_key="qwen",
        credential_field="ASSIST_API_KEY_QWEN",
        region="cn",
    ),
    "qwen_intl": AsrCoreRoute(
        provider_key="qwen",
        credential_field="ASSIST_API_KEY_QWEN_INTL",
        region="intl",
    ),
    "openai": AsrCoreRoute(
        provider_key="openai",
        credential_field="ASSIST_API_KEY_OPENAI",
    ),
    "step": AsrCoreRoute(
        provider_key="step",
        credential_field="ASSIST_API_KEY_STEP",
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
    # The free backend is blocked before credential resolution. An empty field
    # makes it impossible to accidentally borrow AUDIO_API_KEY in the future.
    "free": AsrCoreRoute(provider_key="free", credential_field=""),
}


ASR_PROVIDER_REGISTRY: dict[str, AsrProviderMeta] = {
    "dummy": AsrProviderMeta(
        provider_key="dummy",
        category="dummy",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
    ),
    "qwen": AsrProviderMeta(
        provider_key="qwen",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual", "provider"}),
        implementation_status="blocked_credentials",
    ),
    "openai": AsrProviderMeta(
        provider_key="openai",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=24_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="blocked_credentials",
    ),
    "step": AsrProviderMeta(
        provider_key="step",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual", "provider"}),
        implementation_status="blocked_credentials",
    ),
    "grok": AsrProviderMeta(
        provider_key="grok",
        category="ws_streaming",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"provider"}),
        implementation_status="blocked_credentials",
    ),
    "glm": AsrProviderMeta(
        provider_key="glm",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
    ),
    "gemini": AsrProviderMeta(
        provider_key="gemini",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="implemented",
        requires_smart_turn=True,
    ),
    "free": AsrProviderMeta(
        provider_key="free",
        category="segmented_request",
        worker_input_sample_rate_hz=16_000,
        wire_sample_rate_hz=16_000,
        supported_endpointing_modes=frozenset({"manual"}),
        implementation_status="blocked_backend",
    ),
}
