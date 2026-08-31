# -- coding: utf-8 --
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

import asyncio
from typing import Sequence

from config import MAX_MULTIMODAL_TURN_IMAGES
from main_logic.agent_event_bus import (
    publish_provider_frame_observed_best_effort,
    spawn_bounded_frame_copy,
)
from main_logic.proactive_delivery import (
    PLUGIN_PENDING_IMAGE_MAX_BYTES,
    PLUGIN_PENDING_IMAGE_MAX_COUNT,
    USER_PENDING_IMAGE_MAX_BYTES,
    USER_PENDING_IMAGE_MAX_COUNT,
    approx_base64_decoded_bytes,
)

from ._shared import (
    asyncio,
    HumanMessage,
    logger,
    time,
)


# Source labels for the frames the host copies onto the plugin bus.
#
# Coarse on purpose. Text mode receives every user-side frame through one
# ``stream_image(image_b64)`` call and core/streaming.py drops ``input_type``
# at that door, so from in here "the screen he is sharing", "his camera" and
# "a photo he dragged in" are one indistinguishable queue. A vaguer label that
# is true beats a precise one that is not: a plugin filtering on ``"screen"``
# must never be handed someone's dropped photo.
_FRAME_SOURCE_SCREEN = "screen"    # the proactive-vision screenshot
_FRAME_SOURCE_USER = "user"        # his pending queue + this turn's own frames
_FRAME_SOURCE_PLUGIN = "plugin"    # plugin `read` frames + passive callback media
# 主动搭话 / 问候 / agent 回调那一轮附上的图（prompt_ephemeral）。单独一个标签，
# 因为它和上面三个都不是一回事：这些帧不是用户分享的，用户甚至不知道有这么一轮，
# 一个按 "user" 过滤的插件绝不能读到它们。和 realtime 那侧对齐 —— 语音路径的同
# 一批帧就是以 source="proactive" 进总线的。
_FRAME_SOURCE_PROACTIVE = "proactive"
# Attribution is positional, so it only holds while the turn keeps its shape.
# ``_streaming.py`` falls back to this the moment the budget ladder changes the
# image count and the mapping can no longer be trusted.
_FRAME_SOURCE_UNKNOWN = "unknown"


class _MediaMixin:
    class _ExternalVoiceSubmitCancelled(Exception):
        """Business cancellation of one externally transcribed user turn."""

    @staticmethod
    async def _ignore_already_recorded_external_transcript(_text: str) -> None:
        return None

    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Compatibility method - not used in text mode"""

    async def _run_external_voice_stream(
        self,
        text: str,
        *,
        turn_images: tuple[str, ...] = (),
        turn_source: str | None = None,
        turn_source_count: int | None = None,
        turn_id: str | None = None,
        on_turn_committed=None,
    ) -> None:
        """Run one externally transcribed turn under cancellable task ownership."""

        task = asyncio.create_task(
            self.stream_text(
                text,
                turn_images=turn_images,
                turn_source=turn_source,
                turn_source_count=turn_source_count,
                turn_id=turn_id,
                on_turn_committed=on_turn_committed,
                input_transcript_callback=(
                    self._ignore_already_recorded_external_transcript
                ),
            )
        )
        self._external_voice_submit_task = task
        try:
            try:
                await task
            except asyncio.CancelledError as exc:
                parent = asyncio.current_task()
                if parent is not None and parent.cancelling():
                    raise
                # handle_interruption()/close() cancel the owned child, not the
                # serial transcript worker awaiting this wrapper. Convert that
                # child-cancel echo into an ordinary per-turn failure so the
                # worker can consume the next queued final.
                raise self._ExternalVoiceSubmitCancelled() from exc
        finally:
            if getattr(self, "_external_voice_submit_task", None) is task:
                self._external_voice_submit_task = None

    async def stream_image(
        self,
        image_b64: str,
        *,
        bypass_rate_limit: bool = False,
        cache_latest: bool = True,
        source: str = "user",
    ) -> None:
        """
        Add an image to pending images queue.
        Images will be sent together with the next text message.

        ``bypass_rate_limit`` is accepted for signature parity with the
        realtime client (text mode has no frame-rate throttle — it's an
        in-memory append) and is ignored here.

        ``cache_latest`` is accepted for the same reason. Text mode keeps no
        ambient frame cache, so there is nothing here for it to opt out of —
        but callers must be able to say "this is deliberate input, not an
        ambient screenshot" without first knowing which client they hold.

        ``source`` selects which per-source quota the frame is charged to.
        Anything other than ``"plugin"`` is the user's own frame.
        """
        if not image_b64:
            return

        # Bounded per SOURCE, not in aggregate. A shared cap has no correct
        # eviction policy here -- both candidates were tried during review and
        # both let one source damage the other. Separate queues mean an
        # over-quota push only ever drops its OWN oldest frame.
        if source == "plugin":
            queue = getattr(self, "_pending_plugin_images", None)
            if queue is None:
                # Instances built via __new__ (tests, legacy callers) never ran
                # __init__; mirror how the proactive slot is read defensively.
                queue = []
                self._pending_plugin_images = queue
            cap = PLUGIN_PENDING_IMAGE_MAX_COUNT
            byte_cap = PLUGIN_PENDING_IMAGE_MAX_BYTES
        else:
            queue = self._pending_images
            cap = USER_PENDING_IMAGE_MAX_COUNT
            byte_cap = USER_PENDING_IMAGE_MAX_BYTES

        queue.append(image_b64)
        dropped = 0
        # Count and bytes both, because they fail independently: three images
        # inside the count quota can still be ~24 MiB. The byte arm keeps the
        # LAST image unconditionally -- it bounds accumulation, and a lone
        # frame that is over already passed its own per-image limit upstream.
        while (
            len(queue) > cap
            or (
                len(queue) > 1
                and sum(approx_base64_decoded_bytes(i) for i in queue) > byte_cap
            )
        ):
            # pop(0), not a rebind: turn.py holds a reference to this exact
            # list object and clears it in place, so the identity is load-bearing.
            queue.pop(0)
            dropped += 1
        if dropped:
            logger.info(
                f"Dropped {dropped} oldest {source} image(s) over the "
                f"{cap}-image quota"
            )
        logger.info(
            f"Added image to pending queue "
            f"(source={source}, {source} total: {len(queue)})"
        )

    def _fire_bus_task(self, coro):
        """Run a best-effort bus copy off the turn, with GC protection.

        Every publish on this path can end up crossing loops:
        ``publish_session_event_threadsafe`` hands a cross-thread call to the
        bridge's owner loop through an UN-TIMED ``run_coroutine_threadsafe``.
        Awaiting that inside the model stream lets a stalled bridge hold up the
        first chunk, and with it the user's reply -- for a copy that is
        explicitly optional. The realtime client's ``_fire_task`` exists for
        this exact reason; the offline client has no such helper, so the media
        layer keeps its own rather than reaching across into another client.

        The caller must have snapshotted everything the coroutine reads BEFORE
        calling this. Moving the publish off the turn without freezing its
        inputs just relocates the race: the task would read session state as it
        is when it finally runs, not as it was at delivery.

        Bounded: while the far loop is stalled every pending copy still holds
        its base64, so past the cap new ones are refused rather than queued.

        Returns the task so tests and teardown can join it; nothing on the turn
        path does.
        """
        if getattr(self, "_bus_copies_closed", False):
            # close() has begun. A copy started now would outlive the session
            # it describes, and the drain has already run -- nothing would ever
            # collect it. Closing the coroutine keeps it from warning.
            coro.close()
            return None
        tasks = getattr(self, "_bus_bg_tasks", None)
        if tasks is None:
            tasks = set()
            self._bus_bg_tasks = tasks
        return spawn_bounded_frame_copy(coro, tasks, label="offline bus copy")

    async def _cancel_bus_copies(self) -> None:
        """End every in-flight bus copy. Called first thing in ``close()``.

        Without this a copy parked in the cross-thread handoff outlives the
        session: it keeps its base64 and its reference to ``self`` alive, and
        if the bridge ever recovers it publishes a frame for a session that is
        gone. Cancel then collect, so ``close()`` returns with nothing of this
        client's still scheduled.

        Collecting is what makes the cancel mean anything -- a cancelled task
        has not stopped until it has been awaited. ``return_exceptions`` keeps
        a copy that fails on its way out from turning into a teardown error;
        by this point nobody is going to read it either way.
        """
        # Set by close() before it calls this, so the set cannot grow while
        # it drains. Set here too for a direct caller.
        self._bus_copies_closed = True
        tasks = getattr(self, "_bus_bg_tasks", None)
        if not tasks:
            return
        # Snapshot: the done-callback discards from the live set as they end.
        draining = list(tasks)
        for task in draining:
            task.cancel()
        try:
            await asyncio.gather(*draining, return_exceptions=True)
        except Exception as exc:  # pragma: no cover - gather already absorbs
            logger.debug("bus copies did not drain cleanly: %s", exc)
        tasks.clear()

    def _publish_pending_tool_frames(
        self,
        pending: list | None,
        *,
        turn_id: str | None = None,
    ):
        """Publish the tool images a just-answered request carried. Best effort.

        ``pending`` is filled by ``_append_tool_result_images`` at the moment
        the pixels are written into the outgoing message list, and drained here
        by whichever tool loop sees the provider answer next. That ordering is
        the delivery gate: an injected image is only ever on the bus because a
        later request came back with something, which is the earliest point the
        host can honestly say the provider received it. A loop that runs out of
        iterations, or breaks out, never reaches a drain -- those pixels stay
        unpublished, and that is the correct direction. Under-publishing costs
        a plugin a picture; over-publishing is the host asserting a delivery
        that never happened.

        The drain itself -- read the list, empty it -- is SYNCHRONOUS, and
        only the publish moves off. Emptying it inside the task would put the
        clear after a suspension point, and a later round could drain the same
        frames again before the first task ever ran.

        ``source`` is ``plugin`` for every one of these, which is what they
        are: media a plugin handed the model, not a picture the user shared
        with her. The tool's name rides ``metadata`` rather than being folded
        into ``source`` -- the source vocabulary is a small closed set that
        plugins compare by equality, and a per-tool value there would break
        every such filter.
        """
        if not pending:
            return None
        drained = list(pending)
        pending.clear()
        return self._fire_bus_task(self._publish_provider_frames(
            [frame[0] for frame in drained],
            [_FRAME_SOURCE_PLUGIN] * len(drained),
            turn_id=turn_id,
            mimes=[frame[1] for frame in drained],
            metadatas=[{"tool_name": frame[2]} for frame in drained],
        ))

    async def _publish_provider_frames(
        self,
        images: Sequence[str],
        sources: Sequence[str],
        *,
        turn_id: str | None = None,
        mimes: Sequence[str] | None = None,
        metadatas: Sequence[dict] | None = None,
    ) -> None:
        """Copy this turn's outgoing frames onto the plugin bus. Best effort.

        Call this with the images that were ATTACHED -- after the budget
        ladder ran -- so what a plugin reads is byte-for-byte what the provider
        received. The ladder normalizes every frame to the model resolution
        profile and may re-compress it, so publishing the caller's originals
        would put a bigger, different picture on the bus than the model ever
        saw.

        Publish only once the provider has demonstrably received the turn --
        in ``stream_text`` that is the first streamed chunk, and it is NOT the
        moment the message lands in ``_conversation_history``. A committed turn
        can still die before any request is made (a raising input-transcript
        callback, a cancellation, three failed attempts), and publishing there
        would advertise a delivery that never happened. Under-publishing is the
        safe direction: plugins pull frames, so silence costs them a picture,
        while a false publish is the host asserting something untrue.

        ``mimes`` and ``metadatas`` align by index with ``images``, the same
        convention ``sources`` already uses. Both are optional because the
        ambient path has one mime for the whole turn and nothing to annotate;
        tool frames have neither property -- a tool may hand back a PNG, and
        the plugin that produced it is worth naming.

        Never raises into the turn. The copy is a courtesy to plugins, and a
        bus that is absent, down or slow must not cost the user a reply. The
        first failure ends the loop rather than retrying the rest: these
        failures are the transport being unavailable, not this one frame.
        Cancellation is deliberately NOT swallowed -- that is the session being
        torn down, and it belongs to the caller.
        """
        if not images:
            return
        # __new__-built instances (tests, legacy callers) never ran __init__,
        # read it the same defensive way the media queues are read.
        lanlan_name = str(getattr(self, "lanlan_name", "") or "") or None
        for index, image in enumerate(images):
            if not image:
                continue
            source = (
                sources[index]
                if index < len(sources)
                else _FRAME_SOURCE_UNKNOWN
            )
            extra: dict = {}
            if mimes is not None and index < len(mimes) and mimes[index]:
                extra["mime"] = str(mimes[index])
            if metadatas is not None and index < len(metadatas) and metadatas[index]:
                extra["metadata"] = dict(metadatas[index])
            try:
                await publish_provider_frame_observed_best_effort(
                    lanlan_name,
                    image_base64=image,
                    source=source,
                    turn_id=turn_id,
                    **extra,
                )
            except asyncio.CancelledError:
                raise
            except Exception as publish_error:
                logger.debug(
                    "provider frames not copied to the plugin bus: %s",
                    publish_error,
                )
                return

    async def submit_multimodal_turn(
        self,
        text: str,
        images: str | Sequence[str],
        *,
        turn_id: str,
        source: str | None = None,
    ) -> bool:
        """Submit one utterance's sampled raw frames with its transcript.

        Core samples the utterance down to first/middle/last before it gets
        here; this holds the same per-turn cap so provider-side history can
        never receive more than one utterance's worth of frames.

        Independent ASR already recorded and displayed the transcript before a
        possible session promotion. Suppress the regular Offline input callback
        here so the same user turn is not counted or persisted twice.
        """

        if isinstance(images, str):
            images = (images,)
        staged_images = tuple(image for image in (images or ()) if image)
        if not staged_images or not str(text or "").strip():
            raise ValueError("MULTIMODAL_TURN_REQUIRES_IMAGE_AND_TEXT")
        if len(staged_images) > MAX_MULTIMODAL_TURN_IMAGES:
            logger.warning(
                "multimodal turn over the per-turn image cap: %d supplied, "
                "keeping the first %d",
                len(staged_images),
                MAX_MULTIMODAL_TURN_IMAGES,
            )
            staged_images = staged_images[:MAX_MULTIMODAL_TURN_IMAGES]
        lock = getattr(self, "_multimodal_submit_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_submit_lock = lock

        async with lock:
            # 用户在这一轮之前已经投递的一次性附件（拖图 / 聊天贴图）确实属于这次
            # 发言，按快照一次取走并随本轮一起送；快照之后新到的附件留给下一轮，
            # 不会被本轮的 await 窗口顺手吞掉。
            #
            # 注意 MAX_MULTIMODAL_TURN_IMAGES 只闸上面那段环境帧，不闸这里的附件：
            # 那个数是「一次发声抽样成开头/中间/结尾」的含义，而附件是用户自己决定
            # 带几张的明确投递，按张数砍会静默丢掉他刚选中的图（普通文本轮同样不
            # 闸，两条路保持一致）。
            pending = getattr(self, "_pending_images", None)
            attachments: tuple[str, ...] = ()
            if isinstance(pending, list) and pending:
                attachments = tuple(pending)
                del pending[:len(attachments)]
                # 附件排在本轮抽样帧**之后**，不是之前。下游 fit/trim 一律「从最
                # 旧丢、无条件保住最后一张」，排前面等于让用户明确拖进来的图先
                # 死、环境抽样帧反而活着——用户看到的是"我给她的图她没看见，倒是
                # 讲了屏幕"。顺序契约与 _streaming.py 那条一致：越靠近文本的越该
                # 保住，而附件正是用户为这句话挑的。
                staged_images = staged_images + attachments
            # 这一轮的帧作为 invocation-local 数据直接交给 stream_text，不进
            # _pending_images。那条队列是 session 级的"下一个消费者拿走"：一次性
            # 附件（拖图 / 聊天贴图）不拿 _multimodal_submit_lock，完全可能在这里
            # staging 之后、子任务真正消费之前挤进队列，然后被本轮整批吞掉——用户
            # 那张图既配错了发言，也不再能给它自己的追问用。顺带失败路径也不需要
            # 再回滚一段共享队列。
            committed_to_history = False

            def _mark_committed() -> None:
                nonlocal committed_to_history
                committed_to_history = True

            try:
                await self._run_external_voice_stream(
                    text,
                    turn_images=staged_images,
                    # 与 realtime 侧同源：这批帧一起冻结的采集通道，不是会话
                    # 此刻的通道，也不是"用户附件"这个默认。只盖住抽样帧那一
                    # 段——附件是上面刚接到 staged_images 尾巴上的，它们仍是
                    # 用户自己给的东西。
                    turn_source=source,
                    turn_source_count=len(staged_images) - len(attachments),
                    # 独立 ASR 这一轮自带一个稳定的 turn_id，一路带到帧总线上，
                    # 插件才能把同一次发声抽出的几张帧认成一组。普通文本轮没有
                    # 这个身份，留空即可（记录里就不带 turn_id）。
                    turn_id=str(turn_id or "").strip() or None,
                    on_turn_committed=_mark_committed,
                )
            except BaseException as exc:
                # 本轮帧是 invocation-local 的，失败即消失；但取走的那段用户附件
                # 是共享状态，一次失败的 ASR 回合不该把用户明确投递的图吃掉。
                #
                # 只在这一轮**还没进 history** 时放回。被后一句话打断时，
                # stream_text 可能已经把带图的 HumanMessage 追加进去了，那些附件
                # 已经在上下文里；此时再放回队列，下一轮会把同一批图再发一遍，配
                # 上一段无关的 transcript。history 回滚不了，就以它为准。
                #
                # 判据是本次调用自己的回调，不是全局 history 长度：并发的另一条
                # 文本请求或收尾中的响应同样会追加，长度增长并不代表**这一轮**进
                # 去了，那会把用户的附件白白吃掉。
                if (
                    attachments
                    and isinstance(pending, list)
                    and not committed_to_history
                ):
                    pending[0:0] = attachments
                if isinstance(exc, self._ExternalVoiceSubmitCancelled):
                    return False
                raise
            return True

    async def submit_external_voice_turn(
        self,
        text: str,
        *,
        turn_id: str,
    ) -> bool:
        """Generate a text/TTS reply for a later image-free independent-ASR turn."""

        del turn_id
        if not str(text or "").strip():
            return False
        lock = getattr(self, "_multimodal_submit_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_submit_lock = lock
        async with lock:
            try:
                await self._run_external_voice_stream(text)
            except self._ExternalVoiceSubmitCancelled:
                return False
            return True

    def has_pending_images(self) -> bool:
        """Check if there are pending images waiting to be sent."""
        return len(self._pending_images) > 0

    def set_proactive_screenshot(self, image_b64: str | None) -> None:
        """Stage (or clear) the proactive-vision screenshot for the user's next reply.

        When proactive chat used the screen as its material, the committed
        AIMessage carries only text, so the conversation model can't see what
        was on screen when the user replies. This stashes that screenshot so the
        NEXT ``stream_text`` folds it in as leading visual context — symmetric
        with how ``_pending_images`` carries the user's own frame, but kept in a
        SEPARATE single-slot field: sharing ``_pending_images`` would steal the
        user's next frame (see core.py proactive media note / Codex P2).

        Pass ``None`` (e.g. a proactive round that obtained no screenshot) to
        clear, so the slot always reflects the most recent proactive round and a
        stale screenshot never trails a later talk. The stage timestamp arms the
        TTL (``_PROACTIVE_SCREENSHOT_TTL_SECONDS``) checked lazily at injection;
        the history-length marker pins the screenshot to the AI turn it was staged
        on, so a later proactive talk delivered through another path (greeting /
        agent callback via ``prompt_ephemeral``) supersedes it.
        """
        if image_b64:
            self._proactive_image_to_inject = image_b64
            self._proactive_image_staged_at = time.monotonic()
            self._proactive_image_history_len = len(self._conversation_history)
        else:
            self._proactive_image_to_inject = None
            self._proactive_image_staged_at = 0.0
            self._proactive_image_history_len = 0

    def _evict_old_images(self, keep_turns: int = 2) -> None:
        # 只保留最近 keep_turns 个含图 HumanMessage 的图片，更早的剥掉 image_url
        # 仅留文本。base64 图片在 vision tokenizer 下约 1.5k~3k tokens/张，
        # 多轮累积会把 input 推到 128k+。
        image_turn_indices = [
            idx for idx, msg in enumerate(self._conversation_history)
            if isinstance(msg, HumanMessage) and isinstance(msg.content, list)
            and any(isinstance(item, dict) and item.get("type") == "image_url" for item in msg.content)
        ]
        if len(image_turn_indices) <= keep_turns:
            return

        evicted_imgs = 0
        for idx in image_turn_indices[:-keep_turns]:
            old = self._conversation_history[idx]
            kept_parts = []
            for item in old.content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    evicted_imgs += 1
                else:
                    kept_parts.append(item)
            if len(kept_parts) == 1 and isinstance(kept_parts[0], dict) and kept_parts[0].get("type") == "text":
                self._conversation_history[idx] = HumanMessage(content=kept_parts[0].get("text", ""))
            else:
                self._conversation_history[idx] = HumanMessage(content=kept_parts)

        logger.info(
            f"🖼️ Evicted {evicted_imgs} image(s) from {len(image_turn_indices) - keep_turns} older turn(s); "
            f"kept images in last {keep_turns} turn(s)"
        )
