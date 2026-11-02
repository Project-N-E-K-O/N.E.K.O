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
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    Any,
    Callable,
    Dict,
    GEMINI_TURN_IMAGE_MIME,
    OMNI_WS_FRAME_LIMIT_BYTES,
    Optional,
    VisualDeliveryMode,
    asyncio,
    base64,
    json,
    logger,
    new_client_item_id,
    response_arbiter_fail_open_enabled,
    time,
    uuid,
)

from typing import Sequence

from config import MAX_MULTIMODAL_TURN_IMAGES
from main_logic.proactive_delivery import (
    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
    fit_images_to_turn_budget,
)
from config.prompts.prompts_proactive import (
    REALTIME_PROACTIVE_GENERAL_TRIGGER_PROMPTS,
    REALTIME_PROACTIVE_VISION_TRIGGER_PROMPTS,
    normalize_proactive_prompt_locale,
)
from config.prompts.prompts_sys import _loc

from ._response_arbiter import RealtimeResponseArbiter, ResponseTicket
from ._protocol_capabilities import (
    MultimodalTurnDelivery,
    STRICT_REALTIME_PROTOCOL_CAPABILITIES,
)


# A missing response.done must fail conservatively instead of acknowledging a
# delivery that the provider may still reject. Normal proactive responses
# finish well inside this backstop; it primarily prevents a dead connection
# from leaving the scheduler request open forever.
_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS = 30.0
_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS = 3.0
# How long a Gemini proactive inject waits for in-flight tool work to settle.
# BOUNDED on purpose -- see ``_settle_tools_before_gemini_proactive``. Reuses
# the cancel grace budget rather than inventing a second number.
_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS = 3.0
_PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS = 0.5
_GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL = "Gemini session is closing"
_GEMINI_PROACTIVE_TASK_UNSET = object()


def _proactive_text_instruction(language: str, *, has_vision: bool) -> str:
    lang = normalize_proactive_prompt_locale(language or "en")
    prompts = (
        REALTIME_PROACTIVE_VISION_TRIGGER_PROMPTS
        if has_vision
        else REALTIME_PROACTIVE_GENERAL_TRIGGER_PROMPTS
    )
    return _loc(prompts, lang)


class _ResponseMixin:
    def get_conversation_turn_type(self) -> str:
        """Classify delivered content by its provider response ownership."""
        if getattr(self, "_is_gemini", False):
            owner = getattr(self, "_gemini_proactive_outcome_owner", None)
            proactive = bool(
                owner is not None
                and len(owner) > 4
                and owner[0] == getattr(self, "_connection_generation", None)
                and owner[1] is getattr(self, "_gemini_session", None)
                and owner[2] == getattr(self, "_proactive_inject_outcome_token", None)
                and owner[4] == getattr(self, "_tool_scope_generation", 0)
            )
        else:
            # Captured when accepted start evidence opens the response. It
            # survives terminal bookkeeping before a final transcript flush.
            proactive = getattr(self, "_current_response_source", None) == "proactive"
        return "proactive_reply" if proactive else "assistant_message"

    def _ensure_response_arbiter(self) -> RealtimeResponseArbiter:
        arbiter = getattr(self, "_response_arbiter", None)
        if arbiter is None:
            trace_enabled = bool(getattr(self, "_wire_trace_enabled", False))
            wire_trace = getattr(self, "_wire_trace", None)
            arbiter = RealtimeResponseArbiter(
                self.send_event,
                abort_transport=getattr(self, "_abort_failed_transport", None),
                fail_open=response_arbiter_fail_open_enabled(),
                on_stuck_release=getattr(self, "_on_arbiter_stuck_release", None),
                protocol_capabilities=getattr(
                    self,
                    "_realtime_protocol_capabilities",
                    STRICT_REALTIME_PROTOCOL_CAPABILITIES,
                ),
                trace=trace_enabled,
                trace_tag=getattr(wire_trace, "client_tag", None),
                trace_generation=(
                    (lambda: getattr(self, "_connection_generation", None))
                    if trace_enabled
                    else None
                ),
            )
            self._response_arbiter = arbiter
        return arbiter

    async def prime_context(self, text: str, skipped: bool = False) -> None:
        """Inject context during hot-swap.

        Behaviour depends on the skipped parameter and the provider:

        - ``skipped=True`` (or Qwen): appended to the system instructions
          via ``session.update``, without triggering a model response.
        - ``skipped=False`` (GPT/GLM/Step): injects a one-shot user message
          via ``create_response`` and triggers a model response (used for
          proactively reporting task results). Note: this path does not
          write to session instructions; the text is transient — do not
          change it to persist into instructions.
        - Gemini: injected via ``send_client_content`` regardless of
          skipped (SDK limitation, no session.update mechanism). When
          skipped=True the response is silently discarded via
          ``_skip_until_next_response``.

        Args:
            text: Context to inject (incremental cache + summary/ready).
            skipped: If True, only update instructions without triggering
                     a response. If False, also trigger model response.
        """
        if not text or not text.strip():
            logger.info("prime_context: skipping empty content")
            return

        if self._is_gemini:
            # Gemini Live API lacks session.update; inject context only via
            # send_client_content (creates a user turn).
            # _handle_messages_gemini naturally triggers on_response_done.
            await self._create_response_gemini_with_skip_guard(
                text,
                skipped=skipped,
                raise_on_error=True,
                starts_user_turn=False,
            )
            return

        if not skipped and "qwen" not in self._model_lower:
            # skipped=False: request active model response (task-result reporting).
            # Inject user message and trigger response through create_response.
            # Qwen lacks conversation.item.create; use update_session below.
            # Transport-only user message: a task-result report is not the
            # real user's turn, so it must not retire in-flight tool calls.
            await self.create_response(text, starts_user_turn=False)
        else:
            # skipped=True or Qwen: append only to session instructions.
            lock = getattr(self, "_prime_context_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                self._prime_context_lock = lock
            async with lock:
                current_instructions = str(self.instructions or "")
                next_instructions = (
                    current_instructions + "\n" + text
                    if current_instructions
                    else text
                )
                await self.update_session({"instructions": next_instructions})
                self.instructions = next_instructions
            logger.info("prime_context: updated session instructions")

    async def create_response(
        self,
        instructions: str,
        skipped: bool = False,
        *,
        starts_user_turn: bool = True,
    ) -> None:
        """Inject a persistent user message and trigger an LLM response.

        ``starts_user_turn=False`` marks an injection that uses a provider
        ``role=user`` message purely as transport and does NOT replace the
        real user's turn (a background task-result report, for instance).
        Only a real user turn may retire in-flight tool calls, so keeping the
        distinction here is what stops a system-initiated report from
        cancelling a running tool and leaving its ``function_call``
        unanswered. Mirrors ``_gemini_send_user_turn``'s keyword of the same
        name.

        Unlike ``prime_context`` (which appends to the system instructions),
        this method creates a user-role conversation message and triggers a
        model response. Suited to mid-conversation scenarios where an
        immediate model reply is needed.

        Note: requires that the session already contains a user message, or
        that the API in use supports ``conversation.item.create``; otherwise
        a 1007 error may be triggered.

        Behaviour varies by provider:
          - **OpenAI / GLM / Step**: ``conversation.item.create(role=user)``
            + ``response.create``
          - **Gemini**: ``send_client_content(role=user)``

        See ``prime_context()`` (session-start priming) and
        ``prompt_ephemeral()`` (guarded proactive text turn) for the other two
        injection channels.
        """
        # Gemini sends text through send_client_content.
        if self._is_gemini:
            if not instructions or not instructions.strip():
                logger.info("Gemini: skipping empty content in create_response")
                return
            await self._create_response_gemini_with_skip_guard(
                instructions,
                skipped=skipped,
                raise_on_error=True,
                starts_user_turn=starts_user_turn,
            )
            return

        # Skip empty content to avoid API errors.
        if not instructions or not instructions.strip():
            logger.info("Skipping empty content in create_response")
            return

        if starts_user_turn:
            self.note_user_turn_started()

        if skipped:
            self._skip_until_next_response = True

        item_event_id = f"event_user_item_{uuid.uuid4().hex}"
        response_event_id = f"event_user_response_{uuid.uuid4().hex}"
        item_id = new_client_item_id()
        expected_item_id = item_id
        # Add user message via conversation.item.create, then trigger response. Both steps enter
        # the global arbiter, which releases the next create only at response.done.
        item_event = {
            "type": "conversation.item.create",
            "event_id": item_event_id,
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": instructions
                    }
                ]
            }
        }
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        logger.info("Creating response with user message")
        ticket = await self._ensure_response_arbiter().enqueue(
            source="create_response",
            events_before_response=(item_event,),
            response_event={
                "type": "response.create",
                "event_id": response_event_id,
            },
            ack_expected=True,
            expected_item_id=expected_item_id,
            expected_item_role="user",
        )
        await ticket.sent

    async def submit_external_text_turn(self, text: str, *, turn_id: str):
        """Persist one completed ASR turn and request a reply.

        The transcript is stored as a user conversation item, then a bare
        ``response.create`` is issued — the same pattern as
        ``create_response`` and the proactive inject path. Do not attach
        per-response ``response.instructions`` here: OpenAI Realtime and
        compatible protocols treat them as a replacement for the session
        instructions (the persona system prompt), not an addition. Item
        transport loss is covered by the arbiter's item-ack barrier. The
        caller must pass only a Smart Turn completion, never an ASR partial
        or segment final.
        """
        if getattr(self, "_is_gemini", False):
            raise RuntimeError(
                "external ASR text turns use the existing Gemini SDK path"
            )

        import hashlib

        clean = str(text or "").strip()
        if not clean:
            raise ValueError("external ASR turn must not be empty")
        if len(clean) > 8_000:
            raise ValueError("external ASR turn exceeds the 8000 character budget")
        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("external ASR turn_id must not be empty")

        self.note_user_turn_started()

        event_suffix = uuid.uuid4().hex
        item_id = new_client_item_id()
        expected_item_id = item_id
        item_event = {
            "type": "conversation.item.create",
            "event_id": f"event_asr_item_{event_suffix}",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": clean}],
            },
        }
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        response_event = {
            "type": "response.create",
            "event_id": f"event_asr_response_{event_suffix}",
        }
        text_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
        logger.info(
            "external_turn queued turn=%s chars=%d hash=%s",
            stable_turn_id,
            len(clean),
            text_hash,
        )
        arbiter = self._ensure_response_arbiter()
        ticket = await arbiter.enqueue(
            source="external_asr",
            events_before_response=(item_event,),
            response_event=response_event,
            ack_expected=True,
            expected_item_id=expected_item_id,
            expected_item_role="user",
            priority=0,
        )
        # Speech-start pauses dispatch. Resume only after this priority-0 user
        # turn is present, so queued proactive work cannot win the race. An
        # older completed turn may still be ahead of a newer paused turn in
        # the serial transcript dispatcher; let that ticket through, then
        # restore the newer pause. Before selecting again, the arbiter
        # explicitly yields to this ticket's waiter; its post-selection gate
        # then returns any concurrently blocked work without charging the
        # fairness allowance.
        active_pause_id = getattr(self, "_external_voice_turn_pause_id", None)
        if active_pause_id == stable_turn_id:
            self._external_voice_turn_pause_id = None
        arbiter.allow_ticket_while_paused(ticket)
        if active_pause_id in (None, stable_turn_id):
            arbiter.resume_dispatch()
        try:
            await ticket.sent
        except asyncio.CancelledError:
            await arbiter.cancel_ticket(ticket)
            raise
        finally:
            # Re-arm the newer turn's pause on the failure path too: a
            # transport error (or a newer prepare's cancel_current) can fail
            # ``ticket.sent`` after the resume above already released that
            # newer turn's pause, and without this re-pause queued proactive
            # work could dispatch ahead of that turn's user text.
            if (
                active_pause_id is not None
                and active_pause_id != stable_turn_id
                and getattr(self, "_external_voice_turn_pause_id", None)
                == active_pause_id
            ):
                arbiter.pause_dispatch(active_pause_id)
        return ticket

    def get_multimodal_turn_delivery(self) -> MultimodalTurnDelivery:
        """Return the provider adapter's atomic text+image capability."""

        capabilities = getattr(
            self,
            "_realtime_protocol_capabilities",
            STRICT_REALTIME_PROTOCOL_CAPABILITIES,
        )
        return capabilities.multimodal_turn_delivery

    @staticmethod
    def _decode_multimodal_turn_image(image_b64: str) -> bytes:
        """Validate one image payload before it can enter provider history."""

        from utils.screenshot_utils import MAX_BASE64_SIZE

        if not isinstance(image_b64, str) or not image_b64:
            raise ValueError("multimodal turn image must not be empty")
        if len(image_b64) > MAX_BASE64_SIZE:
            raise ValueError("multimodal turn image exceeds the payload budget")
        try:
            return base64.b64decode(image_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("multimodal turn image is not valid base64") from exc

    @classmethod
    def _normalize_multimodal_turn_images(
        cls,
        images: str | Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[bytes, ...]]:
        """Validate the turn's frames and hold them to the per-turn cap.

        Core samples one utterance down to first/middle/last before it gets
        here. This is the provider-side floor for that contract: whatever the
        caller passes, at most ``MAX_MULTIMODAL_TURN_IMAGES`` frames may enter
        provider history, and provider history cannot be edited afterwards.
        """

        if isinstance(images, str):
            images = (images,)
        staged = tuple(images or ())
        if not staged:
            raise ValueError("multimodal turn requires at least one image")
        if len(staged) > MAX_MULTIMODAL_TURN_IMAGES:
            logger.warning(
                "external_multimodal_turn over the per-turn image cap: "
                "%d supplied, keeping the first %d",
                len(staged),
                MAX_MULTIMODAL_TURN_IMAGES,
            )
            staged = staged[:MAX_MULTIMODAL_TURN_IMAGES]
        return staged, tuple(
            cls._decode_multimodal_turn_image(image) for image in staged
        )

    async def submit_multimodal_turn(
        self,
        text: str,
        images: str | Sequence[str],
        *,
        turn_id: str,
        visual_still_owned=None,
        source: str | None = None,
    ):
        """Submit one atomic raw-image + external-ASR user turn.

        Unsupported Realtime protocols fail closed so Core can hand the whole
        turn to an Offline VLM. This method never invokes the annotation model
        and never degrades a visual turn into text-only input.
        """

        if self.get_multimodal_turn_delivery() is not (
            MultimodalTurnDelivery.DIRECT_ATOMIC
        ):
            raise RuntimeError("realtime multimodal turn requires VLM handoff")
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("external ASR turn must not be empty")
        if len(clean) > 8_000:
            raise ValueError("external ASR turn exceeds the 8000 character budget")
        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("external voice turn_id must not be empty")
        # This turn's channel label, fixed before ANY await below.
        #
        # The caller passes ``MultimodalTurn.source`` -- the value frozen with
        # these very frames, and the only one that truly belongs to this turn.
        # The live fallback is read here too, for the same reason: trimming,
        # the arbiter queue and the SDK send are all awaits, and _transport
        # overwrites _latest_image_source on every staged frame. Reading it at
        # the publish point reads the channel the session moved on to. The
        # frames would be right and the label wrong -- and a plugin filtering
        # the bus by source is exactly who gets the wrong ones.
        frame_source = str(
            source or getattr(self, "_latest_image_source", "") or "unknown"
        )
        staged_images, images_bytes = self._normalize_multimodal_turn_images(
            images
        )

        if self._is_gemini:
            # Above limits only image **count** and **individual** size; three valid frames can still total nearly
            # 30 MB. WebSocket later recompresses/drops frames by aggregate size; Offline uses
            # fit_images_to_turn_budget. Only Gemini directly passes bytes to
            # send_client_content() without an aggregate gate, so provider rejects the whole oversized request,
            # losing the user's turn.
            #
            # Use Offline's ladder: sample, compress, and drop only as last resort, keeping at least one image.
            _fitted, _notice = await fit_images_to_turn_budget(
                list(staged_images),
                TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
            )
            if _notice:
                # This path lacks on_status_message and logs only, but severity must follow
                # user_visible: routine normalization occurs every turn, so warning would flood
                # logs; only actual image loss deserves warning.
                _emit = (
                    logger.warning if _notice.get("user_visible") else logger.info
                )
                _emit(
                    "Gemini multimodal turn fitted for the %d-byte aggregate "
                    "budget: %d -> %d image(s) "
                    "(normalized=%s sampled=%s compressed=%s dropped=%d)",
                    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
                    _notice["original_count"],
                    _notice["final_count"],
                    _notice.get("normalized"),
                    _notice["sampled"],
                    _notice["compressed"],
                    _notice["dropped"],
                )
                images_bytes = tuple(
                    self._decode_multimodal_turn_image(image)
                    for image in _fitted
                )
                # Above asyncio.to_thread compresses several MB-sized screenshots, a real, lengthy yield
                # during which a successor utterance can complete _begin_core_multimodal_turn (synchronously invalidating
                # this turn's record) + prepare_external_voice_turn +
                # handle_interruption. The paths do not mutually exclude (first takes no swap lock,
                # this function no turn-admission lock). Without rechecking, frames no longer owned by
                # this turn reach provider without any signal to Core, so existing text-only
                # degradation cannot run.
                #
                # Use caller-supplied visual_still_owned, same criterion as Offline handoff
                # (asr_runtime.py's lambda: not visual_ownership_lost()).
                # Deliberately do **not** proxy with _tool_scope_generation: create_response and
                # submit_external_text_turn also advance it, falsely degrading this turn
                # even without a successor voice turn.
                #
                # Dropping frames degrades to text only, still sending speech, consistent with this PR's three other ownership checks.
                #
                # Warning: recheck **not** here but inside _submit_external_gemini_turn,
                # immediately before SDK send, for two reasons:
                #   1. Here it would be inside `if _notice:`, never running without image trimming;
                #   2. Another yield follows at _submit_external_gemini_turn's initial
                #      _await_gemini_external_quarantine(), where a successor turn may
                #      invalidate this turn's record.
            await self._submit_external_gemini_turn(
                clean,
                images_bytes=images_bytes,
                visual_still_owned=visual_still_owned,
                turn_id=stable_turn_id,
                source=frame_source,
            )
            return None
        if self.ws is None or self._fatal_error_occurred:
            raise RuntimeError("realtime websocket is not connected")

        import hashlib

        event_suffix = uuid.uuid4().hex
        item_id = new_client_item_id()
        item_event = {
            "type": "conversation.item.create",
            "event_id": f"event_asr_multimodal_item_{event_suffix}",
            "item": {
                "id": item_id,
                "type": "message",
                "role": "user",
                # Beginning/middle/end share one user item: an utterance spans time, so three frames in
                # temporal order before transcript tell the model which visuals accompany it.
                # Still trigger one response only.
                "content": [
                    *(
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64," + image,
                        }
                        for image in staged_images
                    ),
                    {"type": "input_text", "text": clean},
                ],
            },
        }
        response_event = {
            "type": "response.create",
            "event_id": f"event_asr_multimodal_response_{event_suffix}",
        }
        # Check transport limit here rather than waiting for send_event. That path only returns
        # False on failure, ignored by arbiter _worker_send; after dropping the whole
        # conversation.item.create including transcript, it still sends
        # response.create, giving an answer unrelated to this utterance.
        #
        # Recompress/drop frames early (helper mutates item_event in place so send_event
        # does not repeat it). Raise if even one image cannot fit, letting Core use existing whole-turn
        # fail-closed handling (ASR_MULTIMODAL_TURN_FAILED), rather than silent text-only degradation,
        # explicitly forbidden by this PR.
        # Warning: degrade **before** size checking. Lost-ownership frames will not be sent,
        # so remove first; otherwise frames destined for discard that cannot fit would raise
        # RealtimeImagePayloadTooLargeError and fail the whole turn (Core's
        # ASR_MULTIMODAL_TURN_FAILED), instead of text-only degradation with speech still sent.
        def _downgrade_if_visual_ownership_lost(event: Dict[str, Any]) -> None:
            if visual_still_owned is None or visual_still_owned():
                return
            _item = event.get("item")
            if not isinstance(_item, dict):
                return
            _content = _item.get("content")
            if not isinstance(_content, list):
                return
            _kept = [
                part
                for part in _content
                if not (
                    isinstance(part, dict) and part.get("type") == "input_image"
                )
            ]
            if len(_kept) != len(_content):
                logger.info(
                    "external multimodal turn %s lost visual ownership; "
                    "submitting text-only",
                    stable_turn_id,
                )
                _item["content"] = _kept

        # Run twice at different positions for different purposes:
        #   Here before enqueue: cover recompression/frame-dropping to_thread yield above and
        #     reduce payload before occupying an arbiter slot;
        #   pre_commit before dispatch/_worker_send: cover waits inside arbiter
        #     (active response completion, send semaphore), beyond caller's reach.
        # Both remove only images and retain transcript, never reject the whole item; rejection occurs **after commit**,
        # requiring compensating-deletion acknowledgement before degradation (issue #2982).
        _downgrade_if_visual_ownership_lost(item_event)

        item_payload = json.dumps(item_event)
        if len(item_payload) > OMNI_WS_FRAME_LIMIT_BYTES:
            shrunk = await asyncio.to_thread(
                self._try_shrink_image_payload,
                item_event,
                item_payload,
            )
            if shrunk is None:
                from ._transport import RealtimeImagePayloadTooLargeError

                raise RealtimeImagePayloadTooLargeError(
                    "multimodal turn item exceeds the realtime frame limit"
                )
        text_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
        logger.info(
            "external_multimodal_turn queued turn=%s chars=%d images=%d hash=%s",
            stable_turn_id,
            len(clean),
            len(staged_images),
            text_hash,
        )
        # Final visual-ownership recheck before arbiter submission, symmetric with Gemini path.
        #
        # Recompression/frame dropping above runs through asyncio.to_thread, a real yield. Successor turn's
        # _begin_core_multimodal_turn **synchronously** invalidates this turn's record,
        # but subsequent prepare_external_voice_turn may still wait for turn-admission lock,
        # leaving _external_voice_turn_pause_id unchanged. admission_check below
        # would pass using that stale pause id, sending expired frames into provider history. The two criteria
        # act at different times; admission_check cannot cover this interval.
        #
        # Remove images in place while retaining transcript instead of admission_check rejecting the whole item:
        # that rejection happens **after commit**, requiring compensating-deletion confirmation (issue #2982),
        # far costlier than removing frames before commit. Frame loss degrades only visuals; speech still sends.
        arbiter = self._ensure_response_arbiter()
        # send_event's boolean is the only place the transport ever says "these
        # bytes left the socket", and the arbiter drops it on the floor
        # (_send_queued_event awaits the sender and ignores what it returns).
        # So ``ticket.sent`` resolving is not by itself proof that the item was
        # written: a retired ws or an earlier fatal error makes send_event
        # return False and dispatch carries on regardless. The frames bus may
        # only carry frames the provider actually received, so keep this turn's
        # own write result and publish on that.
        item_delivered = False

        async def _send_turn_event(event: Dict[str, Any]):
            nonlocal item_delivered
            written = await self.send_event(
                event,
                pre_send=_downgrade_if_visual_ownership_lost,
            )
            # The same sender carries response.create; only the item event says
            # anything about frames.
            if event is item_event:
                item_delivered = bool(written)
            return written

        ticket = await arbiter.enqueue(
            source="external_asr_multimodal",
            events_before_response=(item_event,),
            response_event=response_event,
            ack_expected=True,
            expected_item_id=item_id,
            expected_item_role="user",
            priority=0,
            admission_check=lambda: getattr(
                self,
                "_external_voice_turn_pause_id",
                None,
            ) in (None, stable_turn_id),
            pre_commit=_downgrade_if_visual_ownership_lost,
            # Third and final check: after arbiter passes to transport, send_event still awaits
            # _send_semaphore; ownership can change during that wait too, and payload serializes only after
            # semaphore acquisition. Use per-ticket event_sender introduced by main (#2837)
            # to carry the same degradation function into that critical section (see _send_turn_event above),
            # naturally reflecting its result in serialization.
            event_sender=_send_turn_event,
        )
        active_pause_id = getattr(self, "_external_voice_turn_pause_id", None)
        if active_pause_id == stable_turn_id:
            self._external_voice_turn_pause_id = None
        arbiter.allow_ticket_while_paused(ticket)
        if active_pause_id in (None, stable_turn_id):
            arbiter.resume_dispatch()
        try:
            await ticket.sent
        except asyncio.CancelledError:
            await arbiter.cancel_ticket(ticket)
            raise
        finally:
            if (
                active_pause_id is not None
                and active_pause_id != stable_turn_id
                and getattr(self, "_external_voice_turn_pause_id", None)
                == active_pause_id
            ):
                arbiter.pause_dispatch(active_pause_id)
        # Only here, and only on the path where ``ticket.sent`` resolved without
        # raising. Everything that could still have removed or rewritten a frame
        # has already run against this very dict -- both ownership downgrades
        # strip input_image parts in place, send_event's recompression rewrites
        # the survivors in place -- and nothing mutates it afterwards. So the
        # parts left in ``item_event`` are exactly the pictures the provider
        # got. Publishing any earlier would put frames on the bus that were
        # never sent, which is the one thing this bus must never do.
        #
        # Read the bytes out HERE, synchronously, and hand only that snapshot
        # to the background publish. Two separate reasons, and both have to
        # hold at once:
        #
        #   * The publish may not sit in this turn's return path.
        #     publish_session_event_threadsafe hands a cross-thread publish to
        #     the bridge's owner loop through an un-timed
        #     run_coroutine_threadsafe, so a stalled bridge would hold up a
        #     turn the provider has already taken. _transport's ambient-frame
        #     publish settled this same question the same way.
        #   * A task that read ``item_event`` later would read it after this
        #     turn let go of the loop, and whatever ran in between would
        #     silently become "what the provider got". The extraction is the
        #     part that must stay here; only the publish moves off.
        if item_delivered:
            self._schedule_turn_frame_publish(
                self._delivered_multimodal_frames(item_event),
                turn_id=stable_turn_id,
                source=frame_source,
            )
        return ticket

    def _delivered_multimodal_frames(
        self,
        item_event: Dict[str, Any],
    ) -> list[tuple[str, str]]:
        """Read a delivered turn's surviving frames out of the item it sent.

        The WebSocket half of the independent-ASR frame publish; Gemini's is
        ``_gemini_delivered_frames``. It matters on its own because these
        frames never pass through ``stream_image``: an external-ASR turn hands
        the sampled frames and the transcript to the provider as one item, and
        that mode also arms the raw-visual fence, so ``stream_image`` refuses
        every ambient frame -- between the two of them, these turns are the
        only frame channel a plugin can see at all.

        Reads the item that was sent rather than the caller's staged images on
        purpose. A turn that lost visual ownership had its ``input_image``
        parts stripped out of this dict, and an oversized one had them
        recompressed -- or the oldest ones dropped -- in place. The staged
        copies are therefore neither the right pictures nor the right set.

        Synchronous, and it has to stay that way. The caller runs this in the
        context that owns ``item_event``, where nothing else can be touching
        it, and hands the returned snapshot -- never the dict -- to the
        background publish. Deferring the read into that task would publish
        whatever a later turn happened to leave in the dict, and "only what the
        provider got" would stop being true with nothing going red.
        """

        item = item_event.get("item")
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            return []
        frames: list[tuple[str, str]] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "input_image":
                continue
            url = part.get("image_url")
            if not isinstance(url, str) or not url.startswith("data:"):
                continue
            header, _, payload = url.partition(",")
            if not payload:
                continue
            # "data:image/jpeg;base64" -> "image/jpeg". Read the mime back out
            # instead of assuming JPEG: recompression rewrites the payload but
            # keeps whatever prefix the part was built with.
            mime = header[len("data:"):].split(";", 1)[0].strip() or "image/jpeg"
            frames.append((mime, payload))
        return frames

    def _schedule_turn_frame_publish(
        self,
        frames: list[tuple[str, str]],
        *,
        turn_id: Optional[str],
        source: str,
    ) -> Optional[asyncio.Task]:
        """Hand one turn's already-extracted frames to the bus, off that turn.

        Fire-and-forget, the same shape ``_transport`` uses for ambient frames
        and for the same reason: the publish can end up on another loop
        (``publish_session_event_threadsafe`` forwards a cross-thread call
        through an un-timed ``run_coroutine_threadsafe``), and a stalled bridge
        must never be able to stall a turn the provider already accepted.
        Copying a frame is not a reason to slow down or fail a delivery that
        already succeeded, so the scheduling itself is guarded too.

        ``frames`` must already be a snapshot taken by the caller, and so
        must ``source``. Handing live state here -- the outgoing item, a
        staging list, ``_latest_image_source`` -- would let a later turn
        rewrite it before the task runs.

        Returns the task so callers that need to join it (tests, teardown) can;
        nothing on the turn path awaits it.
        """

        if not frames:
            return None
        try:
            return self._fire_frame_copy(
                self._publish_turn_frames_task(
                    frames,
                    turn_id=turn_id,
                    # Passed in, never read here. This runs AFTER the send
                    # await, and _latest_image_source is live session state
                    # that the next staged frame overwrites -- sampling at
                    # this point files the turn's pictures under whatever
                    # channel the session moved on to while it waited. The
                    # caller froze it with the turn; see submit_multimodal_turn.
                    source=source,
                )
            )
        except Exception as exc:
            logger.debug(
                "frames bus publish not scheduled for turn %s: %s", turn_id, exc
            )
            return None

    async def _publish_turn_frames_task(
        self,
        frames: list[tuple[str, str]],
        *,
        turn_id: Optional[str],
        source: str,
    ) -> None:
        """Run one turn's frame publish in the background. Never raises.

        The outer of the two layers. ``_publish_turn_frames`` already swallows
        a publisher that fails mid-loop, but the function-local bus import and
        everything else ahead of that loop sit outside it, and an escape here
        would surface as an unretrieved task exception instead of as the failed
        turn it must never become.
        """

        try:
            await self._publish_turn_frames(frames, turn_id=turn_id, source=source)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "frames bus publish failed for turn %s: %s", turn_id, exc
            )

    async def _publish_turn_frames(
        self,
        frames: list[tuple[str, str]],
        *,
        turn_id: Optional[str],
        source: str,
    ) -> int:
        """Hand one delivered turn's frames to the plugin bus. Never raises.

        Shared by both external-ASR routes -- the WebSocket item and the Gemini
        SDK turn -- so the two cannot drift into publishing different record
        shapes for what is, from a plugin's side, the same event.

        ``source`` is passed in rather than read off the session here: this
        runs in a background task, and every field of the record has to be the
        one that was true at delivery.
        """

        # Function-local import: agent_event_bus pulls in pyzmq, and the
        # realtime client sits on the startup import chain.
        from main_logic.agent_event_bus import (
            publish_provider_frame_observed_best_effort,
        )

        # The capture channel staging recorded ("screen" / "camera"). The
        # sampled frames of one utterance come off that same channel, and there
        # is no per-frame source to read here, so this is the honest label
        # rather than a category invented at this layer.
        published = 0
        for mime, image_b64 in frames:
            # No captured_at and no generation: the only per-frame clock this
            # layer can reach (_latest_image_captured_at) is time.monotonic(),
            # while the store indexes captured_at as a wall clock -- a monotonic
            # reading there would file every frame near the epoch. The forwarder
            # stamps arrival time instead, and turn_id is what ties these frames
            # to the turn they were sent with. The character name comes off the
            # session: frames/all is shared, so without it a plugin cannot tell
            # one character's pictures from another's.
            try:
                if await publish_provider_frame_observed_best_effort(
                    getattr(self, "lanlan_name", None),
                    image_base64=image_b64,
                    source=source,
                    turn_id=turn_id,
                    mime=mime,
                ):
                    published += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One failure means the bus itself is unreachable; retrying the
                # remaining frames would only repeat it.
                logger.debug(
                    "frames bus publish failed for turn %s: %s", turn_id, exc
                )
                break
        return published

    async def _cancel_gemini_proactive_submit(
        self,
        *,
        session_closing: bool = False,
        submit_task: Any = _GEMINI_PROACTIVE_TASK_UNSET,
    ) -> None:
        """Cancel and join the task parked in Gemini's proactive SDK send."""

        if submit_task is _GEMINI_PROACTIVE_TASK_UNSET:
            submit_task = getattr(self, "_gemini_proactive_submit_task", None)
        if submit_task is None:
            return
        if submit_task is asyncio.current_task():
            return
        if not submit_task.done():
            if session_closing:
                submit_task.cancel(_GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL)
            else:
                submit_task.cancel()
            await asyncio.gather(submit_task, return_exceptions=True)
        if getattr(self, "_gemini_proactive_submit_task", None) is submit_task:
            self._gemini_proactive_submit_task = None

    async def prepare_external_voice_turn(self, *, turn_id: str) -> bool:
        """Prepare one external ASR turn; report an in-place reconnect.

        Gemini quarantine may retire and replace the SDK connection on this
        same client instance.  The Core owner uses the returned flag to replace
        the receive task that captured the retired session.
        """

        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("external voice turn_id must not be empty")
        connection_generation = self._connection_generation
        async with self._ensure_turn_admission_lock():
            if self._is_gemini:
                self._start_gemini_external_submit_quarantine()
                await self._await_gemini_external_quarantine()
                await self._cancel_gemini_proactive_submit()
                proactive_outcome = getattr(
                    self,
                    "_gemini_proactive_outcome",
                    None,
                )
                proactive_quarantine = getattr(
                    self,
                    "_gemini_proactive_quarantine_task",
                    None,
                )
                if (
                    proactive_outcome is not None
                    and (
                        proactive_quarantine is None
                        or proactive_quarantine.done()
                    )
                ):
                    # SDK-send completion is not a Gemini lifecycle terminal.
                    # Until first content / turn_complete / interrupted arrives,
                    # the accepted proactive turn still owns this unscoped SDK
                    # session. Quarantine that outcome before admitting an
                    # external-ASR successor, just as the in-flight cancellation
                    # path does.
                    proactive_quarantine = self._fire_task(
                        self._interrupt_and_quarantine_gemini_proactive_outcome(
                            proactive_outcome[0],
                            error_msg=(
                                "Gemini proactive turn was superseded by "
                                "external voice input"
                            ),
                        )
                    )
                    self._gemini_proactive_quarantine_task = proactive_quarantine
                await self._await_gemini_proactive_quarantine()
            # Warning: announce new user turn only **after** Gemini quarantine above.
            # note_user_turn_started() advances _tool_scope_generation, while
            # _interrupt_and_quarantine_gemini_proactive_outcome's first gate
            # scope_still_ours() compares recorded owner scope against it. Advancing first
            # always mismatches, making quarantine take retire_without_touching_the_live_turn every time,
            # never sending client_content interruption. The proactive turn accepted by SDK and still
            # generating is thus never interrupted; external ASR overlays another turn on the same unscoped SDK
            # session, interleaving their audio/text, exactly what this fence prevents.
            # Quarantine targets the **previous proactive turn**, so finish it under that turn's
            # scope before this turn truly starts.
            self.note_user_turn_started()
            preparation_arbiter = None
            preparation_token = None
            try:
                if not self._is_gemini:
                    arbiter = self._ensure_response_arbiter()
                    self._external_voice_turn_pause_id = stable_turn_id
                    preparation_token = arbiter.begin_turn_preparation(stable_turn_id)
                    preparation_arbiter = arbiter
                    # Only cancel something the provider is already acting on.
                    # Independent ASR cuts one spoken sentence into several
                    # turns, so this prepare routinely lands while the
                    # *previous* turn's reply is still parked before its first
                    # send. Cancelling that is not barge-in: nothing is being
                    # said over the user, and the turn it discards is a
                    # complete sentence that then never gets answered at all.
                    # Leave it queued -- the lane is serial, and this turn's
                    # own ticket is priority 0.
                    if arbiter.has_live_response or (
                        arbiter.current_source is not None
                        and arbiter.current_source != "external_asr"
                    ):
                        await arbiter.cancel_current(reason="external_asr_prepare")
                await self.handle_interruption()
            except BaseException:
                self.abandon_external_voice_turn(stable_turn_id)
                raise
            finally:
                if preparation_arbiter is not None:
                    preparation_arbiter.end_turn_preparation(preparation_token)
        return self._connection_generation != connection_generation

    def _consume_cancelled_terminal(self) -> bool:
        """Take the terminal owed to a response ``handle_interruption`` cancelled.

        One-shot: the first terminal after the cancellation is that response's,
        no matter what has been minted since. Returns whether this terminal was
        the owed one.

        Bounded: a debt past its deadline is spent but not honoured. The
        cancelled response owes its terminal within one provider round trip, so
        a debt that outlives that window is stale -- and honouring it would skip
        the settlement of a turn that is not the cancelled one, leaving an
        external token nobody settles and a session that reads busy.

        The clock does not run until the interrupt reaches the provider. Gemini
        is interrupted by the successor's content, so until that send lands
        nothing else has been submitted and the first terminal can only be the
        cancelled turn's -- expiring in that window would hand its terminal to a
        successor that does not exist yet. The send both re-stamps the deadline
        and lowers the flag, but it does so after its await returns, and the
        receive loop can deliver that terminal inside the gap.
        """
        if not getattr(self, "_gemini_cancelled_terminal_pending", False):
            return False
        self._gemini_cancelled_terminal_pending = False
        deadline = getattr(self, "_gemini_cancelled_terminal_deadline", None)
        awaiting_delivery = getattr(
            self, "_gemini_cancelled_terminal_awaiting_delivery", False
        )
        self._gemini_cancelled_terminal_deadline = None
        self._gemini_cancelled_terminal_awaiting_delivery = False
        self._gemini_cancelled_terminal_id = None
        if (
            deadline is not None
            and not awaiting_delivery
            and time.monotonic() >= deadline
        ):
            logger.debug(
                "Gemini: cancelled-terminal debt expired unconsumed; this "
                "terminal settles the current turn instead"
            )
            return False
        return True

    def _settle_gemini_external_turn(self, token: object | None = None) -> None:
        """Release accepted external-ASR ownership at its terminal edge."""

        current = getattr(self, "_gemini_external_outcome_token", None)
        if token is not None and current is not token:
            return
        self._gemini_external_outcome_token = None

    def _start_gemini_external_submit_quarantine(
        self,
        submit_task: Optional[asyncio.Task] = None,
    ) -> None:
        """Retire a Gemini session whose external turn send was cancelled."""

        if submit_task is None:
            submit_task = getattr(self, "_gemini_external_submit_task", None)
        outcome_token = getattr(self, "_gemini_external_outcome_token", None)
        if (
            (submit_task is None or submit_task.done())
            and outcome_token is None
        ):
            return
        if submit_task is asyncio.current_task():
            return
        quarantine_task = getattr(
            self,
            "_gemini_external_quarantine_task",
            None,
        )
        if quarantine_task is not None and not quarantine_task.done():
            return
        self._gemini_external_quarantine_task = self._fire_task(
            self._quarantine_gemini_external_submit(submit_task, outcome_token)
        )

    async def _quarantine_gemini_external_submit(
        self,
        submit_task: Optional[asyncio.Task],
        outcome_token: object | None,
    ) -> None:
        """Join a cancelled SDK send, then close the ambiguous connection."""

        submit_was_inflight = submit_task is not None and not submit_task.done()
        if submit_was_inflight:
            submit_task.cancel()
            await asyncio.gather(submit_task, return_exceptions=True)
        if (
            submit_task is not None
            and getattr(self, "_gemini_external_submit_task", None) is submit_task
        ):
            self._gemini_external_submit_task = None
        if (
            not submit_was_inflight
            and outcome_token is not None
            and getattr(self, "_gemini_external_outcome_token", None)
            is not outcome_token
        ):
            # Its terminal event won the race with quarantine startup, so the
            # session no longer contains an ambiguous accepted turn.
            return
        self._settle_gemini_external_turn(outcome_token)
        # Cancellation only ends our await; Gemini may already have accepted
        # the turn. The connection is the smallest scope that can prove no late
        # transcript/response from that turn can cross into its successor.
        self._fatal_error_occurred = True
        await self._close_gemini()

    async def _await_gemini_external_quarantine(self) -> None:
        """Join external-submit quarantine and reconnect before a new turn."""

        quarantine_task = getattr(
            self,
            "_gemini_external_quarantine_task",
            None,
        )
        if quarantine_task is not None and quarantine_task is not asyncio.current_task():
            try:
                await asyncio.shield(quarantine_task)
            except Exception:
                # The retained SDK owner must close successfully before we
                # reconnect; retry it rather than replaying the old exception.
                await self._close_gemini()
            finally:
                if (quarantine_task.done() and getattr(self, "_gemini_external_quarantine_task", None) is quarantine_task):
                    self._gemini_external_quarantine_task = None
        if getattr(self, "_fatal_error_occurred", False) and getattr(self, "_gemini_session", None) is not None:
            await self._close_gemini()
        if getattr(self, "_gemini_session", None) is None:
            instructions = str(getattr(self, "instructions", "") or "")
            if instructions:
                await self.connect(
                    instructions,
                    native_audio=getattr(self, "_native_audio", True),
                )

    async def _await_gemini_proactive_quarantine(self) -> None:
        """Join stale Gemini proactive quarantine before opening a user turn."""

        quarantine_task = getattr(self, "_gemini_proactive_quarantine_task", None)
        if (
            quarantine_task is None
            or quarantine_task is asyncio.current_task()
        ):
            return
        current_task = asyncio.current_task()
        cancelling_before = (
            current_task.cancelling() if current_task is not None else 0
        )
        try:
            await asyncio.shield(quarantine_task)
        except asyncio.CancelledError:
            if (
                current_task is None
                or current_task.cancelling() > cancelling_before
                or not quarantine_task.cancelled()
            ):
                raise
        finally:
            if (
                quarantine_task.done()
                and getattr(self, "_gemini_proactive_quarantine_task", None)
                is quarantine_task
            ):
                self._gemini_proactive_quarantine_task = None

        # A quarantine with no terminal lifecycle retires the old Gemini
        # session. Reconnect before admitting the user's turn so its transcript
        # cannot race the retired SDK context.
        if getattr(self, "_gemini_session", None) is None:
            instructions = str(getattr(self, "instructions", "") or "")
            if instructions:
                await self.connect(
                    instructions,
                    native_audio=getattr(self, "_native_audio", True),
                )

    def abandon_external_voice_turn(self, turn_id: str | None = None) -> None:
        """Release an external-ASR dispatch pause, optionally by turn key."""

        if self._is_gemini:
            return
        current_turn_id = getattr(self, "_external_voice_turn_pause_id", None)
        if turn_id is not None and str(turn_id).strip() != current_turn_id:
            return
        self._external_voice_turn_pause_id = None
        arbiter = getattr(self, "_response_arbiter", None)
        if arbiter is None:
            if current_turn_id is None:
                return
            arbiter = self._ensure_response_arbiter()
        arbiter.resume_dispatch()

    async def _submit_external_gemini_turn(
        self,
        text: str,
        *,
        images_bytes: tuple[bytes, ...] = (),
        visual_still_owned=None,
        turn_id: Optional[str] = None,
        source: str | None = None,
    ) -> None:
        """Submit one external-ASR turn through the owned Gemini lifecycle."""

        # Same rule as submit_multimodal_turn: fix the channel label before
        # this function's own first await (_await_gemini_external_quarantine).
        # The text-only entry carries neither a source nor frames, so the
        # publish below never fires for it; the fallback only keeps that path
        # readable.
        frame_source = str(
            source or getattr(self, "_latest_image_source", "") or "unknown"
        )
        submit_task = asyncio.current_task()
        # Previous external turn may still await its terminal event: overlapping utterance B's prepare can run
        # **before** A's SDK send, when no token exists to quarantine, so prepare's
        # quarantine does nothing. Overwriting A's live token here would leave both turns active,
        # interleaving responses or letting A's terminal also clear B's ownership.
        #
        # Reuse prepare's quarantine criterion: settle the old token and retire this connection.
        # This file already concludes that connection is the smallest scope proving old-turn late content cannot leak into
        # successors (see _quarantine_gemini_external_submit).
        if getattr(self, "_gemini_external_outcome_token", None) is not None:
            self._start_gemini_external_submit_quarantine()
            await self._await_gemini_external_quarantine()
        # An external-ASR turn **is** a new user utterance, but bypasses
        # _transport's _user_recent_activity_time update sites (audio never reaches provider).
        # Without updating, _is_new_turn classifies its first content as late continuation: _interrupted suppresses
        # content, debt remains, then its own terminal consumes debt and skips settlement,
        # leaving token forever and session pinned busy, silencing proactive speech permanently.
        # Final visual-ownership check before sending. The preceding
        # _await_gemini_external_quarantine() genuinely yields (waits while previous token remains
        # until quarantine finishes), letting successor turn invalidate this record during the wait.
        # Same criterion as image budgeting: dropping frames degrades to text, still sends speech.
        if (
            images_bytes
            and visual_still_owned is not None
            and not visual_still_owned()
        ):
            logger.info(
                "Gemini external turn lost visual ownership before the SDK "
                "send; submitting text-only"
            )
            images_bytes = ()
        self._user_recent_activity_time = time.time()
        outcome_token = object()
        self._gemini_external_submit_task = submit_task
        self._gemini_external_outcome_token = outcome_token
        accepted = False
        quarantined = False
        try:
            await self._gemini_send_user_turn(
                text,
                images_bytes=images_bytes,
            )
            accepted = True
        except asyncio.CancelledError:
            # Cancellation ends only **our await**; Gemini may already have accepted the turn:
            # TranscriptDispatcher.invalidate_all() propagates cancellation through worker to
            # here after send already reached SDK. Settling token as unsent falsely claims
            # no turn remains in flight, so next prepare skips quarantine and late transcript/response
            # leaks into the successor.
            #
            # Preserve token and arm quarantine in place; it joins this submit task,
            # settles token, and retires connection, so the busy flag cannot remain stuck permanently
            # (is_active_response() reads this token). Symmetric with proactive path's
            # CancelledError handling and criterion.
            #
            # Do not use _start_gemini_external_submit_quarantine: its self-protection
            # 'submit_task is asyncio.current_task() → return' makes calls from here
            # no-op. Fire quarantine body directly and let it join us.
            quarantined = True
            _existing = getattr(self, "_gemini_external_quarantine_task", None)
            if _existing is None or _existing.done():
                self._gemini_external_quarantine_task = self._fire_task(
                    self._quarantine_gemini_external_submit(
                        submit_task,
                        outcome_token,
                    )
                )
            raise
        finally:
            if getattr(self, "_gemini_external_submit_task", None) is submit_task:
                self._gemini_external_submit_task = None
            if not accepted and not quarantined:
                # Settle immediately only on synchronous send failure (direct provider rejection): turn was truly not accepted.
                self._settle_gemini_external_turn(outcome_token)
        # Gemini half of fourth delivery site, **after** try/finally: CancelledError
        # re-raises above (provider may have accepted, but unverifiable),
        # while synchronous failure leaves accepted false; neither unsent case reaches here.
        #
        # This path needs coverage for correctness: independent ASR arms a raw-visual
        # fence, making stream_image reject every ambient frame. This turn becomes the session's only
        # visual channel. Without it, Gemini independent-ASR users send visuals to provider with every utterance,
        # while plugin frames bus remains empty.
        #
        # Encoded here and published in the background, the same split the
        # WebSocket half makes: this turn's return must not wait on a bus hop
        # that may cross loops with no timeout, and the bytes that reach the
        # bus must be the ones read in the context that owned them.
        if accepted and images_bytes:
            self._schedule_turn_frame_publish(
                self._gemini_delivered_frames(images_bytes),
                source=frame_source,
                # Empty is not an identity: the text-only Gemini route reaches
                # _submit_external_gemini_turn without one, and a blank turn_id
                # on the record would still read as "these frames belong
                # together".
                turn_id=str(turn_id or "") or None,
            )

    def _gemini_delivered_frames(
        self,
        images_bytes: tuple[bytes, ...],
    ) -> list[tuple[str, str]]:
        """Encode a delivered Gemini external-ASR turn's frames for the bus.

        Reads ``images_bytes`` rather than an outgoing event because this route
        never builds one: the frames go to the SDK as raw bytes, so this tuple
        IS what was handed over -- after the budget ladder ran, and after the
        ownership recheck emptied it on a turn that lost its frames.
        """

        return [
            # The same constant ``_gemini_send_user_turn`` sends under, not a
            # second literal: a changed format would otherwise mislabel every
            # record on the bus with nothing going red.
            (GEMINI_TURN_IMAGE_MIME, base64.b64encode(image).decode("ascii"))
            for image in images_bytes
            if image
        ]

    async def submit_external_voice_turn(self, text: str, *, turn_id: str) -> None:
        """Submit external ASR text through the Provider-appropriate path."""

        if self._is_gemini:
            clean = str(text or "").strip()
            if not clean:
                raise ValueError("external ASR turn must not be empty")
            if len(clean) > 8_000:
                raise ValueError("external ASR turn exceeds the 8000 character budget")
            stable_turn_id = str(turn_id or "").strip()
            if not stable_turn_id:
                raise ValueError("external voice turn_id must not be empty")
            await self._submit_external_gemini_turn(clean)
            return
        await self.submit_external_text_turn(text, turn_id=turn_id)

    def is_active_response(self) -> bool:
        """Return True iff the realtime session is currently producing a response.

        Tracks ``response.created`` → ``response.done`` (OpenAI / GLM / Step /
        free / GPT) and Gemini's ``turn_complete`` lifecycle via the shared
        ``_is_responding`` flag, so callers can gate "manual inject + request
        response" against the realtime API's "one active response at a time"
        constraint.
        """
        # Between Gemini external-ASR SDK send return and first model-content event,
        # _is_responding remains False and arbiter idle, but provider already accepted the turn
        # (_gemini_external_outcome_token remains live until turn_complete
        # / interrupted). Ignoring it during this interval lets queued proactive speech pass all busy checks
        # and inject another unmanaged Gemini turn, whose terminal events interfere with this turn's.
        return bool(
            self._is_responding
            or getattr(self, "_gemini_external_outcome_token", None) is not None
            or self._ensure_response_arbiter().is_busy
        )

    async def inject_text_and_request_response(
        self,
        text: str,
        *,
        events_before_text: tuple[Dict[str, Any], ...] = (),
        on_rejected: Optional[Callable[[str], None]] = None,
        on_completed: Optional[Callable[[], None]] = None,
    ) -> Optional[ResponseTicket]:
        """Inject a user-role text item and explicitly trigger a response.

        Used by the voice-mode proactive path (agent task callbacks /
        plugin push_message ai_behavior="respond") to surface a rendered
        instruction to the realtime model and have it speak the result
        immediately — without waiting for the next user turn (which is what
        the hot-swap pending_extra_replies channel does).

        Caller is responsible for gating against active-response races
        (see ``is_active_response``) — the realtime API only allows one
        in-flight response at a time and will reject a second
        ``response.create`` with ``response_already_active``.

        Server-side rejection (e.g. VAD races in between the caller's gate
        check and our ``response.create``) does not raise here because the
        server delivers it asynchronously via an ``error`` event. Pass
        ``on_rejected=cb(error_msg)`` to receive that rejection — the
        message loop will invoke it when ``error.event_id`` matches the
        client-side id we stamp on ``response.create``. The caller can use
        it to put the optimistically-pruned cb back in the queue.
        ``on_completed`` is fired only when this request's arbiter ticket
        reaches ``response.done``. It is deliberately not tied to the next
        global terminal event: another queued or active response may finish
        before this request is even dispatched.

        Returns the exact arbiter ticket for WebSocket providers so callers
        can target cancellation to this request. Gemini and empty-text paths
        return ``None``.

        Provider dispatch (all realtime providers supported — symmetric with
        ``create_response``):
          - **OpenAI / GLM / Step / free / GPT / Qwen / Grok**:
            ``conversation.item.create`` (role=user, input_text) +
            ``response.create``. Uses user role rather than system to avoid
            permanent drift of session instruction context — the rendered
            body already self-identifies as a system notification via its
            localized header wrapper. (Qwen included: the
            Aliyun doc claiming function_call_output-only is stale for
            qwen3.5-omni-flash-realtime; verified live.)
          - **Gemini Live**: ``send_client_content(turn_complete=True)`` via
            the shared ``_gemini_send_user_turn`` helper — Gemini's idiomatic
            inject+trigger. Synchronous send failures raise; successful sends
            remain pending until ``turn_complete`` or ``interrupted``.
        """
        if self._fatal_error_occurred:
            raise RuntimeError("realtime session has fatal_error_occurred set")
        if not text or not text.strip():
            return

        if self._is_gemini:
            # Symmetric with create_response → _create_response_gemini.
            # send_client_content(turn_complete=True) injects a user turn and
            # triggers a response. Delivery remains pending until Gemini's
            # turn_complete/interrupted lifecycle settles this exact inject;
            # SDK-send success alone is not response completion.
            if self._gemini_session is None:
                raise RuntimeError("Gemini session not available for proactive inject")
            gemini_text_parts: list[str] = []
            for event in events_before_text:
                if event.get("type") != "conversation.item.create":
                    raise ValueError("Gemini proactive prefix must be a text item")
                item = event.get("item")
                if not isinstance(item, dict) or item.get("role") != "user":
                    raise ValueError("Gemini proactive prefix must use user role")
                content = item.get("content")
                if not isinstance(content, list):
                    raise ValueError("Gemini proactive prefix content must be a list")
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "input_text":
                        raise ValueError("Gemini proactive prefix must contain input_text")
                    prefix_text = str(part.get("text") or "").strip()
                    if prefix_text:
                        gemini_text_parts.append(prefix_text)
            gemini_text_parts.append(text.strip())
            gemini_text = "\n".join(gemini_text_parts)
            proactive_session = self._gemini_session
            proactive_scope = getattr(self, "_tool_scope_generation", 0)
            await self._settle_tools_before_gemini_proactive()
            if self._gemini_session is not proactive_session:
                # The settle is an await: a teardown OR a replacement session
                # can land inside it, and this inject belongs to neither.
                raise RuntimeError("Gemini session not available for proactive inject")
            if proactive_scope != getattr(self, "_tool_scope_generation", 0):
                # A real user turn began while we waited. Sending now would put
                # this notification inside their turn, and Gemini treats client
                # content as an interruption of the current generation -- the
                # exact failure the wait exists to avoid, just aimed at the user
                # instead of at a tool. The caller keeps the callback queued and
                # retries it on the next idle hook, so nothing is lost.
                #
                # Deliberately NOT also gated on is_active_response(): waiting
                # for tools makes a model response DURING the wait the normal
                # case (the tool returned and generation continued), and
                # rejecting on that would silently drop proactive messages on
                # the healthy path. Guarding an active response is documented
                # as the caller's job.
                raise RuntimeError(
                    "Gemini proactive inject was superseded by a new user turn"
                )
            outcome_token = f"gemini_inject_{uuid.uuid4().hex}"
            # getattr, not attribute access: several focused tests build the
            # client via __new__ and this read is now unconditional, where the
            # owner tuple below only ran when a callback was supplied.
            proactive_generation = getattr(self, "_connection_generation", 0)
            if on_rejected is not None or on_completed is not None:
                if getattr(self, "_gemini_proactive_outcome", None) is not None:
                    raise RuntimeError("another Gemini proactive inject is pending")
                self._gemini_proactive_outcome = (
                    outcome_token,
                    on_rejected,
                    on_completed,
                )
                self._gemini_proactive_outcome_owner = (
                    proactive_generation,
                    proactive_session,
                    outcome_token,
                    self._gemini_context_manager,
                    # The connection and the session both survive a new user
                    # turn, so neither can tell one apart. Only the tool scope
                    # moves -- and this inject stops owning the Gemini
                    # generation the moment it does.
                    proactive_scope,
                )
                self._proactive_inject_outcome_token = outcome_token
                self._proactive_inject_awaiting_outcome = True
            submit_task = asyncio.current_task()
            existing_submit_task = getattr(
                self,
                "_gemini_proactive_submit_task",
                None,
            )
            existing_submit_session = getattr(
                self,
                "_gemini_proactive_submit_session",
                None,
            )
            if (
                existing_submit_task is not None
                and existing_submit_task is not submit_task
                and not existing_submit_task.done()
                # Narrow by session, same criterion as outcome owner above. Sends interleave only on
                # **the same** SDK session; one stuck on a retired session must not block
                # replacement connection's injection. _on_connection_attached retires its earlier
                # outcome for exactly this reason; blocking here still forces replacement to wait for 60s
                # expiry or quarantine completion before proactive speech. Teardown
                # (_cancel_gemini_submit_tasks) cancels the retired send without leaks.
                and existing_submit_session is proactive_session
            ):
                outcome = getattr(self, "_gemini_proactive_outcome", None)
                if outcome is not None and outcome[0] == outcome_token:
                    self._settle_gemini_proactive_inject(notify=False)
                raise RuntimeError("another Gemini proactive SDK send is pending")
            self._gemini_proactive_submit_task = submit_task
            self._gemini_proactive_submit_session = proactive_session
            try:
                await self._gemini_send_user_turn(
                    gemini_text,
                    starts_user_turn=False,
                )
            except asyncio.CancelledError as exc:
                outcome = getattr(self, "_gemini_proactive_outcome", None)
                if outcome is not None and outcome[0] == outcome_token:
                    if (
                        exc.args
                        and exc.args[0]
                        == _GEMINI_PROACTIVE_SESSION_CLOSE_CANCEL
                    ):
                        # The owning SDK session is being retired immediately;
                        # no delayed quarantine may survive this close and later
                        # seize a replacement connection.
                        self._settle_gemini_proactive_inject(notify=False)
                    else:
                        # Cancellation can arrive after the SDK accepted the
                        # unscoped turn but before its await resumes. Suppress
                        # callbacks and quarantine until a terminal (or session
                        # retirement) makes retry correlation safe again.
                        self._gemini_proactive_outcome = (
                            outcome_token,
                            None,
                            None,
                        )
                        quarantine_task = self._fire_task(
                            self._interrupt_and_quarantine_gemini_proactive_outcome(
                                outcome_token,
                                error_msg="Gemini proactive SDK send was cancelled",
                            )
                        )
                        self._gemini_proactive_quarantine_task = quarantine_task
                raise
            except Exception:
                # Owner-scoped, like the CancelledError branch above. This send
                # can fail AFTER a replacement attached and registered its own
                # outcome, and an unconditional settle would clear the
                # SUCCESSOR's -- its caller would then never see a completion
                # or rejection and would wait out its full timeout.
                self._settle_gemini_proactive_inject(
                    notify=False,
                    expected_connection_generation=proactive_generation,
                    expected_provider_session=proactive_session,
                    expected_outcome_token=outcome_token,
                )
                raise
            finally:
                if (
                    getattr(self, "_gemini_proactive_submit_task", None)
                    is submit_task
                ):
                    self._gemini_proactive_submit_task = None
                    self._gemini_proactive_submit_session = None
            if on_rejected is not None or on_completed is not None:
                self._fire_task(
                    self._expire_gemini_proactive_outcome(outcome_token, 60.0)
                )
            return
        # Qwen follows the WebSocket path documented above. Its older
        # function_call_output-only documentation is stale; do not restore a
        # Qwen exclusion without rechecking the live API.
        if self.ws is None:
            raise RuntimeError("realtime websocket is not connected")

        # Role choice: ``user`` (not ``system``).
        # OpenAI Realtime persists conversation items as part of session
        # history. ``role="system"`` items are treated as high-priority
        # instructions that influence every subsequent turn — accumulating
        # several proactive callbacks under system role causes prompt
        # drift (model starts repeating meta-behavior or interpreting
        # stale callback text as standing orders for unrelated turns).
        # ``role="user"`` keeps the inject in dialog-weight context, and
        # ``_build_callback_instruction`` already wraps the body in a
        # ``======[系统通知] ...======`` header that makes the model
        # treat it as a one-shot system notification rather than user
        # speech. Matches the existing ``create_response`` precedent.
        # Stamp stable client event_ids on BOTH events so the server's
        # ``error.event_id`` can be matched back to this specific request
        # whichever event it rejects (the item itself, or the
        # ``response.create`` — e.g. ``response_already_active`` from a VAD
        # race). ``send_event()`` would otherwise overwrite a missing
        # event_id with its own timestamp-based string — fine for routing but
        # useless for rejection matching since the caller has no view of it.
        # A single ``_reject_once`` wrapper fires ``on_rejected`` at most once
        # even if both event_ids somehow error, and unregisters both handlers.
        item_event_id = f"event_inject_item_{uuid.uuid4().hex}"
        create_event_id = f"event_inject_resp_{uuid.uuid4().hex}"
        outcome_token = create_event_id
        item_id = new_client_item_id()
        expected_item_id = item_id

        def _close_outcome_window() -> None:
            if (
                getattr(self, "_proactive_inject_outcome_token", None)
                == outcome_token
            ):
                self._proactive_inject_outcome_token = None
                self._proactive_inject_awaiting_outcome = False

        if on_rejected is not None or on_completed is not None:
            _fired = False

            def _remove_outcome_handlers() -> None:
                self._inject_rejection_handlers.pop(item_event_id, None)
                self._inject_rejection_handlers.pop(create_event_id, None)

            def _reject_once(error_msg: str) -> None:
                nonlocal _fired
                # Unregister both regardless so neither lingers.
                _remove_outcome_handlers()
                if _fired:
                    return
                _fired = True
                _close_outcome_window()
                if on_rejected is not None:
                    on_rejected(error_msg)

            def _complete_once() -> None:
                nonlocal _fired
                _remove_outcome_handlers()
                if _fired:
                    return
                _fired = True
                _close_outcome_window()
                if on_completed is not None:
                    on_completed()

            if on_rejected is not None:
                self._inject_rejection_handlers[item_event_id] = _reject_once
                self._inject_rejection_handlers[create_event_id] = _reject_once
            # The realtime API echoes our event_id on ``error`` but not on
            # ``response.created``. Completion is therefore observed from the
            # exact arbiter ticket below, never by sweeping callbacks on an
            # unrelated global ``response.done``.
            self._fire_task(
                self._expire_inject_rejection_handler(
                    item_event_id,
                    60.0,
                    outcome_token,
                )
            )
            self._fire_task(
                self._expire_inject_rejection_handler(
                    create_event_id,
                    60.0,
                    outcome_token,
                )
            )
            # Open the no-id content-fallback window for THIS inject. Closed
            # when its own arbiter ticket completes or is rejected.
            self._proactive_inject_outcome_token = outcome_token
            self._proactive_inject_awaiting_outcome = True

        item_event: Dict[str, Any] = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                    }
                ],
            },
        }
        if expected_item_id is not None:
            item_event["item"]["id"] = item_id
        item_event["event_id"] = item_event_id

        # send_event() silently returns when ws drops to None or fatal flag
        # flips mid-flight (it does not raise). Without the post-send checks,
        # a connection lost in the brief await window between the entry guard
        # and the actual send would look like a successful inject — caller
        # would prune the cb but nothing reached the model. Re-check after
        # each send and raise so the caller's exception branch keeps the cb
        # for retry. On any synchronous send failure, drop both rejection
        # handlers so the caller's ``except`` path is the single source of
        # truth and a late error event can't double-fire the re-queue.
        arbiter = self._ensure_response_arbiter()
        ticket: Optional[ResponseTicket] = None
        try:
            create_event: Dict[str, Any] = {"type": "response.create"}
            create_event["event_id"] = create_event_id
            ticket = await arbiter.enqueue(
                source="proactive",
                events_before_response=(*events_before_text, item_event),
                response_event=create_event,
                ack_expected=True,
                expected_item_id=expected_item_id,
                expected_item_role="user",
                priority=20,
            )
            await asyncio.shield(ticket.sent)
            if self._fatal_error_occurred or self.ws is None:
                raise RuntimeError(
                    "realtime connection lost after proactive response.create"
                )

            if on_rejected is not None or on_completed is not None:
                async def _observe_ticket_outcome() -> None:
                    try:
                        await asyncio.shield(ticket.done)
                    except Exception as exc:
                        _reject_once(str(exc))
                    else:
                        _complete_once()

                self._fire_task(_observe_ticket_outcome())
        except asyncio.CancelledError:
            ticket_completed = False
            if ticket is not None:
                try:
                    cancellation_requested = await asyncio.shield(
                        arbiter.cancel_ticket(ticket)
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        "proactive inject cancellation cleanup failed: %s",
                        cancel_exc,
                    )
                else:
                    if not cancellation_requested:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(ticket.done),
                                timeout=(
                                    _PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS
                                ),
                            )
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                        except Exception:
                            pass
                        else:
                            ticket_completed = True
                            if (
                                on_rejected is not None
                                or on_completed is not None
                            ):
                                _complete_once()
            if not ticket_completed:
                self._inject_rejection_handlers.pop(item_event_id, None)
                self._inject_rejection_handlers.pop(create_event_id, None)
                _close_outcome_window()
            raise
        except Exception:
            self._inject_rejection_handlers.pop(item_event_id, None)
            self._inject_rejection_handlers.pop(create_event_id, None)
            _close_outcome_window()
            raise
        return ticket

    async def _settle_tools_before_gemini_proactive(self) -> bool:
        """Let in-flight tool work finish first, within a bounded budget.

        The raw providers get this ordering for free: their proactive inject
        enqueues at priority 20 and a tool result at priority 5, so the
        arbiter always sends the result first and the two never race. Gemini
        has no arbiter on this path -- the inject is a direct
        ``send_client_content`` -- and Gemini treats client content as an
        interruption of the current generation, which is exactly how
        ``cancel_response`` implements barge-in for it. An unhindered inject
        can therefore abandon a function call whose side effect has already
        run, leaving the provider without its output.

        ``starts_user_turn=False`` does not cover this. That keeps the LOCAL
        tool scope alive so a result is not discarded on arrival; it cannot
        stop the provider from dropping the call.

        Bounded, and that is the whole design. The tool-turn gate this branch
        removed had no TTL and could block proactive messages forever; here a
        tool that outlives the budget loses its ordering guarantee, never the
        message. Returns whether everything settled -- for the log only.

        What happens to a call that outlives the budget is
        ``_abandon_tool_calls_for_gemini_proactive``: it is answered with an
        abandoned ``function_call_output`` and retired, so the conversation
        never keeps a ``function_call`` nobody replied to and a result landing
        afterwards is dropped instead of being injected into the proactive
        turn.
        """

        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS
        poll_interval = self._TOOL_TASK_CANCEL_TIMEOUT_S
        while True:
            # Recomputed every round, not snapshotted once -- same shape as
            # the batch collector's own wait loop, and for the same reason. A
            # call the provider cancelled cannot answer any more: its result
            # is filtered out on arrival and the collector has already stopped
            # waiting for it. But the task object survives until the handler
            # exits, which a handler that swallows CancelledError never does,
            # so waiting for one spends this budget on nothing. Cancellations
            # arrive asynchronously, so a snapshot taken before the wait goes
            # stale the moment one lands inside it.
            retired = self._retired_tool_tasks()
            pending = tuple(
                task
                for task in getattr(self, "_tool_tasks", ())
                if not task.done() and task is not current and task not in retired
            )
            if not pending:
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "Gemini proactive inject proceeding with %d tool call(s) "
                    "still running after %.1fs; answering them as abandoned",
                    len(pending),
                    _GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS,
                )
                await self._abandon_tool_calls_for_gemini_proactive(pending)
                return False
            await asyncio.wait(
                pending,
                timeout=min(poll_interval, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            poll_interval = min(
                poll_interval * 2, self._TOOL_BATCH_POLL_CEILING_S
            )

    async def _abandon_tool_calls_for_gemini_proactive(self, tasks) -> None:
        """Answer the calls the settle budget gave up on, before injecting.

        The inject that follows is an interruption -- that is how
        ``cancel_response`` implements Gemini barge-in -- so the generation
        that issued these calls is about to be discarded. Sent BEFORE the
        inject, while that generation is still the current one, so the
        outputs bind to the calls they answer rather than arriving inside the
        proactive turn.

        Owner-scoped end to end: ``_retire_tool_tasks_as_abandoned`` groups
        the replies by the owner each call was captured with, and
        ``_send_tool_result_gemini`` re-checks
        ``_tool_task_owner_is_current`` before it writes -- an abandoned-call
        reply must not be the one thing that crosses into a replacement
        connection.
        """

        for owner, results in self._retire_tool_tasks_as_abandoned(tasks):
            if not results:
                continue
            # BOUNDED, and shielded so the bound only releases US. The settle
            # budget promises that the proactive message always gets out, and
            # this write goes to the same session that just proved it can be
            # slow: awaiting it outright would let a wedged session restore
            # the unbounded tool-turn gate #2837 removed. Shielded rather than
            # cancelled because the reply is still worth delivering late -- it
            # just no longer gets to hold the notification.
            send = self._create_tool_task(
                self._send_tool_result_gemini(
                    results,
                    provider_session=owner.provider_session,
                    owner=owner,
                )
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(send),
                    timeout=self._TOOL_TASK_CANCEL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                # Tracked as a tool task, so it is still awaited on close and
                # its failure is still logged -- it simply no longer holds the
                # notification.
                logger.warning(
                    "Gemini abandoned-call reply is still in flight after "
                    "%.1fs; proceeding with the proactive inject",
                    self._TOOL_TASK_CANCEL_TIMEOUT_S,
                )

    def _settle_gemini_proactive_inject(
        self,
        *,
        error_msg: Optional[str] = None,
        notify: bool = True,
        expected_connection_generation: int | None = None,
        expected_provider_session: Any = None,
        expected_outcome_token: str | None = None,
    ) -> None:
        """Settle the one pending Gemini proactive turn at its lifecycle edge."""
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is None:
            return
        token, on_rejected, on_completed = outcome
        if expected_connection_generation is not None:
            if token != expected_outcome_token:
                return
            expected_owner = (
                expected_connection_generation,
                expected_provider_session,
                expected_outcome_token,
            )
            owner = getattr(self, "_gemini_proactive_outcome_owner", None)
            if owner is None or owner[:3] != expected_owner:
                return
        self._gemini_proactive_outcome = None
        quarantine_task = getattr(self, "_gemini_proactive_quarantine_task", None)
        if (
            quarantine_task is not None
            and quarantine_task is not asyncio.current_task()
            and not quarantine_task.done()
        ):
            quarantine_task.cancel()
        if (
            quarantine_task is not None
            and quarantine_task is not asyncio.current_task()
            and quarantine_task.done()
        ):
            self._gemini_proactive_quarantine_task = None
        self._gemini_proactive_outcome_owner = None
        if getattr(self, "_proactive_inject_outcome_token", None) == token:
            self._proactive_inject_outcome_token = None
            self._proactive_inject_awaiting_outcome = False
        if not notify:
            return
        try:
            if error_msg is not None:
                if on_rejected is not None:
                    on_rejected(error_msg)
            elif on_completed is not None:
                on_completed()
        except Exception as cb_exc:
            logger.warning("Gemini proactive outcome handler raised: %s", cb_exc)

    async def _expire_gemini_proactive_outcome(
        self,
        token: str,
        ttl: float,
    ) -> None:
        """Fail closed if Gemini never emits turn_complete/interrupted."""
        try:
            await asyncio.sleep(ttl)
        except asyncio.CancelledError:
            return
        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is not None and outcome[0] == token:
            await self._interrupt_and_quarantine_gemini_proactive_outcome(
                token,
                error_msg="Gemini proactive response lifecycle timed out",
            )

    async def _interrupt_and_quarantine_gemini_proactive_outcome(
        self,
        token: str,
        *,
        error_msg: str,
    ) -> None:
        """Interrupt an unscoped Gemini turn and retain its token until safe."""

        outcome = getattr(self, "_gemini_proactive_outcome", None)
        if outcome is None or outcome[0] != token:
            return
        owner = getattr(self, "_gemini_proactive_outcome_owner", None)
        if owner is None or owner[2] != token:
            return
        connection_generation, provider_session, _, context = owner[:4]
        owner_scope = owner[4] if len(owner) > 4 else None
        # Gemini lifecycle events are not tagged with a response id. Do not
        # release this token while the original generation can still emit a
        # late terminal: that terminal could otherwise settle a newer retry.
        def scope_still_ours() -> bool:
            # The connection and the session both survive a new user turn, so
            # neither tells one apart. Only the tool scope moves.
            return owner_scope is None or owner_scope == getattr(
                self, "_tool_scope_generation", 0
            )

        def retire_without_touching_the_live_turn() -> None:
            # BOTH halves of this quarantine act on whatever is generating
            # now: client_content interrupts it, and the retirement below
            # marks the session fatal and closes it. Once a real user turn
            # owns the generation, either one takes THEIR response down, so
            # neither may run -- this inject has no claim left. Settling is
            # still correct and still safe: the outcome fences added for
            # replacement connections already stop a late terminal from the
            # abandoned turn settling anyone else's retry.
            logger.info(
                "Gemini proactive quarantine retired without interrupting: a "
                "new user turn owns the generation now"
            )
            self._settle_gemini_proactive_inject(
                error_msg=error_msg,
                expected_connection_generation=connection_generation,
                expected_provider_session=provider_session,
                expected_outcome_token=token,
            )

        if not scope_still_ours():
            retire_without_touching_the_live_turn()
            return
        try:
            await provider_session.send_client_content(
                turns=None,
                turn_complete=False,
            )
        except Exception as exc:
            logger.warning(
                "Gemini proactive interrupt failed while quarantining outcome: %s",
                exc,
            )
        await asyncio.sleep(_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS)
        if not scope_still_ours():
            # Re-checked: the grace period is exactly long enough for a user
            # to start talking.
            retire_without_touching_the_live_turn()
            return
        live_owner = getattr(self, "_gemini_proactive_outcome_owner", None)
        still_current_connection = bool(
            connection_generation == self._connection_generation
            and provider_session is self._gemini_session
        )
        if live_owner is None:
            # SETTLED, not merely superseded -- and the two are different
            # owners of the teardown. Every other settler (an ordinary close,
            # the receive-loop teardown, a normal turn_complete) either owns
            # the context it closed or has no context to close, so exiting the
            # captured one here would be a second __aexit__ on a one-shot SDK
            # context that already left the close registry. Deliberately NOT
            # gated on connection currency: an ordinary close finishing during
            # the grace sleep also drops _gemini_session, which makes the
            # connection read false and used to let this fall straight through.
            return
        if still_current_connection and live_owner[:3] != owner[:3]:
            # SUPERSEDED on the live connection: a newer outcome took over and
            # owns what follows. A replacement CONNECTION is the opposite case
            # and must not return -- nobody else will exit the context this
            # quarantine captured.
            return

        # No terminal followed the interrupt. Retire the whole Gemini session
        # before releasing the token so no event from the abandoned turn can
        # cross-talk with a future reconnect/retry.
        if still_current_connection:
            self._fatal_error_occurred = True
        try:
            if still_current_connection:
                await self._close_gemini()
            else:
                await self._close_gemini_context(context, provider_session)
        except Exception as exc:
            logger.warning(
                "Gemini proactive quarantine close failed: %s",
                exc,
            )
        self._settle_gemini_proactive_inject(
            error_msg=error_msg,
            expected_connection_generation=connection_generation,
            expected_provider_session=provider_session,
            expected_outcome_token=token,
        )

    async def _expire_inject_rejection_handler(
        self,
        event_id: str,
        ttl: float,
        token: Optional[str] = None,
    ) -> None:
        """TTL backstop for a ticket whose terminal lifecycle never arrives."""
        try:
            await asyncio.sleep(ttl)
        except asyncio.CancelledError:
            return
        if token is None:
            # Standalone image rejection handlers share this TTL helper but
            # do not own the proactive response-outcome gate.
            self._inject_rejection_handlers.pop(event_id, None)
            return
        if self._proactive_inject_outcome_token != token:
            return
        self._inject_rejection_handlers.pop(event_id, None)
        if not self._inject_rejection_handlers:
            self._proactive_inject_awaiting_outcome = False
            self._proactive_inject_outcome_token = None

    @staticmethod
    def _looks_like_response_conflict(error_msg: str) -> bool:
        """Heuristic: does this ``error`` message look like the server
        rejecting a ``response.create`` because a response is already active?

        That ``response_already_active`` class is the ONLY async rejection a
        proactive inject can provoke (our inject is the only client-issued
        ``response.create`` on the voice path). Matching its content lets us
        route the rejection even when the provider's error doesn't echo our
        client ``event_id``. Kept deliberately broad across phrasings /
        providers but still scoped to response-conflict wording so unrelated
        errors (auth / quota / 503 / idle-timeout) don't trip it."""
        low = error_msg.lower()
        if "response_already_active" in low:
            return True
        return "response" in low and any(
            k in low for k in ("already", "active", "in progress", "in_progress", "exists", "ongoing")
        )

    def _route_inject_rejection(self, err_event_id, error_msg: str) -> None:
        """Deliver a server rejection to the matching proactive-inject
        ``on_rejected`` handler so the caller re-enqueues the cb.

        Two correlation paths:
          1. **By id (precise)** — OpenAI Realtime (and any provider that
             echoes the offending client ``event_id``): pop and fire the exact
             handler.
          2. **By content (fallback)** — ONLY when the provider omits a
             client-correlation id entirely (``err_event_id`` falsy): if the
             error looks like a response-conflict
             (``_looks_like_response_conflict``), fire only the handler keyed
             by the current proactive outcome token. Standalone callback-image
             handlers may remain in the shared map after an earlier successful
             text turn and must never be swept by a later no-id conflict.

        Critically, the content fallback is gated on ``err_event_id`` being
        absent. If the error DOES carry a client event_id that simply isn't
        ours, the rejection belongs to a different ``response.create`` (e.g.
        ``create_response`` hot-swap priming / tool-result continuation /
        ``signal_user_activity_end`` — all of which get a timestamp event_id
        from ``send_event``'s setdefault), NOT our inject. Firing our handlers
        on those would re-enqueue callbacks the model actually accepted →
        duplicate announcements. So a present-but-non-matching id means "not
        ours; do nothing"."""
        if not self._inject_rejection_handlers:
            return

        def _fire(handler) -> None:
            try:
                handler(error_msg)
            except Exception as cb_exc:
                logger.warning("proactive inject rejection handler raised: %s", cb_exc)

        if err_event_id:
            # Id present: fire ONLY on an exact match. A non-matching id
            # belongs to some other request's rejection — not ours.
            handler = self._inject_rejection_handlers.pop(err_event_id, None)
            if handler is not None:
                # Outcome-bound handlers close their own token window.
                # Standalone image handlers do not own that shared gate and
                # must not clear a newer text inject's state.
                _fire(handler)
            return

        # No client-correlation id at all — fall back to content matching,
        # but ONLY while a proactive inject is genuinely awaiting its outcome
        # (one-shot window). This excludes a no-id response-conflict raised by
        # a DIFFERENT response.create sender (create_response / tool-result /
        # signal_user_activity_end) from hitting a lingering, already-succeeded
        # proactive handler.
        if (
            self._proactive_inject_awaiting_outcome
            and self._looks_like_response_conflict(error_msg)
        ):
            outcome_event_id = getattr(
                self,
                "_proactive_inject_outcome_token",
                None,
            )
            handler = (
                self._inject_rejection_handlers.pop(outcome_event_id, None)
                if outcome_event_id
                else None
            )
            if handler is not None:
                self._proactive_inject_awaiting_outcome = False
                _fire(handler)

    async def prompt_ephemeral(
        self,
        instruction: str = "",
        *,
        language: str = "zh",
        user_turn_active: Optional[Callable[[], bool]] = None,
        session_owned: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Inject a text turn and explicitly request proactive speech.

        Realtime providers now all accept text input, so proactive turns no
        longer synthesize and upload a fake user WAV to trip server VAD.  This
        avoids bogus ASR transcripts in the UI and removes the pacing/race
        surface of a multi-chunk audio injection.

        Pending native visual context is sent immediately before the text turn.
        Standard StepFun remains the only non-native realtime provider: when a
        VISION_MODEL description is available, it is injected as text first.
        """
        # ── Guard checks ──────────────────────────────────────────────
        if self._fatal_error_occurred:
            return False
        if self._proactive_inject_awaiting_outcome:
            logger.debug("prompt_ephemeral: skipped — another proactive inject is pending")
            return False
        if self._is_gemini:
            if self._gemini_session is None:
                return False
        elif self.ws is None:
            return False
        if self.is_active_response():
            logger.debug(
                "prompt_ephemeral: skipped — response arbiter is active or queued"
            )
            return False
        _now = time.time()
        # ── AI-speech guard (symmetric with _user_recent_activity_time) ─────────
        # response.done / turn_complete set _is_responding False, but AI-side
        # content may continue trickling:
        #   1. Gemini turn_complete precedes final audio-frame delivery.
        #   2. Brief False between sub-turns of long Gemini responses.
        #   3. Gap from response.created to first content chunk (_is_responding True already
        #      covers this, but redundant protection is harmless).
        # A 3s window covers those premature-completion gaps, preventing proactive text from interrupting the AI's own tail.
        if _now - self._ai_recent_activity_time < self._ai_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — AI recently active (%.2fs ago)",
                         _now - self._ai_recent_activity_time)
            return False
        # ── User-speech guards ───────────────────────────────────────
        # B: First check recent voice frames via independent _user_recent_activity_time;
        # signal is independent of sustain, covering first 500ms of user speech and between-sentence pauses.
        # Applies to every VAD source (RNNoise / server-VAD / RMS), no longer gated by
        # _rnnoise_vad_active. RMS threshold 500 is conservative; false positives are acceptable
        # compared with proactively interrupting user speech.
        if _now - self._user_recent_activity_time < self._user_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — user recently active (%.2fs ago)",
                         _now - self._user_recent_activity_time)
            return False
        # A: Existing _client_vad_active + grace check (sustained VAD fallback).
        # Grace extended from 2s to 6s for natural pauses.
        # Gate: reliable VAD source available.
        #   - Server-VAD backends (Qwen/OpenAI): reliable speech_started/stopped,
        #     independent of RNNoise, covering 16kHz mobile utterances >8s (_user_recent_activity
        #     expires 8s after speech_started while user still speaks, requiring _client_vad_active fallback).
        #   - RNNoise client VAD (48kHz desktop + Gemini/lanlan.app+free).
        # RMS-only path (16kHz without server-VAD) is too noisy to trust; rely on _user_recent_activity.
        if self._has_server_vad or self._rnnoise_vad_active:
            if self._client_vad_active:
                logger.debug("prompt_ephemeral: skipped — user speaking (VAD active)")
                return False
            if _now - self._client_vad_last_speech_time < self._client_vad_grace_period:
                logger.debug("prompt_ephemeral: skipped — VAD grace period")
                return False
        if callable(user_turn_active) and user_turn_active():
            logger.debug(
                "prompt_ephemeral: skipped — external user turn is active"
            )
            return False

        outcome_observed = asyncio.Event()
        delivery_rejected = False
        visual_delivery_rejected = False
        rejection_message = ""
        visual_event_id: str | None = None
        events_before_text: tuple[Dict[str, Any], ...] = ()
        # Whether this turn actually wrote raw frames into provider context; on race-related early returns,
        # use this to decide whether snapshot is consumed.
        _raw_frame_sent = False
        external_visual_delivery = getattr(
            self,
            "_visual_delivery_mode",
            VisualDeliveryMode.NATIVE,
        ) == VisualDeliveryMode.EXTERNAL_DESCRIPTION

        def _on_rejected(error_msg: str) -> None:
            nonlocal delivery_rejected, rejection_message
            delivery_rejected = True
            rejection_message = error_msg
            if (
                not visual_delivery_rejected
                and self._looks_like_response_conflict(error_msg)
            ):
                # A response-conflict rejection happens only after the arbiter
                # has persisted every event preceding response.create,
                # including this snapshot's native image or Step description.
                # The competing response can already see that context, so do
                # not offer it to the next scheduled proactive turn. Exact
                # visual-event rejection still re-arms it via
                # _on_visual_rejected instead.
                _mark_snapshot_consumed_if_current()
            logger.warning("prompt_ephemeral: proactive text rejected: %s", error_msg)
            outcome_observed.set()

        def _on_visual_rejected(error_msg: str) -> None:
            nonlocal visual_delivery_rejected
            visual_delivery_rejected = True
            if (
                getattr(self, "_latest_image_generation", 0)
                == snapshot_image_generation
            ):
                self._proactive_image_consumed = False
            _on_rejected(error_msg)

        def _on_completed() -> None:
            outcome_observed.set()

        def _remove_visual_rejection_handler() -> None:
            if visual_event_id is not None:
                self._inject_rejection_handlers.pop(visual_event_id, None)

        # ── Resolve pending visual context ────────────────────────────
        # Native providers can consume an unconsumed raw frame immediately.
        # Standard StepFun cannot: stream_image() caches the frame before its
        # external VISION_MODEL analysis finishes. Defer that proactive
        # attempt until the matching description exists, or we would select a
        # screen-aware prompt without injecting any visual context and then
        # incorrectly mark the frame consumed.
        has_pending_frame = (
            self._latest_image_b64 is not None
            and not self._proactive_image_consumed
        )
        if (
            has_pending_frame
            and not external_visual_delivery
            and not self._supports_native_image
            and not self._image_recognized_this_turn
        ):
            logger.debug(
                "prompt_ephemeral: skipped — StepFun visual analysis is pending"
            )
            return False
        # Independent ASR fenced raw frames: microphone routing belongs to Core and frames cannot use provider
        # connection, but stage_multimodal_frame still keeps observation cache warm. During fencing,
        # treating has_vision as true makes native injection below fail against the fence, losing the entire proactive
        # message including text. Screen-sharing frames continuously refill cache,
        # silencing the whole session. Treat fenced turns as no vision: degrade to text, leave frames unconsumed,
        # and use them in a later turn after the fence lifts.
        raw_delivery_fenced = (
            getattr(self, "_raw_visual_delivery_blocked", False)
            and not external_visual_delivery
        )
        has_vision = self._image_recognized_this_turn or (
            (self._supports_native_image or external_visual_delivery)
            and has_pending_frame
            and not raw_delivery_fenced
        )
        # Snapshot the current image so concurrent stream_image() calls don't
        # cause us to mark a newer frame as consumed.
        snapshot_image_b64 = self._latest_image_b64 if has_vision else None
        snapshot_image_generation = (
            getattr(self, "_latest_image_generation", 0) if has_vision else None
        )

        def _mark_snapshot_consumed_if_current() -> None:
            if (
                has_vision
                and getattr(self, "_latest_image_generation", 0)
                == snapshot_image_generation
            ):
                self._proactive_image_consumed = True
                if not self._supports_native_image:
                    # The completed Step annotation belongs to the consumed
                    # generation. Rearm analysis only here; response.done may
                    # belong to an unrelated response while this frame is
                    # still waiting for its proactive delivery.
                    self._image_recognized_this_turn = False
                    self._image_description = _IMAGE_ANALYSIS_PENDING_DESCRIPTION

        # Text-triggered turns do not produce the server-VAD speech_stopped
        # event that normally starts a fresh external-TTS turn. Rotate before
        # persisting visual context so activity that wins while the callback
        # is blocked cannot consume an image from an abandoned proactive turn.
        if self._has_server_vad and self.on_sid_rotate is not None:
            try:
                await self.on_sid_rotate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "prompt_ephemeral: SID rotation failed before visual inject: %s",
                    exc,
                )
                return False
            if (
                self.is_active_response()
                or self._client_vad_active
                or (callable(user_turn_active) and user_turn_active())
                or self._user_recent_activity_time > _now
                or self._ai_recent_activity_time > _now
            ):
                logger.info(
                    "prompt_ephemeral: skipped — activity started during SID rotation"
                )
                return False

            # SID rotation can yield long enough for Core to reconcile an
            # independent/blocked visual route. A snapshot captured under the
            # old mode must not cross that boundary into a different ASR turn;
            # retry from the new route instead of sending it as either raw
            # media or an external description.
            current_external_visual_delivery = getattr(
                self,
                "_visual_delivery_mode",
                VisualDeliveryMode.NATIVE,
            ) == VisualDeliveryMode.EXTERNAL_DESCRIPTION
            if current_external_visual_delivery != external_visual_delivery:
                logger.info(
                    "prompt_ephemeral: skipped — visual route changed during SID rotation"
                )
                return False
            external_visual_delivery = current_external_visual_delivery

        if (
            has_vision
            and not self._is_gemini
            and (
                # One-shot description-mode cue images now use raw WebSocket events and may also be
                # rejected asynchronously, requiring this correlation handle too; otherwise
                # _on_visual_rejected() finds no owner and never rearms the snapshot.
                (self._supports_native_image and snapshot_image_b64)
                or (
                    not self._supports_native_image
                    and self._image_recognized_this_turn
                    and self._image_description
                )
            )
        ):
            # WebSocket-native image events can be rejected asynchronously
            # after the write succeeds. Correlate that exact error with this
            # proactive delivery before deciding the frame was consumed.
            # Gemini uses its SDK instead of client event IDs, so synchronous
            # SDK send failures are handled by stream_image() directly.
            visual_event_id = f"event_inject_image_{uuid.uuid4().hex}"
            self._inject_rejection_handlers[visual_event_id] = _on_visual_rejected
            self._fire_task(
                self._expire_inject_rejection_handler(visual_event_id, 60.0)
            )

        if (
            has_vision
            and not external_visual_delivery
            and self._supports_native_image
            and snapshot_image_b64
        ):
            # ``bypass_rate_limit`` identifies this as one deliberate cue image.
            # stream_image also owns the provider-specific wire event, including
            # the dedicated free-service input_image_buffer.append route.
            try:
                stage_result = await self.stream_image(
                    snapshot_image_b64,
                    bypass_rate_limit=True,
                    cache_latest=False,
                    event_id=visual_event_id,
                    # Same basis as external-description below: this image accompanies a proactive/greeting
                    # turn unknown to the user. Omitting this parameter uses the default,
                    # making frame-copy plugins filtering 'proactive' miss native-provider
                    # delivery.
                    source="proactive",
                )
            except asyncio.CancelledError:
                _remove_visual_rejection_handler()
                raise
            except Exception as exc:
                _remove_visual_rejection_handler()
                logger.warning(
                    "prompt_ephemeral: native image inject failed; keeping visual context for retry: %s",
                    exc,
                )
                return False
            if hasattr(stage_result, "accepted"):
                raw_stage_mode = getattr(stage_result, "mode", None)
                stage_mode = getattr(raw_stage_mode, "value", raw_stage_mode)
                if not bool(stage_result.accepted) or stage_mode != "native":
                    _remove_visual_rejection_handler()
                    logger.info(
                        "prompt_ephemeral: visual route changed during native image staging; keeping snapshot for retry"
                    )
                    return False
            if delivery_rejected:
                _remove_visual_rejection_handler()
                logger.info(
                    "prompt_ephemeral: native image rejected before proactive text inject"
                )
                return False
        elif has_vision and external_visual_delivery and snapshot_image_b64:
            try:
                stage_result = await self.stream_image(
                    snapshot_image_b64,
                    source="proactive",
                    request_id=f"proactive-{snapshot_image_generation}",
                    bypass_rate_limit=True,
                    cache_latest=False,
                    event_id=visual_event_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "prompt_ephemeral: external visual analysis failed: %s",
                    exc,
                )
                # A transient analysis failure is retryable. Keep the exact
                # generation unconsumed and do not downgrade this attempt to a
                # text-only nudge. Only an explicit empty analysis result below
                # is terminal for the selected frame.
                return False
            external_description = str(
                getattr(stage_result, "description", "") or ""
            ).strip()
            _raw_mode = getattr(stage_result, "mode", None)
            _stage_mode = getattr(_raw_mode, "value", _raw_mode)
            if (
                hasattr(stage_result, "accepted")
                and not bool(getattr(stage_result, "accepted", False))
                and _stage_mode == VisualDeliveryMode.NATIVE.value
            ):
                # Raw cue image **never sent**: routing mode changed while waiting for send semaphore /
                # recompressing oversized images, or transport disconnected, and send_event returned False. This is not terminal
                # empty-analysis failure; treating it that way consumes an image that never reached provider
                # and degrades the turn to text. Match native delivery above:
                # detach correlation handle, retain armed snapshot, retry next proactive turn.
                _remove_visual_rejection_handler()
                logger.info(
                    "prompt_ephemeral: raw cue image was not delivered; keeping snapshot for retry"
                )
                return False
            if (
                bool(getattr(stage_result, "accepted", False))
                and _stage_mode == VisualDeliveryMode.NATIVE.value
            ):
                _raw_frame_sent = True
                if delivery_rejected:
                    # Provider rejected the image before this turn's text injection (raw image travels
                    # by WebSocket event; error.event_id may arrive before write returns).
                    # Sending text normally would describe an image absent from context. Match native
                    # delivery's `if delivery_rejected:` handling above.
                    _remove_visual_rejection_handler()
                    logger.info(
                        "prompt_ephemeral: raw cue image rejected before proactive text inject"
                    )
                    return False
                # Image already delivered raw (description-mode one-shot cues use native channel).
                # Add only a brief explanation of what it is, avoiding a separate
                # VISION_MODEL annotation call, saving paid invocation and paraphrase distortion.
                # Same description policy as branch below: explicitly identify it as not user speech.
                events_before_text = ({
                    "type": "conversation.item.create",
                    "item": {
                        "id": new_client_item_id("vis"),
                        "type": "message",
                        "role": "user",
                        "content": [{
                            "type": "input_text",
                            "text": (
                                "[系统视觉感知结果，不是用户陈述]\n"
                                "上面这张图是此刻的屏幕画面。"
                            ),
                        }],
                    },
                },)
            elif external_description:
                if not self._is_gemini:
                    visual_event_id = f"event_inject_image_{uuid.uuid4().hex}"
                    self._inject_rejection_handlers[
                        visual_event_id
                    ] = _on_visual_rejected
                    self._fire_task(
                        self._expire_inject_rejection_handler(
                            visual_event_id,
                            60.0,
                        )
                    )
                visual_event = {
                    "type": "conversation.item.create",
                    "item": {
                        "id": new_client_item_id("vis"),
                        "type": "message",
                        "role": "user",
                        "content": [{
                            "type": "input_text",
                            "text": (
                                "[系统视觉感知结果，不是用户陈述]\n"
                                f"当前画面：{external_description}"
                            ),
                        }],
                    },
                }
                if visual_event_id is not None:
                    visual_event["event_id"] = visual_event_id
                events_before_text = (visual_event,)
            else:
                # The selected generation reached a terminal empty/error
                # analysis result. Retire that exact snapshot so a proactive
                # retry cannot repeatedly spend vision calls on the same stale
                # frame; the generation fence preserves any newer arrival.
                _mark_snapshot_consumed_if_current()
                has_vision = False
        elif (
            has_vision
            and self._image_recognized_this_turn
            and self._image_description
        ):
            # Only standard StepFun reaches this path. Queue the description
            # in the same arbiter ticket as the proactive text/response so its
            # event id participates in the delivery outcome instead of being
            # an uncorrelated fire-and-forget conversation item.
            events_before_text = ({
                "type": "conversation.item.create",
                "event_id": visual_event_id,
                "item": {
                    "id": new_client_item_id("vis"),
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": self._image_description}],
                },
            },)

        # Re-check activity after any image await. A user or AI turn that won
        # during the visual send must preempt this proactive response.create.
        # The manager can also replace this client during the await; a retired
        # session must never receive a nudge whose result would be discarded.
        if callable(session_owned) and not session_owned():
            _remove_visual_rejection_handler()
            logger.info(
                "prompt_ephemeral: skipped — session ownership changed during visual inject"
            )
            return False
        if (
            self.is_active_response()
            or (callable(user_turn_active) and user_turn_active())
            or self._user_recent_activity_time > _now
            or self._ai_recent_activity_time > _now
        ):
            if (
                has_vision
                and snapshot_image_b64
                and self._supports_native_image
                and (not external_visual_delivery or _raw_frame_sent)
            ):
                # The raw frame is already persistent provider context and may
                # be consumed by the turn that won this race. Account for it
                # now to avoid resending duplicate/stale visual context. Keep
                # the exact rejection handler alive so a late provider error
                # can re-arm the snapshot.
                #
                # Criterion is whether raw frames actually sent this turn, not current delivery mode: description-mode
                # one-shot cues now also use raw channel, so mode checks miss them. That
                # image is already in provider context but snapshot stays armed, resending it
                # on the next proactive turn.
                _mark_snapshot_consumed_if_current()
            else:
                # Step's description is only queued below, so no visual event
                # was sent when activity won this pre-inject gate.
                _remove_visual_rejection_handler()
            logger.info("prompt_ephemeral: skipped — activity started during visual inject")
            return False

        text = instruction.strip() or _proactive_text_instruction(
            language,
            has_vision=has_vision,
        )

        proactive_ticket = None
        # Captured before the inject so the timeout path below can tell a
        # still-ours generation from one a later user turn took over.
        ephemeral_scope = getattr(self, "_tool_scope_generation", 0)

        def _ephemeral_scope_still_ours() -> bool:
            return ephemeral_scope == getattr(self, "_tool_scope_generation", 0)

        try:
            inject_kwargs = {
                "on_rejected": _on_rejected,
                "on_completed": _on_completed,
            }
            if events_before_text:
                inject_kwargs["events_before_text"] = events_before_text
            proactive_ticket = await self.inject_text_and_request_response(
                text,
                **inject_kwargs,
            )
        except asyncio.CancelledError:
            # inject_text_and_request_response may discover that cancellation
            # lost to this exact ticket's successful terminal state while it
            # was still awaiting ticket.sent. Its completion callback is the
            # authoritative signal that the snapshot was delivered.
            if outcome_observed.is_set() and not delivery_rejected:
                _mark_snapshot_consumed_if_current()
            else:
                _remove_visual_rejection_handler()
            raise
        except Exception as exc:
            _remove_visual_rejection_handler()
            logger.warning(
                "prompt_ephemeral: proactive text inject failed; keeping visual context for retry: %s",
                exc,
            )
            return False
        try:
            await asyncio.wait_for(
                outcome_observed.wait(),
                timeout=_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            # The inject itself has already returned, so cancellation here
            # must target the retained ticket rather than leaving a response
            # alive whose visual snapshot this coroutine can no longer consume.
            cancellation_requested = True
            ticket_completed = False
            try:
                if proactive_ticket is not None:
                    cancellation_requested = await asyncio.shield(
                        self._ensure_response_arbiter().cancel_ticket(
                            proactive_ticket
                        )
                    )
                elif not outcome_observed.is_set():
                    # Same fence as the timeout path below, and for the same
                    # reason: with no ticket this is a raw client_content
                    # interrupt aimed at whatever is generating NOW, so after
                    # a real user turn it would cancel THEIR response.
                    await asyncio.shield(
                        self.cancel_response(
                            send_guard=_ephemeral_scope_still_ours,
                        )
                    )
            except Exception as cancel_exc:
                logger.warning(
                    "prompt_ephemeral: cancellation cleanup failed: %s",
                    cancel_exc,
                )
            # The receive loop may have resolved this exact ticket just before
            # cancellation reached cancel_ticket(). Its terminal no-op is
            # authoritative: consume a successful delivery instead of
            # re-offering the same visual context to the scheduler.
            if proactive_ticket is not None and not cancellation_requested:
                ticket_done = getattr(proactive_ticket, "done", None)
                if ticket_done is not None:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(ticket_done),
                            timeout=(
                                _PROACTIVE_TICKET_CANCEL_OBSERVE_TIMEOUT_SECONDS
                            ),
                        )
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception:
                        pass
                    else:
                        ticket_completed = True
                        _mark_snapshot_consumed_if_current()
            if not ticket_completed:
                _remove_visual_rejection_handler()
            raise
        except asyncio.TimeoutError:
            # Gemini has no exact response ticket. Its turn_complete callback
            # may settle this outcome at the timeout boundary after wait_for
            # has already chosen the timeout branch. Observe that local gate
            # before issuing an unscoped client-content interruption, which
            # could otherwise cancel a newer turn.
            outcome_settled = outcome_observed.is_set()
            if outcome_settled:
                logger.info(
                    "prompt_ephemeral: proactive outcome settled at delivery timeout boundary"
                )
            ticket_completed = False
            # ``response.done`` can resolve the exact ticket at the timeout
            # boundary before its observer gets a turn to set
            # ``outcome_observed``. Treat that completed result as the
            # authoritative delivery outcome; otherwise we would preserve an
            # already-consumed visual snapshot and resend it on the next
            # scheduler attempt.
            ticket_done = getattr(proactive_ticket, "done", None)
            if (
                not outcome_settled
                and ticket_done is not None
                and ticket_done.done()
            ):
                try:
                    ticket_done.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                else:
                    ticket_completed = True
            if not outcome_settled and ticket_completed:
                logger.info(
                    "prompt_ephemeral: proactive ticket completed at delivery timeout boundary"
                )
            if not outcome_settled and not ticket_completed:
                # Keep this request quarantined until its own terminal
                # lifecycle arrives: clearing the shared maps here would let a
                # late response.done sweep handlers registered by a retry.
                # Ask the provider to terminate the hanging response so the
                # normal response.done/error path releases the gate promptly.
                # If even the cancel lifecycle never arrives, the existing 60s
                # TTL remains the conservative final backstop.
                cancellation_requested = True
                try:
                    if proactive_ticket is not None:
                        cancellation_requested = (
                            await self._ensure_response_arbiter().cancel_ticket(
                                proactive_ticket,
                                wait=False,
                            )
                        )
                    else:
                        # Gemini has no ticket to cancel, so this is a raw
                        # client_content interrupt aimed at whatever is
                        # generating now. Guard it: after a new user turn it
                        # would cancel THEIR response instead of ours.
                        await self.cancel_response(
                            send_guard=_ephemeral_scope_still_ours,
                        )
                except Exception as cancel_exc:
                    logger.warning(
                        "prompt_ephemeral: timed-out response cancel failed; keeping inject quarantined: %s",
                        cancel_exc,
                    )
                # cancel_ticket() atomically reports a terminal/missing exact
                # request as a no-op. Its worker may still need one event-loop
                # turn to publish ticket.done, so consume that authoritative
                # result before classifying the timeout as failed.
                if proactive_ticket is not None and not cancellation_requested:
                    ticket_done = getattr(proactive_ticket, "done", None)
                    if ticket_done is not None:
                        try:
                            await asyncio.shield(ticket_done)
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                        else:
                            ticket_completed = True
                if ticket_completed:
                    logger.info(
                        "prompt_ephemeral: proactive ticket completed while timeout cancellation no-op'd"
                    )
                    # Fall through to the normal success path so the matching
                    # visual snapshot is consumed exactly once.
                else:
                    logger.warning(
                        "prompt_ephemeral: proactive text delivery timed out; keeping visual context for retry"
                    )
                    _remove_visual_rejection_handler()
                    return False
        if delivery_rejected:
            if visual_delivery_rejected and proactive_ticket is not None:
                try:
                    await self._ensure_response_arbiter().cancel_ticket(
                        proactive_ticket
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        "prompt_ephemeral: rejected visual response cleanup failed: %s",
                        cancel_exc,
                    )
            _remove_visual_rejection_handler()
            logger.info(
                "prompt_ephemeral: proactive text delivery failed; keeping visual context for retry: %s",
                rejection_message,
            )
            return False
        _mark_snapshot_consumed_if_current()
        # Native image validation/filtering errors may arrive after the text
        # response has completed. Keep the exact image handler until rejection
        # or its TTL so a late error can re-arm this snapshot for retry.
        logger.info(
            "prompt_ephemeral: proactive text injected (%s)",
            "vision" if has_vision else "general",
        )
        return True

    async def cancel_response(
        self,
        *,
        wait: bool = False,
        timeout: float = 3.0,
        send_guard: Callable[[], bool] | None = None,
    ) -> None:
        """Cancel the current response."""
        if self._is_gemini:
            if self._gemini_session is None:
                return
            if send_guard is not None and not send_guard():
                return
            # Gemini Live has no response.cancel event. Any client_content
            # interrupts current generation; leaving turn_complete false avoids
            # immediately starting a replacement model turn.
            await self._gemini_session.send_client_content(
                turns=None,
                turn_complete=False,
            )
            return
        if wait:
            await self._ensure_response_arbiter().cancel_current(timeout)
            return
        await self.send_event(
            {"type": "response.cancel"},
            send_guard=send_guard,
        )
