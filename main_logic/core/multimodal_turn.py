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


# 中间帧候选集上限。抽样只需要"大致铺满整段"，候选越多越准但也越占内存
# （每个候选是一整张 base64 原图）。
_MAX_MIDDLE_CANDIDATES = 5

# 「语音确认到 record 建立」窗口内允许暂存的帧校验任务数上限。这段窗口只有一次
# lifecycle 通知投递那么长，正常最多积压一两条；设上限只是防 record 一直没建出来
# 时无限增长。
_MAX_PRERECORD_VISUAL_VALIDATIONS = 8

# 同时在飞的回合记录数上限。新的 prepare 不能直接把旧记录清掉：上一条已被接受的
# final 可能还在 TranscriptDispatcher 里跑（例如正卡在有界的视觉校验 join 上），
# 记录一没它的身份自检就失败、整句话既不落库也不提交 —— 重叠发声会抹掉用户完整的
# 上一轮。每条记录都在自己 dispatch 的 finally 里按 turn_id 移除，所以这里只是内存
# 兜底，取一个真实场景摸不到的数（同时在飞的 ASR final 不会有这么多）。
_MAX_LIVE_TURN_RECORDS = 8

# onset 可信窗口。判据是「一个回合从确认到建记录最长能等多久」，不是帧的新鲜度：
# 重叠发声要排在上一轮的 provider final 后面，而 registry 里最长的
# provider_final_timeout_ms 是 40 秒。留一倍余量。
#
# 放在这里而不是 asr_runtime.py：那是 mixin 模块，顶层只允许 docstring / import /
# class（scripts/check_core_contracts.py 的 CORE_MIXIN_SHAPE），常量一律落在本模块
# 再导入过去，与上面两个上限同一处置。
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
    # record 真正建立的时刻。started_at 可能被回拨到语音起点（overlap 的后继发声
    # 甚至早于上一轮封口），所以"这个封口属于本轮吗"不能拿 started_at 判。
    registered_at: float = 0.0
    # 这条回合的 final 是否已经开始派发。派发中的记录绝不能被后继的 prepare 挤掉：
    # 它一没，那条 final 回来做身份自检时就认为世界变了，用户整句话既不落库也不提交。
    dispatch_started: bool = False
    first_frame: _IndependentVisualFrame | None = None
    last_frame: _IndependentVisualFrame | None = None
    # 语义端点时刻（monotonic 秒）。None = 这段发声还没结束。拍摄时间晚于它的帧
    # 不属于本回合——用"当下的 lifecycle 状态"代替这个截止值是不对的：说话期间
    # 拍到、端点之后才校验完的帧会被误杀。
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
            # 乱序到达：先拍的那帧后落地时仍然是这段发声的开头。
            self.first_frame = frame
        if (
            self.last_frame is None
            or frame.captured_at >= self.last_frame.captured_at
        ):
            self.last_frame = frame
        # 中间那张不能靠"边收边猜"选：发声多长事先不知道，只保一个候选的话中点
        # 前移时想提拔的那张已经被丢了，middle 会永远卡在开头附近。
        #
        # 每一帧都按拍摄时间插进候选集，超过上限时丢掉"最冗余"的那个内点——它左
        # 右邻居之间跨度最小，删掉它对整段覆盖的损失最小；两端永远不动。整个决定
        # （收谁、丢谁）因此全部跑在拍摄顺序上：并发校验下落地顺序和拍摄顺序不是
        # 一回事，任何一步按落地顺序做，都会把时间上真正居中的那几张先丢掉，而事
        # 后再排序捞不回来。
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
            # 候选集已经按拍摄时间维护（见 observe），正中间那个就是时间上的中间。
            middle = self.middle_candidates[len(self.middle_candidates) // 2]
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
