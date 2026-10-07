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

"""Generic, plugin-agnostic proactive-delivery pacing/ordering front stage.

Problem (observed in voice mode especially): proactive cues — plugin
``push_message(ai_behavior="respond")``, greeting, agent task results — are
produced far faster than the assistant can speak them, and the legacy gate
released the next cue on the realtime API's ``response.done`` (generation
finished) while the FRONTEND was still playing buffered audio. Result: she
talks non-stop / interrupts herself, and a low-value "state digest" cue
competes equally with an urgent "you got hit" cue.

This manager sits IN FRONT of the existing, race-tested
``LLMSessionManager.enqueue_agent_callback`` + ``trigger_agent_callbacks``
delivery core (it does NOT replace it). It owns the WAITING cues and decides
WHICH cue and WHEN to hand one off, applying:

* **Priority ordering** — HIGHER number = more important (the repo-wide
  convention shared by existing producers: bilibili gift/SC=9, memo
  reminder=8, study answer_evaluated=5, and the HUD ``priority_min`` filter).
  ``priority`` arrives from ``push_message(priority=...)``; unspecified
  default (0) = least important, so a cue that set a priority always outranks
  one that didn't. minecraft is tagged on the same scale (alert highest).
* **Coalescing** — OPT-IN: queued cues sharing an explicit ``coalesce_key``
  collapse to the newest. An unset key never coalesces (unique per cue), so
  no existing plugin regresses by having distinct cues silently dropped.
  Minecraft opts in per category (alert / completion / in_progress /
  keep_going).
* **Batched + playback gate** — cues that pile up while she is speaking are
  released TOGETHER as one batch (the legacy "one LLM turn for several
  near-simultaneous cues" behaviour), and never while audio is playing.
  Release happens after the FRONTEND reports ``voice_play_end`` (or
  ``text_end``), plus a min-gap. Only one batch is in flight at a time.
* **Min-gap pacing** — never release within ``min_gap_s`` of the last
  playback end (anti-flood).
* **Preemption / staleness** — when the gate opens the current highest
  priority cue wins the slot; a cue that has waited longer than ``ttl_s``
  is dropped rather than spoken stale.

The manager runs entirely inside the asyncio event loop; all public methods
are synchronous and schedule the actual (awaitable) hand-off via
``create_task``. There is therefore no internal lock — the single-threaded
loop serialises everything between ``await`` points.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("main_logic.proactive_delivery")

DELIVERY_ACK_FUTURE_KEY = "_proactive_delivery_ack_future"
DELIVERY_RETRACTED_KEY = "_proactive_delivery_retracted"
CALLBACK_EXPIRES_AT_KEY = "_expires_at_monotonic"
VOICE_DELIVERY_COMMITTED_KEY = "_voice_delivery_committed"
SWAP_PRIME_DELIVERY_CLAIM_KEY = "_swap_prime_delivery_claimed"
# 最近一次挂图失败是**瞬时**的（网络抖动 / provider 临时拒绝），不是终局的
# （格式不支持之类）。终局失败再试也没用，瞬时的下一轮大概率能成 —— 所以只有
# 瞬时失败值得为它把这条通知多留一轮。
PASSIVE_MEDIA_TRANSIENT_KEY = "_passive_media_transient_failure"
# 已经为此多留过几轮。上限 1：留两轮以上就等于回到「扣住不放」，那是这条判据
# 一开始要避免的。
PASSIVE_MEDIA_RETRY_KEY = "_passive_media_retries"
PASSIVE_MEDIA_MAX_RETRIES = 1
# 这一轮的图片预算装不下它，被推到下一轮。与「瞬时失败」是两回事：那是尝试过
# 失败了，这是**根本没轮到它尝试**，所以既不该套重试上限，也不该退化成 text-only
# ——退化就等于把它的图永久丢掉，而这恰恰是预算延后想避免的。
PASSIVE_MEDIA_BUDGET_DEFERRED_KEY = "_passive_media_budget_deferred"

# Image budget for ONE model turn. A trigger drains every pending proactive
# callback into a single turn, so a per-push cap alone does not bound what the
# provider receives: cues pile up whenever the proactive claim is denied (the
# user is mid-conversation), then release together. The figures match the
# contract PLUGIN_DEVELOPMENT_GUIDE.md advertises to plugin authors.
CALLBACK_IMAGE_MAX_COUNT = 8
CALLBACK_IMAGE_MAX_TOTAL_BYTES = 8 * 1024 * 1024

# Staged-image quotas for the text path, counted PER SOURCE.
#
# The user's own screen/camera frames and plugin `read` images are attached to
# the same turn. A single shared cap makes them fight, and every policy
# available under one cap harms the user: evicting the oldest lets a background
# plugin discard the frame the user just staged, and refusing the newest lets a
# plugin block the user's own image once the queue is full. Both were tried
# during review and both were correctly rejected.
#
# Separate quotas remove the conflict instead of picking a winner. Neither
# source can spend the other's budget, so trimming is always the offending
# source's own oldest frame. The user gets the larger share: they stage
# deliberately, one frame at a time, and can see what they staged.
USER_PENDING_IMAGE_MAX_COUNT = 5
PLUGIN_PENDING_IMAGE_MAX_COUNT = 3

# Bytes, also per source. Count and size are independent axes: three images
# inside the plugin's count quota can still be three near-8-MiB images, which
# is ~24 MiB attached to one turn and past what the provider will take.
#
# The plugin ceiling is the per-turn figure the plugin guide already
# advertises, so the staged path cannot quietly exceed the documented
# contract. The user gets the larger share for the same reason they get more
# slots -- deliberate frames, staged one at a time.
#
# These bound ACCUMULATION, not a single image: a lone frame is kept even if
# it is over, because it already passed its own per-image limit upstream and
# silently dropping the only image is worse than letting the provider judge.
PLUGIN_PENDING_IMAGE_MAX_BYTES = CALLBACK_IMAGE_MAX_TOTAL_BYTES
USER_PENDING_IMAGE_MAX_BYTES = 2 * CALLBACK_IMAGE_MAX_TOTAL_BYTES

# What ONE outgoing request on the TEXT path may carry, across every source
# at once.
#
# The per-source quotas above deliberately do not talk to each other -- that is
# the whole point of splitting them, so neither source can spend the other's
# budget. But they are not the last word on what leaves the process: the text
# path attaches the proactive screenshot, the plugin frames AND the user frames
# to the SAME HumanMessage, so the worst case is their SUM (16 + 8 MiB plus a
# screenshot), which is several times the per-request ceiling the provider will
# accept. A request over that ceiling is rejected outright, so the failure is
# not "the model saw fewer images" but "the user's message never arrived".
#
# Same figure as the per-turn callback budget rather than a second spelling:
# one turn is one turn regardless of which path assembled it, and this is the
# number PLUGIN_DEVELOPMENT_GUIDE.md already advertises.
#
# This is a CEILING, not a quota — it never grants budget a per-source quota
# withheld. It only trims what the per-source quotas already let through.
#
# And it is NOT the binding limit everywhere. On the realtime WebSocket path a
# far smaller one binds first: config.session_settings OMNI_WS_FRAME_LIMIT_BYTES
# is 250_000 bytes for ONE serialized frame, measured on the JSON — i.e. after
# base64 has already inflated every image by ~4/3 — so roughly 180 KB of actual
# image bytes, some 45x tighter than the 8 MiB here. That path enforces it on
# its own and never consults this figure: omni_realtime_client/_responses.py
# re-compresses the item event and raises RealtimeImagePayloadTooLargeError if
# that still does not fit, and _transport.py does the same per frame (raising
# only when the caller asked for it, otherwise refusing the send). So passing
# this budget tells you nothing about whether a realtime turn will fit. Change
# either number and check the other: the cross-reference is written on both
# sides for that reason.
TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES = CALLBACK_IMAGE_MAX_TOTAL_BYTES

# What the manager queue may HOLD, as opposed to what one release may send.
#
# CALLBACK_IMAGE_MAX_* above bound a single model turn. The queue itself had
# only a TTL, and nothing bounded its depth or its bytes: cues accumulate
# precisely when the proactive claim keeps being denied (the user is talking),
# so a plugin pushing images into a busy conversation could hold hundreds of MB
# of base64 with nothing to stop it, each cue carrying its own full copy.
#
# The count matches the flood guard on pending_agent_callbacks, so a cue that
# survives one queue is not arbitrarily dropped by the other. The byte ceiling
# is four turns' worth of the documented per-turn budget -- generous enough
# that ordinary backlog is untouched, finite enough to bound the worst case.
QUEUED_CUE_MAX_COUNT = 50
QUEUED_IMAGE_MAX_TOTAL_BYTES = 4 * CALLBACK_IMAGE_MAX_TOTAL_BYTES


def approx_base64_decoded_bytes(encoded: str) -> int:
    """Decoded size of a base64 payload, without materializing the bytes."""
    return len(encoded) * 3 // 4


def trim_images_to_turn_budget(
    images: list[str],
    max_total_bytes: int = TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
) -> tuple[list[str], int]:
    """Trim one turn's attachments to what a single request may carry.

    ``images`` is in ATTACHMENT order, which is also chronological: the
    proactive screenshot (what the screen showed before the user spoke), then
    plugin-supplied context, then the user's own frames. Trimming takes from
    the FRONT, so the frames nearest the text -- the ones the message is
    actually about -- are the last to go, and a plugin cannot displace the
    frame the user just staged.

    The per-source quotas in this module bound each source separately and on
    purpose. This bounds their SUM, which is the only figure the provider
    sees. It never grants budget a per-source quota withheld; it only trims
    what those quotas already let through.

    The LAST image is kept unconditionally, mirroring the byte arm of the
    per-source quotas: it already passed its own per-image limit upstream, and
    sending one over-budget image for the provider to judge beats sending a
    message whose visual content silently vanished.

    Returns ``(kept, dropped_count)``; ``kept`` is a prefix-trimmed view, so
    the caller can tell exactly which leading attachments were dropped.
    """
    kept = list(images)
    dropped = 0
    total = sum(approx_base64_decoded_bytes(img) for img in kept)
    while len(kept) > 1 and total > max_total_bytes:
        total -= approx_base64_decoded_bytes(kept.pop(0))
        dropped += 1
    return kept, dropped


def split_callbacks_by_image_budget(callbacks: list) -> tuple[list, list]:
    """Split a FIFO batch into (deliverable prefix, overflow) on the image budget.

    The split is callback-ATOMIC: a callback is taken whole or left whole, so a
    taken callback keeps its complete ``media_images`` and the downstream
    preserve-until-success retry semantics stay intact.

    The head callback is always taken even when it alone exceeds the budget.
    Deferring it would park a cue that can never fit, and the queue would spin
    on it forever — the opposite of what this bound exists to prevent.

    Strict FIFO: once the budget is spent, everything after it defers, including
    text-only callbacks. Letting later text jump the queue would reorder cues
    against the narrative order the instruction renders them in.
    """
    taken: list = []
    overflow: list = []
    count = 0
    total_bytes = 0
    for callback in callbacks:
        if overflow:
            overflow.append(callback)
            continue
        images = callback.get("media_images") if isinstance(callback, dict) else None
        images = [img for img in (images or []) if isinstance(img, str) and img]
        if not images:
            taken.append(callback)
            continue
        cb_bytes = sum(approx_base64_decoded_bytes(img) for img in images)
        fits = (
            count + len(images) <= CALLBACK_IMAGE_MAX_COUNT
            and total_bytes + cb_bytes <= CALLBACK_IMAGE_MAX_TOTAL_BYTES
        )
        # ``count`` only advances for callbacks that contributed real images,
        # so a non-zero count IS "the prefix already claimed budget" — which is
        # the condition the head-progress rule turns on.
        if not fits and count > 0:
            overflow.append(callback)
            continue
        count += len(images)
        total_bytes += cb_bytes
        taken.append(callback)
    return taken, overflow


# 单轮图片超预算时保留的张数：开头 / 中间 / 结尾。与独立 ASR 的抽样判据同源
# ——一段视觉材料最有信息量的是它的两端和中点。
TURN_IMAGE_SAMPLE_KEEP = 3


def _sample_head_middle_tail(images: list) -> list:
    if len(images) <= TURN_IMAGE_SAMPLE_KEEP:
        return list(images)
    return [images[0], images[len(images) // 2], images[-1]]


async def fit_images_to_turn_budget(
    images: list,
    max_total_bytes: int,
) -> tuple[list, Optional[dict]]:
    """Bring one turn's images to the model profile, and under a byte budget.

    Rung 0 runs UNCONDITIONALLY: every image is normalized to the resolution
    profile we send models (``MODEL_IMAGE_MAX_WIDTH`` x
    ``COMPRESS_TARGET_HEIGHT``, JPEG q80) before anything else is even
    considered. That used to sit behind the "already under budget" early
    return, which made this a pure CEILING and left a real hole: an image only
    ever got downscaled when it was too BIG, so a plugin frame the SDK had
    normalized to 2048x1536 / ~49 KiB sailed under the 8 MiB budget and reached
    the model at 1536px high, full width, untouched. No path guaranteed a
    bounded resolution. Now one does, and it does not depend on the byte total.

    Rung 0 is a fixed point (``normalize_image_for_model`` returns an
    already-conforming payload unchanged), which matters because images ride
    ``_conversation_history`` for several more turns and would otherwise be
    re-encoded once per turn, degrading generationally.

    Below it, a single request carrying too many bytes is still rejected WHOLE
    by the provider, so something has to give. The order of what gives is
    unchanged and deliberate:

    1. **Sample** down to head/middle/tail. A long burst of frames is mostly
       redundant; its two ends and its midpoint carry nearly all of the signal.
       The frames in between are discarded WHOLE, not shrunk -- see the notice
       paragraph below, this rung is reported the same way a drop is.
    2. **Compress** what survives. Re-encoding to 720p JPEG typically cuts a
       screenshot several-fold and costs nothing a viewer would notice.
    3. Only if both fail, drop from the front (oldest first, always keeping the
       last one) -- and say so.

    Never drops the turn itself, and never drops images without reporting it.

    Returns ``(kept, notice)``; ``notice`` is None only when literally nothing
    happened -- already at the profile AND already under budget.

    The notice is NOT automatically a user-facing message any more. Rung 0
    fires on nearly every turn that carries an image, so a caller that toasted
    on any notice would toast constantly about routine housekeeping. Read
    ``notice["user_visible"]``: it is True whenever whole images LEFT the turn
    -- dropped by the final trim, or thrown away by the head/middle/tail
    sample, which keeps three frames and discards every other one. Those two
    rungs look different from in here (one is "redundancy", one is "content"),
    but they are the same event from the other side of the screen: a picture he
    sent is simply not there, and what she says next may not line up with it.
    Normalizing and re-compressing stay log-only, because they lose nothing a
    reader would notice -- every frame is still present, only smaller.
    Everything else in the notice (``normalized`` / ``sampled`` /
    ``compressed`` / ``dropped``) is there for the log either way.
    """
    kept = [img for img in (images or []) if img]
    if not kept:
        return kept, None

    original_count = len(kept)
    original_bytes = sum(approx_base64_decoded_bytes(img) for img in kept)

    # ── Rung 0：归一化到模型档位。跑在预算判断之前，所以这条保证与图片大小无关。
    from utils.screenshot_utils import normalize_image_for_model

    def _normalize_all(payloads: list) -> list:
        out = []
        for payload in payloads:
            try:
                out.append(normalize_image_for_model(payload))
            except Exception:
                # 单张失败不该拖累整轮：原样留着，下面的阶梯照旧管它的字节数。
                out.append(payload)
        return out

    try:
        normalized = await asyncio.to_thread(_normalize_all, kept)
    except Exception:
        normalized = kept
    # 归一化器对「已经合规」的图返回**同一个字符串对象**，所以身份比较正好是
    # 「这张图真被重编码了吗」的精确判据，不用再去比内容或长度。
    was_normalized = any(new is not old for new, old in zip(normalized, kept))
    kept = normalized
    total = sum(approx_base64_decoded_bytes(img) for img in kept)

    notice: dict = {
        "original_count": original_count,
        "original_bytes": original_bytes,
        "budget_bytes": max_total_bytes,
        "normalized": was_normalized,
        "sampled": False,
        "compressed": False,
        "dropped": 0,
    }

    if total <= max_total_bytes:
        if not was_normalized:
            # 图片本来就在档位内、也在预算内：什么都没发生，连日志都不必打扰。
            return kept, None
        notice["final_count"] = len(kept)
        notice["final_bytes"] = total
        notice["user_visible"] = False
        return kept, notice

    if len(kept) > TURN_IMAGE_SAMPLE_KEEP:
        kept = _sample_head_middle_tail(kept)
        notice["sampled"] = True
        total = sum(approx_base64_decoded_bytes(img) for img in kept)

    if total > max_total_bytes:
        from utils.screenshot_utils import decode_and_compress_screenshot_b64

        def _compress_all(payloads: list) -> list:
            out = []
            for payload in payloads:
                try:
                    shrunk = decode_and_compress_screenshot_b64(payload)
                except Exception:
                    out.append(payload)
                    continue
                # 压缩反而变大就保留原图（已经是小图/已压过的会这样）。
                out.append(shrunk if len(shrunk) < len(payload) else payload)
            return out

        try:
            compressed = await asyncio.to_thread(_compress_all, kept)
        except Exception:
            compressed = kept
        if sum(approx_base64_decoded_bytes(i) for i in compressed) < total:
            kept = compressed
            notice["compressed"] = True
            total = sum(approx_base64_decoded_bytes(img) for img in kept)

    # 兜底：压完仍超限，才轮到丢内容。复用既有的 trim —— 它的判据（从最旧丢、
    # 无条件保住最后一张）正是这一步要的。
    if total > max_total_bytes:
        kept, dropped = trim_images_to_turn_budget(kept, max_total_bytes)
        notice["dropped"] = dropped
        total = sum(approx_base64_decoded_bytes(img) for img in kept)

    notice["final_count"] = len(kept)
    notice["final_bytes"] = total
    # 判据是「读者少了一张图吗」，不是「哪一级跑了」。抽样排在丢弃前面、名字也
    # 温和，但 _sample_head_middle_tail 只留开头/中间/结尾三张，其余是整张扔掉的，
    # 从读者那一侧看跟 trim 掉的没有任何分别。归一化 / 重压则相反：每一帧都还在，
    # 只是小一点，而且 rung 0 几乎每个带图回合都会跑，弹了就是刷屏。
    notice["user_visible"] = notice["dropped"] > 0 or notice["sampled"]
    return kept, notice


def resolve_callback_delivery_ack(callback: dict, delivered: bool) -> None:
    """Resolve an optional in-memory delivery acknowledgement future."""
    future = callback.get(DELIVERY_ACK_FUTURE_KEY)
    if future is None:
        return
    try:
        if not future.done():
            future.set_result(bool(delivered))
    except Exception:
        logger.debug("delivery ack future resolution failed", exc_info=True)


def callback_is_expired(callback: dict, *, now: Optional[float] = None) -> bool:
    """Return whether a host-stamped callback deadline has elapsed."""
    deadline = callback.get(CALLBACK_EXPIRES_AT_KEY)
    if isinstance(deadline, bool):
        return False
    try:
        deadline_value = float(deadline)
    except (TypeError, ValueError, OverflowError):
        return False
    if not 0.0 < deadline_value < float("inf"):
        return False
    return (time.monotonic() if now is None else now) >= deadline_value


def effective_priority(raw: Any) -> int:
    # Repo-wide convention (the "greatest common denominator" of existing
    # producers): HIGHER number = more important. Matches every current
    # producer — bilibili gift/SC=9, memo reminder=8, study answer_evaluated=5
    # — and the HUD ``priority_min`` filter. Unspecified / invalid → 0 = least
    # important (a cue that didn't set a priority never preempts one that did).
    # minecraft is tagged on this SAME scale (alert highest). Within a release
    # batch, cues sort by importance DESC, then FIFO (see _QueuedCue.sort_key).
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return 0


class _QueuedCue:
    __slots__ = ("eff_priority", "seq", "coalesce_key", "callback", "submitted_at")

    def __init__(self, eff_priority: int, seq: int, coalesce_key: str,
                 callback: dict, submitted_at: float) -> None:
        self.eff_priority = eff_priority
        self.seq = seq
        self.coalesce_key = coalesce_key
        self.callback = callback
        self.submitted_at = submitted_at

    @property
    def sort_key(self) -> tuple[int, int]:
        # Importance DESC (higher = more important → first), then FIFO within
        # the same importance. Negate priority so a plain ascending sort yields
        # most-important-first.
        return (-self.eff_priority, self.seq)


class ProactiveDeliveryManager:
    def __init__(
        self,
        *,
        # Receives the WHOLE released batch (list of callback dicts), not a
        # single cue — releases are batched (see _pump / _run_deliver).
        deliver: Callable[[list[dict]], Awaitable[Any]],
        name: str = "",
        min_gap_s: float = 2.0,
        inflight_timeout_s: float = 12.0,
        ttl_s: float = 90.0,
        max_play_s: float = 45.0,
        can_release: Optional[Callable[[], bool]] = None,
        busy_recheck_s: float = 0.5,
    ) -> None:
        # ``deliver`` does the actual hand-off into the existing pipeline
        # (enqueue_agent_callback + trigger_agent_callbacks). Awaited inside
        # a task so a slow/blocking delivery can't stall the loop.
        self._deliver = deliver
        self._name = name
        self._min_gap_s = float(min_gap_s)
        self._inflight_timeout_s = float(inflight_timeout_s)
        self._ttl_s = float(ttl_s)
        # Optional predicate (core's gate, inverted): returns False when the
        # session is busy in ways the playback gate alone can't see — a
        # response still GENERATING (is_active_response, before any
        # voice_play_start) or the SM not IDLE. When it returns False we keep
        # cues IN the manager (so coalescing/priority still apply to later
        # cues) and recheck shortly, instead of releasing into the inner
        # trigger which would just defer them into pending_agent_callbacks,
        # outside manager ordering.
        self._can_release = can_release
        self._busy_recheck_s = float(busy_recheck_s)
        # Watchdog ceiling for a missing voice_play_end: above a normal single
        # reply, but short enough to recover a dropped end-signal reasonably
        # fast. The common cause (frontend disconnect/refresh) is already
        # handled by session teardown (_reset_proactive_gate on end_session/ws
        # drop); this only backstops the rare "connection alive but end signal
        # lost" case. Set above typical reply length so it doesn't cut off a
        # long answer mid-playback (Codex P2).
        self._max_play_s = float(max_play_s)

        self._queue: list[_QueuedCue] = []
        self._seq = itertools.count()

        # Gate state. ``_playing`` spans voice_play_start..voice_play_end (or
        # text_start..text_end). ``_inflight`` guards single-flight between a
        # release and its playback confirmation; ``_inflight_deadline`` lets
        # us recover if a released cue never produces playback (deferred by
        # the inner gate, text with no audio, frontend disconnect).
        self._playing = False
        self._play_start_ts = 0.0
        self._inflight = False
        self._inflight_deadline = 0.0
        self._last_play_end_ts = 0.0

        self._pump_handle: Optional[asyncio.TimerHandle] = None

    # ── helpers ──────────────────────────────────────────────────────────
    @property
    def min_gap_s(self) -> float:
        """Min seconds between proactive turns (read-only). Callers that retry
        delivery outside the manager (e.g. core's voice_play_end re-fire of a
        deferred cue) should honor this for pacing parity."""
        return self._min_gap_s

    def _now(self) -> float:
        return time.monotonic()

    def _resolve_key(self, callback: dict, coalesce_key: Optional[str]) -> str:
        # Coalescing is OPT-IN: a cue collapses with another only when both
        # set the SAME explicit coalesce_key. An unset key yields a unique
        # sentinel so the cue never coalesces. This is deliberate — defaulting
        # to ``source`` would silently drop DISTINCT important cues that share
        # a source (e.g. a bilibili gift vs a super-chat, two memo reminders,
        # a study answer vs mastery event), regressing every existing plugin
        # that emits multiple proactive cues. Plugins opt in by passing
        # coalesce_key (minecraft tags per category: mc_alert / mc_completion
        # / mc_in_progress / mc_keep_going).
        k = (coalesce_key or "").strip()
        if k:
            return k
        return f"__uniq:{next(self._seq)}"

    # ── producer ─────────────────────────────────────────────────────────
    def submit(self, callback: dict, *, priority: Any = 0,
               coalesce_key: Optional[str] = None) -> list[str]:
        key = self._resolve_key(callback, coalesce_key)
        eff = effective_priority(priority)
        # Coalesce: newest replaces any queued cue with the same key.
        if self._queue:
            dropped = [c for c in self._queue if c.coalesce_key == key]
            if dropped:
                self._queue = [c for c in self._queue if c.coalesce_key != key]
                for cue in dropped:
                    resolve_callback_delivery_ack(cue.callback, False)
                logger.debug(
                    "[proactive%s] coalesced %d queued cue(s) on key=%r",
                    self._suffix(), len(dropped), key,
                )
        self._queue.append(
            _QueuedCue(eff, next(self._seq), key, callback, self._now())
        )
        evicted_keys = self._enforce_queue_budget()
        logger.debug(
            "[proactive%s] submit key=%r eff_priority=%d queue=%d",
            self._suffix(), key, eff, len(self._queue),
        )
        self._schedule_pump(0.0)
        # Reported so the owner can rebuild per-key coalescing bookkeeping: a
        # cue whose seq was recorded at submit time but which the budget then
        # evicted must stop marking older same-key cues stale, or the older one
        # gets retracted for a replacement that no longer exists (Codex P2).
        return evicted_keys

    @staticmethod
    def _cue_image_bytes(cue: "_QueuedCue") -> int:
        images = cue.callback.get("media_images") if isinstance(cue.callback, dict) else None
        if not isinstance(images, (list, tuple)):
            return 0
        return sum(
            approx_base64_decoded_bytes(img) for img in images if isinstance(img, str)
        )

    def _enforce_queue_budget(self) -> list[str]:
        """Bound what the queue holds, by cue count and by queued image bytes.

        Both axes drop in REVERSE release order -- the cue that would have gone
        out last goes first -- so an over-budget burst sheds its own least
        important, most recent tail instead of the important cue that has
        waited longest. Release order is (priority DESC, FIFO), so that victim
        is simply the maximum sort_key.

        The two axes do NOT share a candidate pool, and that is the whole point
        of splitting the loop. "Shed your own tail" holds for the DEPTH axis,
        where every cue occupies exactly one slot, so evicting any cue makes
        progress. It does not hold for the BYTE axis, where only image-bearing
        cues occupy budget: picking the global maximum sort_key there selects a
        text-only cue with a near-certain probability -- text cues outnumber
        image cues and the lowest priority in the system belongs to first-party
        text (topic hooks submit at -20). Evicting it frees zero bytes, the
        loop does not terminate, and it keeps eating text cues until the image
        cues it was supposed to bound finally come into range. Measured on the
        shared-pool version: a burst of 48 text cues plus 8 full-size image
        cues left a queue of 4, against a depth ceiling of 50, with all 48 text
        cues acked False.

        That ack is not a silent drop -- topic hooks resolve a delivery future
        on it and burn their one-shot quota -- so the cost of picking the wrong
        victim lands on first-party features that never touched an image.

        Dropped cues are acked False, the same as a TTL drop: the producer is
        told its cue will not be delivered rather than being left waiting.
        """
        evicted_keys: list[str] = []
        if not self._queue:
            return evicted_keys

        def _evict(victim: "_QueuedCue", reason: str, remaining_bytes: int) -> int:
            self._queue.remove(victim)
            freed = self._cue_image_bytes(victim)
            if victim.coalesce_key:
                evicted_keys.append(victim.coalesce_key)
            resolve_callback_delivery_ack(victim.callback, False)
            logger.info(
                "[proactive%s] dropping queued cue key=%r reason=%s "
                "(depth=%d bytes=%d)",
                self._suffix(), victim.coalesce_key, reason,
                len(self._queue), remaining_bytes - freed,
            )
            return freed

        queued_bytes = sum(self._cue_image_bytes(c) for c in self._queue)

        # Depth axis: every cue occupies a slot, so any cue is a valid victim
        # and the rule is unchanged from the shared-pool version.
        while len(self._queue) > QUEUED_CUE_MAX_COUNT:
            victim = max(self._queue, key=lambda c: c.sort_key)
            queued_bytes -= _evict(victim, "queue_depth", queued_bytes)

        # Byte axis: same ordering rule, but only among cues that actually hold
        # bytes. Run second because freeing bytes can only shrink the queue --
        # it can never push the depth axis back over its ceiling, so no loop
        # back to the first pass is needed.
        while queued_bytes > QUEUED_IMAGE_MAX_TOTAL_BYTES:
            carriers = [c for c in self._queue if self._cue_image_bytes(c) > 0]
            if not carriers:
                # Unreachable while the running total is accurate: bytes can
                # only come from carriers. Kept as an accounting backstop so a
                # future bookkeeping bug degrades to "budget not enforced"
                # instead of spinning forever.
                logger.warning(
                    "[proactive%s] byte budget over (%d) with no image-bearing "
                    "cue to evict; leaving queue as-is",
                    self._suffix(), queued_bytes,
                )
                break
            victim = max(carriers, key=lambda c: c.sort_key)
            queued_bytes -= _evict(victim, "queue_bytes", queued_bytes)
        return evicted_keys

    def retract(self, callback: dict) -> bool:
        """Remove a not-yet-released callback from the manager queue."""
        callback[DELIVERY_RETRACTED_KEY] = True
        delivery_id = callback.get("_callback_delivery_id")
        callback_obj_id = id(callback)
        remaining: list[_QueuedCue] = []
        removed = False
        for cue in self._queue:
            queued = cue.callback
            same_callback = id(queued) == callback_obj_id
            same_delivery = (
                bool(delivery_id)
                and queued.get("_callback_delivery_id") == delivery_id
            )
            if same_callback or same_delivery:
                removed = True
                resolve_callback_delivery_ack(queued, False)
                continue
            remaining.append(cue)
        self._queue = remaining
        return removed

    # ── lifecycle signals (from LifecycleEventBus) ───────────────────────
    def on_playback_start(self, **_: Any) -> None:
        self._playing = True
        self._play_start_ts = self._now()

    def on_playback_end(self, **_: Any) -> None:
        self._playing = False
        self._inflight = False
        self._last_play_end_ts = self._now()
        # Wait out the min-gap before the next release.
        self._schedule_pump(self._min_gap_s)

    # text-mode boundaries reuse the same gating semantics
    on_text_start = on_playback_start
    on_text_end = on_playback_end

    def reset_gate(self) -> None:
        """Clear ONLY the playback-gate / single-flight state — NOT the queue.
        Call on session lifecycle boundaries so a dropped voice_play_end
        (frontend disconnect/refresh, teardown mid-playback) can't leave the
        gate stuck closed and wedge delivery. Queued cues are preserved; the
        caller drains them via drain_pending() and hands them to
        pending_agent_callbacks for redelivery, so proactive cues are never
        dropped on teardown (they are generally important)."""
        self._playing = False
        self._play_start_ts = 0.0
        self._inflight = False
        self._inflight_deadline = 0.0
        self._last_play_end_ts = 0.0
        if self._pump_handle is not None:
            self._pump_handle.cancel()
            self._pump_handle = None

    def drain_pending(self) -> list:
        """Pop and return all queued cue callbacks (clearing the queue), in the
        SAME priority order a normal release would use (priority asc, then
        FIFO). Used on session teardown to move not-yet-released cues into
        pending_agent_callbacks so the reconnect path redelivers them rather
        than losing them — exporting in queue/append order would drop the
        priority-asc + FIFO ordering, letting a late high-priority cue trail
        behind earlier low-priority ones on redelivery."""
        ordered = sorted(self._queue, key=lambda c: c.sort_key)
        self._queue = []
        return [cue.callback for cue in ordered]

    def latest_queued_coalesce_seq(self, key: str) -> Optional[int]:
        """Return the newest submit seq still waiting under ``key``."""
        return max(
            (
                cue.callback.get("_coalesce_submit_seq")
                for cue in self._queue
                if cue.coalesce_key == key
                and not cue.callback.get(DELIVERY_RETRACTED_KEY)
                and isinstance(cue.callback.get("_coalesce_submit_seq"), int)
            ),
            default=None,
        )

    # ── pump ─────────────────────────────────────────────────────────────
    def _suffix(self) -> str:
        return f":{self._name}" if self._name else ""

    def _schedule_pump(self, delay: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. constructed before the server is up).
            # A later signal/submit that runs in-loop will reschedule.
            return
        if self._pump_handle is not None:
            # Collapse multiple scheduled pumps; keep the soonest.
            self._pump_handle.cancel()
        self._pump_handle = loop.call_later(max(0.0, delay), self._pump)

    def _drop_stale(self) -> None:
        if not self._queue:
            return
        now = self._now()
        fresh: list[_QueuedCue] = []
        for c in self._queue:
            ttl_stale = self._ttl_s > 0 and now - c.submitted_at > self._ttl_s
            if ttl_stale:
                resolve_callback_delivery_ack(c.callback, False)
                logger.info(
                    "[proactive%s] dropping stale cue key=%r age=%.1fs reason=ttl",
                    self._suffix(), c.coalesce_key, now - c.submitted_at,
                )
            else:
                fresh.append(c)
        self._queue = fresh

    def release_inflight_noop(self) -> None:
        """Free the inflight slot for a release that delivered nothing.

        ``_pump`` arms ``_inflight`` before invoking ``deliver``, expecting a
        playback/text lifecycle signal (or the inflight timeout) to clear it.
        When a released batch delivers nothing — e.g. every cue is dropped at a
        release-time gate — no such signal arrives, so the slot would stay
        armed for the whole timeout window and hold back the next cue. The
        deliver callback calls this to release the slot immediately and re-pump.
        """
        self._inflight = False
        self._schedule_pump(0.0)

    def _pump(self) -> None:
        self._pump_handle = None
        self._drop_stale()
        if not self._queue:
            return
        now = self._now()
        if self._playing:
            # Watchdog: voice_play_end may never arrive (frontend disconnect /
            # refresh mid-playback). If playback has "run" longer than any
            # plausible utterance, treat the flag as stale and re-open the
            # gate rather than wedge the queue forever.
            if self._max_play_s > 0 and now - self._play_start_ts > self._max_play_s:
                logger.warning(
                    "[proactive%s] playback watchdog: no voice_play_end after %.0fs; clearing stuck playing flag",
                    self._suffix(), now - self._play_start_ts,
                )
                self._playing = False
            else:
                # Still audibly speaking — don't inject; re-check at the
                # watchdog deadline in case no end signal arrives.
                if self._max_play_s > 0:
                    self._schedule_pump(self._play_start_ts + self._max_play_s - now)
                return
        if self._inflight:
            if now < self._inflight_deadline:
                # Released cue still awaiting playback confirmation.
                self._schedule_pump(self._inflight_deadline - now)
                return
            # Timed out without playback — release the slot and continue.
            logger.debug("[proactive%s] inflight timed out; releasing slot", self._suffix())
            self._inflight = False
        gap_remaining = self._min_gap_s - (now - self._last_play_end_ts)
        if self._last_play_end_ts > 0.0 and gap_remaining > 0:
            self._schedule_pump(gap_remaining)
            return
        # Core-gate parity: the playback gate above can't see a response that's
        # still GENERATING (is_active_response, before any voice_play_start) or
        # an SM not-IDLE. Releasing then would have the inner trigger defer the
        # cues into pending_agent_callbacks — OUTSIDE manager ordering, so later
        # same-key/higher-priority cues couldn't coalesce/reorder them. Keep
        # them queued and recheck shortly instead (Codex P2).
        if self._can_release is not None:
            try:
                ok = bool(self._can_release())
            except Exception:
                ok = True  # predicate failure must not wedge delivery
            if not ok:
                self._schedule_pump(self._busy_recheck_s)
                return
        # Gate open: release the ENTIRE pending batch in one shot (sorted by
        # priority), preserving the legacy "near-simultaneous proactive cues
        # are drained into ONE LLM turn" behaviour. The playback gate above
        # already guaranteed she has finished speaking, so this batch won't
        # interrupt audio. Cues that arrive while she speaks accumulate and go
        # out as the next batch after voice_play_end + min-gap.
        batch = sorted(self._queue, key=lambda c: c.sort_key)
        self._queue = []
        self._inflight = True
        self._inflight_deadline = now + self._inflight_timeout_s
        callbacks = [c.callback for c in batch]
        logger.info(
            "[proactive%s] release batch n=%d keys=%s",
            self._suffix(), len(callbacks), [c.coalesce_key for c in batch],
        )
        asyncio.create_task(self._run_deliver(callbacks))
        # Arm the inflight-timeout: if no playback signal arrives (deliver
        # deferred by the inner gate / text with no audio / frontend
        # disconnect) the deadline pump frees the slot so later batches aren't
        # wedged. Normal completion (playback_end / next submit) reschedules a
        # sooner pump anyway.
        self._schedule_pump(self._inflight_timeout_s)

    async def _run_deliver(self, callbacks: list[dict]) -> None:
        callbacks = [
            callback
            for callback in callbacks
            if not callback.get(DELIVERY_RETRACTED_KEY)
        ]
        if not callbacks:
            self._inflight = False
            self._schedule_pump(0.0)
            return
        try:
            await self._deliver(callbacks)
        except Exception:
            for callback in callbacks:
                resolve_callback_delivery_ack(callback, False)
            logger.exception("[proactive%s] deliver failed", self._suffix())
            # Free the slot so the queue isn't wedged on a failed hand-off.
            self._inflight = False
            self._schedule_pump(0.0)
