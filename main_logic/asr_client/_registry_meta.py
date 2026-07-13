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
AsrImplementationStatus = Literal[
    "implemented",
    "planned",
    "blocked_credentials",
    "blocked_backend",
]


@dataclass(frozen=True, slots=True)
class AsrProviderMeta:
    """Architectural metadata for one ASR provider implementation."""

    provider_key: str
    category: AsrProviderCategory
    canonical_sample_rate_hz: int
    implementation_status: AsrImplementationStatus


# Business code must route through this table rather than scattering
# ``if core_type == ...`` branches. qwen and qwen_intl intentionally share one
# worker implementation; their regional endpoint/credential differences belong
# to the future Alibaba worker.
CORE_ASR_ROUTES: dict[str, str] = {
    "qwen": "alibaba",
    "qwen_intl": "alibaba",
    "openai": "openai",
    "step": "step",
    "grok": "grok",
    "glm": "glm",
    "gemini": "gemini",
    "free": "free",
}


ASR_PROVIDER_REGISTRY: dict[str, AsrProviderMeta] = {
    "dummy": AsrProviderMeta(
        provider_key="dummy",
        category="dummy",
        canonical_sample_rate_hz=16_000,
        implementation_status="implemented",
    ),
    "alibaba": AsrProviderMeta(
        provider_key="alibaba",
        category="ws_streaming",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "openai": AsrProviderMeta(
        provider_key="openai",
        category="ws_streaming",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "step": AsrProviderMeta(
        provider_key="step",
        category="ws_streaming",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "grok": AsrProviderMeta(
        provider_key="grok",
        category="ws_streaming",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "glm": AsrProviderMeta(
        provider_key="glm",
        category="segmented_request",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "gemini": AsrProviderMeta(
        provider_key="gemini",
        category="segmented_request",
        canonical_sample_rate_hz=16_000,
        implementation_status="planned",
    ),
    "free": AsrProviderMeta(
        provider_key="free",
        category="segmented_request",
        canonical_sample_rate_hz=16_000,
        implementation_status="blocked_backend",
    ),
}
