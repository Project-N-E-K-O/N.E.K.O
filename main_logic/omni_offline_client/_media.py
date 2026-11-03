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
# Images attached to proactive conversation / greeting / agent-callback turns (prompt_ephemeral) need a separate label,
# because they differ from all three sources above: users did not share these frames and may not know the turn exists.
# A plugin filtering "user" must never see them. Match realtime behavior: the voice path publishes the same
# frames to the bus with source="proactive".
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
        turn_context=None,
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
                turn_context=turn_context,
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
        turn_context=None,
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
            # One-shot attachments already submitted before this turn (dragged/pasted images) belong to this utterance.
            # Take them once via a snapshot and send with this turn; attachments arriving after the snapshot stay for the next turn,
            # rather than being swallowed during this turn's await window.
            #
            # MAX_MULTIMODAL_TURN_IMAGES gates only the ambient frames above, not these attachments:
            # that limit means sampling one utterance at beginning/middle/end, whereas attachments are explicitly
            # selected by the user. A count cap silently discards their chosen images (ordinary text turns likewise
            # impose no cap; keep both paths consistent).
            pending = getattr(self, "_pending_images", None)
            attachments: tuple[str, ...] = ()
            if isinstance(pending, list) and pending:
                attachments = tuple(pending)
                del pending[:len(attachments)]
                # Place attachments **after** this turn's sampled frames. Downstream fit/trim always drops oldest first
                # and preserves the final image unconditionally. Putting attachments first discards user-selected images
                # while retaining ambient frames, so users observe that their image was ignored
                # while the character discussed the screen. Match _streaming.py's order contract: images nearest the text
                # deserve preservation, and attachments were chosen specifically for this utterance.
                staged_images = staged_images + attachments
            # Pass this turn's frames directly to stream_text as invocation-local data, without
            # _pending_images. That session-wide queue is consumed by the next consumer; one-shot
            # dragged/pasted attachments do not take _multimodal_submit_lock and may enter it
            # after staging here but before subtask consumption, then be swallowed by this turn.
            # The user's image would be paired with the wrong utterance and unavailable for its own follow-up.
            # This also removes the need to roll back shared-queue state on failure.
            committed_to_history = False

            def _mark_committed() -> None:
                nonlocal committed_to_history
                committed_to_history = True

            try:
                await self._run_external_voice_stream(
                    text,
                    turn_images=staged_images,
                    # Match realtime provenance: use the capture channel frozen with this frame batch,
                    # not the session's current channel or the default "user attachment". Apply only to
                    # sampled frames; attachments were appended to staged_images above and remain
                    # explicitly supplied user material.
                    turn_source=source,
                    turn_source_count=len(staged_images) - len(attachments),
                    # Standalone ASR supplies a stable turn_id for this utterance; carry it through to the frame bus
                    # so plugins can group frames sampled from the same utterance. Ordinary text turns have
                    # no such identity; leave it empty (omit turn_id from records).
                    turn_id=str(turn_id or "").strip() or None,
                    on_turn_committed=_mark_committed,
                    # Additional context for this turn (knowledge/corpus/memory), fetched by Core before submission.
                    turn_context=turn_context,
                )
            except BaseException as exc:
                # This turn's frames are invocation-local and disappear on failure, but consumed user attachments
                # are shared state: a failed ASR round must not swallow explicitly supplied images.
                #
                # Restore them only if this turn has **not entered history**. When interrupted by another utterance,
                # stream_text may already have appended the image-bearing HumanMessage, so the attachments
                # are already in context. Requeueing would send them again next turn alongside an unrelated
                # transcript. History cannot be rolled back, so treat it as authoritative.
                #
                # Use this invocation's callback, not global history length: concurrent text requests
                # or finishing responses also append history. Length growth does not prove this turn
                # entered history and would needlessly lose the user's attachments.
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
        turn_context=None,
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
                await self._run_external_voice_stream(text, turn_context=turn_context)
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
        # Retain images only in the most recent keep_turns image-bearing HumanMessages; remove image_url
        # from older ones, keeping text. Base64 images cost roughly 1.5k–3k vision tokens each,
        # so accumulated turns can push input beyond 128k.
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
                replacement = HumanMessage(content=kept_parts[0].get("text", ""))
            else:
                replacement = HumanMessage(content=kept_parts)
            self._conversation_history[idx] = replacement
            # This turn's resident knowledge block follows the replaced message.
            self._rebind_resident_anchor(old, replacement)

        logger.info(
            f"🖼️ Evicted {evicted_imgs} image(s) from {len(image_turn_indices) - keep_turns} older turn(s); "
            f"kept images in last {keep_turns} turn(s)"
        )
