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
from dataclasses import dataclass, field


# 中间帧候选集上限。抽样只需要"大致铺满整段"，候选越多越准但也越占内存
# （每个候选是一整张 base64 原图）。
_MAX_MIDDLE_CANDIDATES = 5


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
    it is full, every other candidate is dropped and the sampling stride
    doubles, so the survivors stay spread evenly over however long the user
    talks. ``sampled_frames`` then picks the centre candidate as the middle.
    """

    turn_id: str
    session_epoch: int
    route_generation: int
    start_image_generation: int
    started_at: float
    first_frame: _IndependentVisualFrame | None = None
    last_frame: _IndependentVisualFrame | None = None
    # 语义端点时刻（monotonic 秒）。None = 这段发声还没结束。拍摄时间晚于它的帧
    # 不属于本回合——用"当下的 lifecycle 状态"代替这个截止值是不对的：说话期间
    # 拍到、端点之后才校验完的帧会被误杀。
    endpoint_at: float | None = None
    middle_candidates: list[_IndependentVisualFrame] = field(default_factory=list)
    candidate_stride: int = 1
    observed_frames: int = 0
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
            # 乱序到达：先拍的那帧后落地时仍然是这段发声的开头。
            self.first_frame = frame
        if (
            self.last_frame is None
            or frame.captured_at >= self.last_frame.captured_at
        ):
            self.last_frame = frame
        # 中间那张不能靠"边收边猜"选：发声多长事先不知道，只保一个候选的话中点
        # 前移时想提拔的那张已经被丢了，middle 会永远卡在开头附近。改成等距抽样
        # ——候选满了就隔一个丢一个、步长翻倍，候选集始终 <= _MAX_MIDDLE_CANDIDATES
        # 且大致均匀铺满整段，最后取正中间那个。
        if self.observed_frames % self.candidate_stride == 0:
            self.middle_candidates.append(frame)
            if len(self.middle_candidates) > _MAX_MIDDLE_CANDIDATES:
                del self.middle_candidates[1::2]
                self.candidate_stride *= 2
        self.observed_frames += 1

    def sampled_frames(self) -> tuple[_IndependentVisualFrame, ...]:
        """Return the retained frames in capture order, without duplicates."""

        middle = None
        if self.middle_candidates:
            # 候选是按落地顺序 append 的，并发校验下这和拍摄顺序不是一回事；
            # 取"时间上的中间那张"必须先按 captured_at 排。
            by_capture = sorted(
                self.middle_candidates,
                key=lambda item: (item.captured_at, item.generation),
            )
            middle = by_capture[len(by_capture) // 2]
        ordered: list[_IndependentVisualFrame] = []
        seen: set[int] = set()
        for frame in (self.first_frame, middle, self.last_frame):
            if frame is None or frame.generation in seen:
                continue
            seen.add(frame.generation)
            ordered.append(frame)
        # 按拍摄时间排序，不按 staging 顺序：校验任务并发跑，generation 反映的是
        # 谁先落地，乱序到达时它和真实时间顺序不是一回事。
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
        self.candidate_stride = 1
        self.observed_frames = 1


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
