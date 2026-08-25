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

    async def _run_external_voice_stream(self, text: str) -> None:
        """Run one externally transcribed turn under cancellable task ownership."""

        task = asyncio.create_task(
            self.stream_text(
                text,
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
        image_b64: str,
        *,
        turn_id: str,
    ) -> bool:
        """Submit exactly one raw image with one transcript as a user turn.

        Independent ASR already recorded and displayed the transcript before a
        possible session promotion. Suppress the regular Offline input callback
        here so the same user turn is not counted or persisted twice.
        """

        if not image_b64 or not str(text or "").strip():
            raise ValueError("MULTIMODAL_TURN_REQUIRES_IMAGE_AND_TEXT")
        lock = getattr(self, "_multimodal_submit_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_submit_lock = lock

        async with lock:
            pending = getattr(self, "_pending_images", None)
            staged_index = len(pending) if isinstance(pending, list) else None
            await self.stream_image(image_b64, bypass_rate_limit=True)
            try:
                await self._run_external_voice_stream(text)
            except BaseException as exc:
                # stream_text consumes pending images as soon as it constructs
                # the HumanMessage. If it failed before that point, remove only
                # this turn's staged frame so it cannot leak into a later turn.
                current_pending = getattr(self, "_pending_images", None)
                if (
                    staged_index is not None
                    and current_pending is pending
                    and len(pending) > staged_index
                    and pending[staged_index] is image_b64
                ):
                    del pending[staged_index]
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
