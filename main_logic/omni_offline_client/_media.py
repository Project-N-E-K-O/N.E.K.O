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

from typing import Sequence

from config import MAX_MULTIMODAL_TURN_IMAGES

from ._shared import (
    asyncio,
    HumanMessage,
    logger,
    time,
)


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
    ) -> None:
        """Run one externally transcribed turn under cancellable task ownership."""

        task = asyncio.create_task(
            self.stream_text(
                text,
                turn_images=turn_images,
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

    async def stream_image(self, image_b64: str, *, bypass_rate_limit: bool = False) -> None:
        """
        Add an image to pending images queue.
        Images will be sent together with the next text message.

        ``bypass_rate_limit`` is accepted for signature parity with the
        realtime client (text mode has no frame-rate throttle — it's an
        in-memory append) and is ignored here.
        """
        if not image_b64:
            return

        # Store base64 image
        self._pending_images.append(image_b64)
        logger.info(f"Added image to pending queue (total: {len(self._pending_images)})")

    async def submit_multimodal_turn(
        self,
        text: str,
        images: str | Sequence[str],
        *,
        turn_id: str,
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
                staged_images = attachments + staged_images
            # 这一轮的帧作为 invocation-local 数据直接交给 stream_text，不进
            # _pending_images。那条队列是 session 级的"下一个消费者拿走"：一次性
            # 附件（拖图 / 聊天贴图）不拿 _multimodal_submit_lock，完全可能在这里
            # staging 之后、子任务真正消费之前挤进队列，然后被本轮整批吞掉——用户
            # 那张图既配错了发言，也不再能给它自己的追问用。顺带失败路径也不需要
            # 再回滚一段共享队列。
            try:
                await self._run_external_voice_stream(
                    text,
                    turn_images=staged_images,
                )
            except BaseException as exc:
                # 本轮帧是 invocation-local 的，失败即消失；但取走的那段用户附件
                # 是共享状态，必须放回队头，否则一次失败的 ASR 回合会把用户明确
                # 投递的图吃掉。
                if attachments and isinstance(pending, list):
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
