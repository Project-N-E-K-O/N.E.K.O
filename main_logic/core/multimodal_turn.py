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

import asyncio
import bisect
from dataclasses import dataclass, field


# Maximum size of the intermediate-frame candidate set. Sampling only needs to cover the full segment approximately;
# more candidates improve accuracy but also consume more memory
_MAX_MIDDLE_CANDIDATES = 5

# (each candidate is a full base64 original image).
# Lifecycle notifications normally queue at most one or two items; this cap only prevents unbounded growth
# if record creation never completes.
_MAX_PRERECORD_VISUAL_VALIDATIONS = 8

# Maximum number of simultaneously in-flight turn records. A new prepare must not simply clear old records: an accepted
# final from the previous turn may still be running in TranscriptDispatcher (for example, blocked in a bounded visual-validation join);
# without its record, identity validation fails and the whole sentence is neither stored nor committed — overlapping speech would erase the user's complete
# previous turn. Each record is removed by its own dispatch finally block using turn_id, so this is only an in-memory
# safety cap, set far above realistic usage (ASR finals in flight cannot reach this count).
_MAX_LIVE_TURN_RECORDS = 8

# Trusted onset window. The criterion is "how long after confirmation may a turn wait for its record to be created," not frame freshness:
# overlapping speech must wait behind the previous turn's provider final, and the largest registered
# provider_final_timeout_ms is 40 seconds. Allow twice that.
#
# Defined here instead of asr_runtime.py: that is a mixin module whose top level may contain only docstring / imports /
# class (CORE_MIXIN_SHAPE in scripts/check_core_contracts.py); keep constants in this module
# and import them there, alongside the two caps above.
_ONSET_TRUST_WINDOW_S = 80.0


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
    """One independent-ASR utterance and the frames sampled across it.

    Screen/camera frames arrive at roughly 1 fps for as long as the user keeps
    talking, so an utterance is a span, not an instant. Keeping every frame
    would hand the answering model an unbounded image list; keeping only the
    newest loses what the user was pointing at when they started. This samples
    the span down to at most three: first, middle, last.

    The sampler retains a bounded candidate set (never a growing buffer): once
    it is full, each new frame evicts the most redundant interior candidate, so
    the survivors stay spread over however long the user talks. Every decision
    is made in capture order, because concurrent validation means arrival order
    is not capture order. ``sampled_frames`` then picks the centre candidate.
    """

    turn_id: str
    session_epoch: int
    route_generation: int
    start_image_generation: int
    started_at: float
    # Time when the record was actually created. started_at may be moved back to the speech onset (a later overlapping utterance
    # may even begin before the previous turn is sealed), so do not use started_at to determine "whether this seal belongs to this turn."
    registered_at: float = 0.0
    # Whether final dispatch for this turn has started. A record with dispatch in progress must never be displaced by a later prepare:
    # without it, identity validation when the final returns sees a changed world, and the user's whole sentence is neither stored nor committed.
    dispatch_started: bool = False
    first_frame: _IndependentVisualFrame | None = None
    last_frame: _IndependentVisualFrame | None = None
    # Semantic endpoint time (monotonic seconds). None means this utterance has not ended. A frame captured after this point
    # does not belong to this turn — using the "current lifecycle state" as the cutoff is wrong: a frame captured while speaking
    # but validated only after the endpoint would be incorrectly rejected.
    endpoint_at: float | None = None
    middle_candidates: list[_IndependentVisualFrame] = field(default_factory=list)
    pending_visual_validations: dict[asyncio.Task, float] = field(
        default_factory=dict,
        repr=False,
    )
    invalidated: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def observe(self, frame: _IndependentVisualFrame) -> None:
        """Fold one newly staged frame into the first/middle/last sample."""

        if (
            self.first_frame is None
            or frame.captured_at < self.first_frame.captured_at
        ):
            # Out-of-order arrival: a frame captured earlier is still the onset frame when it lands later.
            self.first_frame = frame
        if (
            self.last_frame is None
            or frame.captured_at >= self.last_frame.captured_at
        ):
            self.last_frame = frame
        # The middle frame cannot be selected by "guessing as frames arrive": utterance duration is unknown in advance; if only one candidate is retained,
        # the frame that should be promoted as the midpoint may already be discarded when the midpoint moves, leaving middle stuck near the beginning.
        #
        # Insert each frame into the candidate set by capture time; when over the cap, drop the most redundant interior point — the one with the smallest span between its
        # neighbors, which causes the least loss of coverage; never remove endpoints. The decision about
        # which frame to keep or drop therefore uses capture order throughout: under concurrent validation, landing order differs from capture order,
        # and any step based on landing order will discard the frames truly in the temporal middle first; sorting afterward cannot recover them.
        # The candidate set is already maintained in capture-time order (see observe), so its center entry is the temporal midpoint.
        candidates = self.middle_candidates
        bisect.insort(
            candidates,
            frame,
            key=lambda item: (item.captured_at, item.generation),
        )
        if len(candidates) > _MAX_MIDDLE_CANDIDATES:
            victim = min(
                range(1, len(candidates) - 1),
                key=lambda index: (
                    candidates[index + 1].captured_at
                    - candidates[index - 1].captured_at,
                    index,
                ),
            )
            del candidates[victim]

    def sampled_frames(self) -> tuple[_IndependentVisualFrame, ...]:
        """Return the retained frames in capture order, without duplicates."""

        middle = None
        if self.middle_candidates:
            # Sort by capture time, not staging order: validation tasks run concurrently, and generation reflects
            middle = self.middle_candidates[len(self.middle_candidates) // 2]
        ordered: list[_IndependentVisualFrame] = []
        seen: set[int] = set()
        for frame in (self.first_frame, middle, self.last_frame):
            if frame is None or frame.generation in seen:
                continue
            seen.add(frame.generation)
            ordered.append(frame)
        # Sort by capture time, not staging order: validation tasks run concurrently, and generation reflects
        # which one landed first; when frames arrive out of order, that is not the true temporal sequence.
        ordered.sort(key=lambda item: (item.captured_at, item.generation))
        return tuple(ordered)

    def accepts(self, frame: _IndependentVisualFrame) -> bool:
        """Report whether ``frame`` was captured inside this utterance."""

        return (
            frame.session_epoch == self.session_epoch
            and frame.route_generation == self.route_generation
            and frame.generation > self.start_image_generation
            and frame.captured_at >= self.started_at
            and (self.endpoint_at is None or frame.captured_at <= self.endpoint_at)
        )

    def adopt_single_frame(self, frame: _IndependentVisualFrame) -> None:
        """Seed the sample with one late-discovered frame."""

        self.first_frame = frame
        self.last_frame = frame
        self.middle_candidates = [frame]


@dataclass(frozen=True, slots=True)
class MultimodalTurn:
    """One immutable independent-ASR user turn with its frozen raw frames."""

    turn_id: str
    session_epoch: int
    route_generation: int
    start_image_generation: int
    image_generation: int
    captured_at: float
    images: tuple[str, ...]
    transcript: str
    source: str
    request_id: str | None
