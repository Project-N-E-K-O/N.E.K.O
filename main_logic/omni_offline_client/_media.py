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

from main_logic.proactive_delivery import (
    PLUGIN_PENDING_IMAGE_MAX_BYTES,
    PLUGIN_PENDING_IMAGE_MAX_COUNT,
    USER_PENDING_IMAGE_MAX_BYTES,
    USER_PENDING_IMAGE_MAX_COUNT,
    approx_base64_decoded_bytes,
)

from ._shared import (
    HumanMessage,
    logger,
    time,
)


class _MediaMixin:
    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Compatibility method - not used in text mode"""

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
