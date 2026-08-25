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
"""Provider-neutral raw-image ownership for independent-ASR user turns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _IndependentVisualFrame:
    image_b64: str
    session_epoch: int
    route_generation: int
    generation: int
    captured_at: float
    source: str
    request_id: str | None


@dataclass(slots=True)
class _CoreMultimodalTurnRecord:
    turn_id: str
    session_epoch: int
    route_generation: int
    start_image_generation: int
    started_at: float
    frame: _IndependentVisualFrame | None = None


@dataclass(frozen=True, slots=True)
class MultimodalTurn:
    """One immutable independent-ASR user turn with its frozen raw frame."""

    turn_id: str
    session_epoch: int
    route_generation: int
    start_image_generation: int
    image_generation: int
    captured_at: float
    image_b64: str
    transcript: str
    source: str
    request_id: str | None
