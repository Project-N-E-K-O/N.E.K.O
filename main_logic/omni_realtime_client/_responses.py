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
    OMNI_WS_FRAME_LIMIT_BYTES,
    Optional,
    VisualDeliveryMode,
    asyncio,
    base64,
    json,
    logger,
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
    def _ensure_response_arbiter(self) -> RealtimeResponseArbiter:
        arbiter = getattr(self, "_response_arbiter", None)
        if arbiter is None:
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
            # Gemini Live API 没有 session.update 机制，只能通过
            # send_client_content 注入上下文（会创建 user turn）。
            # on_response_done 由 _handle_messages_gemini 自然触发。
            await self._create_response_gemini_with_skip_guard(
                text,
                skipped=skipped,
                raise_on_error=True,
                starts_user_turn=False,
            )
            return

        if not skipped and "qwen" not in self._model_lower:
            # skipped=False：需要模型主动响应（任务结果汇报）
            # 通过 create_response 注入 user 消息 + 触发响应
            # Qwen 不支持 conversation.item.create，走下方 update_session
            # Transport-only user message: a task-result report is not the
            # real user's turn, so it must not retire in-flight tool calls.
            await self.create_response(text, starts_user_turn=False)
        else:
            # skipped=True 或 Qwen：仅追加到 session instructions
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
        # Gemini 使用 send_client_content 发送文本内容
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

        # 跳过空内容的发送，避免触发 API 错误
        if not instructions or not instructions.strip():
            logger.info("Skipping empty content in create_response")
            return

        if starts_user_turn:
            self.note_user_turn_started()

        if skipped:
            self._skip_until_next_response = True

        item_event_id = f"event_user_item_{uuid.uuid4().hex}"
        response_event_id = f"event_user_response_{uuid.uuid4().hex}"
        item_id = f"item_neko_{uuid.uuid4().hex}"
        expected_item_id = item_id
        # 通过 conversation.item.create 添加用户消息，再触发响应。两步都
        # 进入全局仲裁器，直到 response.done 才释放下一次 create 的资格。
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
        item_id = f"item_neko_{uuid.uuid4().hex}"
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
                arbiter.pause_dispatch()
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
        staged_images, images_bytes = self._normalize_multimodal_turn_images(
            images
        )

        if self._is_gemini:
            # 上面只管了**张数**和**单张**上限；三张各自合规的帧加起来仍可能逼近
            # 30 MB。WebSocket 分支后面还有按聚合大小重压/摘帧那一步，Offline 走
            # fit_images_to_turn_budget —— 只有 Gemini 这条直接把 bytes 交给
            # send_client_content()，没有任何聚合闸。超限时 provider 整条请求拒收，
            # 用户这一轮就没了。
            #
            # 用与 Offline 同一条阶梯：先抽样、再压缩、都不行才丢，并且至少留一张。
            _fitted, _notice = await fit_images_to_turn_budget(
                list(staged_images),
                TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
            )
            if _notice:
                # 这条路没有 on_status_message，本来就只落日志——但级别要跟着
                # user_visible 走：归一化是每回合都会发生的例行事，用 warning 打
                # 等于把日志淹掉；真丢了图才是 warning 级的事。
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
                # 上面那步是 asyncio.to_thread 压几张 MB 级截图，是真实耗时的让
                # 出点。后继发声可以整段跑完 _begin_core_multimodal_turn（同步把
                # 本轮 record invalidated）+ prepare_external_voice_turn +
                # handle_interruption —— 这两条路互不互斥（前者不拿 swap lock，
                # 本函数也不拿 turn admission lock）。不复查的话，我们会把已经不
                # 属于这一轮的帧发给 provider，而 Core 收不到任何信号，既有的
                # "降级成纯文本"出口根本走不到。
                #
                # 判据用调用方穿进来的 visual_still_owned，与 Offline 交接那条路
                # 同源（asr_runtime.py 的 lambda: not visual_ownership_lost()）。
                # 刻意**不**用 _tool_scope_generation 代理：create_response 和
                # submit_external_text_turn 也会推进它，会在根本没有语音后继时
                # 把这一轮误降级。
                #
                # 丢帧只降级成纯文本，话照送 —— 与本 PR 其它三处所有权判据一致。
                #
                # ⚠️ 复查**不**放在这里，而是穿到 _submit_external_gemini_turn 里
                # 紧贴 SDK 送出的那一刻。理由有二：
                #   1. 放这里会被关在 `if _notice:` 之内，不裁剪时根本不跑；
                #   2. 这之后还有一个让出点——_submit_external_gemini_turn 头部的
                #      _await_gemini_external_quarantine()，后继回合可以在那段等待
                #      里让本轮 record 失效。
            await self._submit_external_gemini_turn(
                clean,
                images_bytes=images_bytes,
                visual_still_owned=visual_still_owned,
            )
            return None
        if self.ws is None or self._fatal_error_occurred:
            raise RuntimeError("realtime websocket is not connected")

        import hashlib

        event_suffix = uuid.uuid4().hex
        item_id = f"item_neko_{uuid.uuid4().hex}"
        item_event = {
            "type": "conversation.item.create",
            "event_id": f"event_asr_multimodal_item_{event_suffix}",
            "item": {
                "id": item_id,
                "type": "message",
                "role": "user",
                # 开头/中间/结尾同属一个 user item：一次发声是一段时间，三张按
                # 时间顺序排在 transcript 前面，模型才知道这段话对着的是哪段画面。
                # 仍然只触发一次回复。
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
        # 传输上限在这里先判一次，而不是等 send_event 里再判。原因是那条路失败
        # 只会 return False，而 arbiter 的 _worker_send 不看这个布尔值：整条
        # conversation.item.create（连同 transcript）被丢掉之后，它照样会发
        # response.create，用户拿到一个和自己这句话无关的回复。
        #
        # 这里把重压/摘帧提前做掉（helper 会就地改写 item_event，所以 send_event
        # 之后不会重复做）；连一张都压不进上限时抛错，让 Core 走既有的整轮
        # fail-closed（ASR_MULTIMODAL_TURN_FAILED）。不静默降级成纯文本 —— 那是
        # 本 PR 明确禁止的行为。
        # ⚠️ 降级要跑在超限判断**之前**。所有权已经丢了的话这些帧本来就不会送
        # 出去，先摘掉再判大小：否则一批注定要丢的帧压不进上限时会抛
        # RealtimeImagePayloadTooLargeError，把这一轮整个判死（Core 走
        # ASR_MULTIMODAL_TURN_FAILED），而它其实只该降级成纯文本、话照送。
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

        # 跑两次，位置不同、作用也不同：
        #   这里（enqueue 之前）—— 覆盖上面重压/摘帧那段 to_thread 让出点，并且
        #     能在还没占用 arbiter 名额时就把负载降下来；
        #   pre_commit（dispatch、_worker_send 之前）—— 覆盖 arbiter 内部的等待
        #     （等活跃响应结束、等发送信号量），那段窗口调用方够不着。
        # 两次都只摘图、保留 transcript，不走"整条拒"：拒是**提交之后**才发生的，
        # 要付一次未经确认的补偿删除（issue #2982）。
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
        # 送进 arbiter 之前的最后一道视觉所有权复查，与 Gemini 那条路对偶。
        #
        # 上面的重压/摘帧是 asyncio.to_thread，真实让出点。后继回合的
        # _begin_core_multimodal_turn 是**同步**把本轮 record invalidated 的，
        # 但它随后的 prepare_external_voice_turn 可能还堵在 turn-admission 锁上、
        # 尚未更新 _external_voice_turn_pause_id —— 于是下面那条 admission_check
        # 拿着没变的 pause id 照样放行，过期的帧就进了 provider 历史。两个判据的
        # 时机不同，admission_check 覆盖不了这一段。
        #
        # 就地摘掉图片、保留 transcript，而不是让 admission_check 去拒整条：
        # 那是**提交之后**才拒，需要一次未经确认的补偿删除（见 issue #2982），
        # 比在提交前把帧摘掉贵得多。丢帧只降级成纯文本，话照送。
        arbiter = self._ensure_response_arbiter()
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
            # 第三处，也是最后一处：arbiter 交给传输之后，send_event 还要等
            # _send_semaphore；那段等待里所有权同样可能翻转，而 payload 是拿到
            # 信号量之后才序列化的。用 main(#2837) 引入的每-ticket event_sender
            # 把同一个降级函数送进那个临界区，序列化自然会带上结果。
            event_sender=lambda _ev: self.send_event(
                _ev,
                pre_send=_downgrade_if_visual_ownership_lost,
            ),
        )
        active_pause_id = getattr(self, "_external_voice_turn_pause_id", None)
        if active_pause_id == stable_turn_id:
            self._external_voice_turn_pause_id = None
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
                arbiter.pause_dispatch()
        return ticket

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
            # ⚠️ 必须在上面这段 Gemini 隔离**之后**才宣告新用户回合。
            # note_user_turn_started() 会推进 _tool_scope_generation，而
            # _interrupt_and_quarantine_gemini_proactive_outcome 的第一道闸
            # scope_still_ours() 正是拿 owner 里记下的 scope 跟它比：先推进的话这
            # 个判据恒不相等，隔离每次都走 retire_without_touching_the_live_turn，
            # 一次 client_content 打断都不发。那条已被 SDK 收下、还在生成的主动
            # 搭话回合于是完全没被打断，外部 ASR 回合直接压在同一个 unscoped SDK
            # session 上，两个回合的音频/文本交错——正是这道围栏要防的事。
            # 隔离针对的是**上一轮**（主动搭话）那一轮，所以它必须在上一轮的
            # scope 下跑完；跑完之后这一轮才真正开始。
            self.note_user_turn_started()
            try:
                if not self._is_gemini:
                    arbiter = self._ensure_response_arbiter()
                    self._external_voice_turn_pause_id = stable_turn_id
                    arbiter.pause_dispatch()
                    await arbiter.cancel_current()
                await self.handle_interruption()
            except BaseException:
                self.abandon_external_voice_turn(stable_turn_id)
                raise
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
            await asyncio.shield(quarantine_task)
            if (
                quarantine_task.done()
                and getattr(self, "_gemini_external_quarantine_task", None)
                is quarantine_task
            ):
                self._gemini_external_quarantine_task = None
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
    ) -> None:
        """Submit one external-ASR turn through the owned Gemini lifecycle."""

        submit_task = asyncio.current_task()
        # 上一轮 external turn 可能还没等到终结事件：重叠发声时 B 的 prepare 会跑
        # 在 A 的 SDK send **之前**，那一刻还没有 token 可隔离，于是 prepare 里的
        # 隔离空转。到这里再直接覆盖 A 的活 token，两个回合就并存了——两份响应
        # 交错，或者 B 的所有权被 A 的终结顺手带走。
        #
        # 用与 prepare 相同的隔离，不另立判据：settle 掉旧的并退掉这条连接。
        # 「连接是能证明旧回合的迟到内容不会串进后继的最小范围」是这个文件已有的
        # 结论（见 _quarantine_gemini_external_submit）。
        if getattr(self, "_gemini_external_outcome_token", None) is not None:
            self._start_gemini_external_submit_quarantine()
            await self._await_gemini_external_quarantine()
        # 外部 ASR 回合**就是**一次新的用户发声，但这条路径不经过
        # _transport 里更新 _user_recent_activity_time 的那些点（音频没走 provider）。
        # 不更新的话 _is_new_turn 会把这一轮的首个内容判成「迟到续帧」：内容被
        # _interrupted 抑制、欠账不作废，随后它自己的终结把欠账消费掉、跳过结算
        # —— token 永远挂着，会话被钉成「忙」，她再也不会主动开口。
        # 送出前的最后一道视觉所有权复查。上面的
        # _await_gemini_external_quarantine() 是一个真实让出点（前一轮的 token
        # 还挂着时要等隔离跑完），后继回合可以在那段等待里让本轮 record 失效。
        # 与图片预算那一步同一判据：丢帧只降级成纯文本，话照送。
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
            # 取消只结束了**我们的 await**，Gemini 可能已经收下这一轮：
            # TranscriptDispatcher 的 invalidate_all() 会把取消沿 worker 一路传到
            # 这里，而那时 send 早就交给 SDK 了。此时按"没送成"结算 token，等于对
            # 外宣称没有在飞的回合，下一轮 prepare 便不会起隔离，那一轮的迟到
            # transcript / 响应会串进后继回合。
            #
            # 所以保住 token，改为就地武装隔离——隔离本体会 join 掉这条 submit、
            # settle token 再退掉这条连接，所以忙标志不会因此永久挂住（
            # is_active_response() 读的就是这个 token）。这与 proactive 那条路
            # 在 CancelledError 上的处理对偶，判据一致。
            #
            # 不走 _start_gemini_external_submit_quarantine：它有
            # "submit_task is asyncio.current_task() → return" 的自保，从这里调
            # 是空转。直接 fire 隔离本体，由它去 join 我们自己。
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
                # 同步发送失败（provider 直接拒）才立刻结算：那一轮确实没被收下。
                self._settle_gemini_external_turn(outcome_token)

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
        # Gemini 的 external-ASR 回合在 SDK send 返回之后、第一个模型内容事件到达
        # 之前，_is_responding 还是 False、arbiter 也已经空闲，但那一轮其实已经被
        # provider 接下了（_gemini_external_outcome_token 活着，要到 turn_complete
        # / interrupted 才落地）。这段窗口里不认它，排队的主动搭话就能过掉所有忙检
        # 查、再插一个不受管辖的 Gemini 回合，两者的终结事件互相顶掉。
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
                # 按 session 收窄，与上面 outcome owner 同一判据。两条 send 只有落
                # 在**同一个** SDK session 上才会互相交错；卡在退休 session 上的那
                # 条不该挡住替换连接的 inject——_on_connection_attached 正是为此退
                # 掉了前一条的 outcome，如果这里还拦着，替换连接依然要等到 60s 过
                # 期或隔离结束才做得成自己的主动搭话。退休那条由拆除路径
                # (_cancel_gemini_submit_tasks) 取消，不会漏。
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
        item_id = f"item_neko_{uuid.uuid4().hex}"
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
        # ── AI-speech guard（对称于 _user_recent_activity_time）─────────
        # _is_responding 已被 response.done / turn_complete flip False，但 AI 侧
        # content 流可能还在滴水：
        #   1. Gemini turn_complete 早于最后几帧音频送达
        #   2. Gemini 长回复 sub-turn 间的 False 瞬间
        #   3. response.created 到首 content chunk 的空窗（_is_responding 已 True
        #      覆盖这一条，但加这层冗余保险无害）
        # 3s 窗口覆盖上述抢跑 gap，避免主动文本踩着 AI 尾巴打断自己。
        if _now - self._ai_recent_activity_time < self._ai_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — AI recently active (%.2fs ago)",
                         _now - self._ai_recent_activity_time)
            return False
        # ── User-speech guards ───────────────────────────────────────
        # B: 先用独立的 _user_recent_activity_time 判定近期是否有语音帧；
        # 此信号不依赖 sustain，覆盖用户说话首 500ms 与句间停顿缝隙。
        # 适用所有 VAD 源（RNNoise / server-VAD / RMS），所以不再门控在
        # _rnnoise_vad_active 下 —— RMS 阈值 500 已较保守，误触可接受，
        # 相比主动搭话切断用户说话的体验损失值得。
        if _now - self._user_recent_activity_time < self._user_recent_activity_window:
            logger.debug("prompt_ephemeral: skipped — user recently active (%.2fs ago)",
                         _now - self._user_recent_activity_time)
            return False
        # A: 现有 _client_vad_active + grace 检查（sustained VAD 信号兜底）。
        # Grace 已从 2s 扩到 6s，覆盖自然停顿。
        # 门控条件：存在可靠 VAD 信号源。
        #   - server-VAD 后端（Qwen/OpenAI）：server 的 speech_started/stopped 可靠，
        #     不依赖 RNNoise。特别覆盖 16kHz 移动端长句 >8s 的场景（_user_recent_activity
        #     在 speech_started 打点后 8s 过期，而用户还在说，需要 _client_vad_active 兜底）。
        #   - RNNoise 客户端 VAD（48kHz 桌面 + Gemini/lanlan.app+free）
        # RMS-only 路径（16kHz 无 server-VAD）信号太噪，不信任，依赖 _user_recent_activity。
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
        # 这一轮有没有真的把原始帧写进 provider context。竞态早退时要靠它决定
        # 快照该不该记成已消费。
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
        # 独立 ASR 给会话上了原始帧栅栏：麦克风路线归 Core，帧不许走 provider 连
        # 接，但 stage_multimodal_frame 仍然为「主动观察」把缓存喂得温热。栅栏期还
        # 把 has_vision 算成真的话，下面那次 native inject 必被栅栏拒掉、整条主动搭
        # 话连文本一起发不出去；而屏幕共享期间帧持续到达，缓存一直被重新装填，于是
        # 整场都哑了。栅栏期按无视觉处理：这一轮退化成纯文本，帧留着不消费，等栅栏
        # 解除之后的某一轮再用。
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
                # 描述模式下的一次性 cue 图现在也走原始 WebSocket 事件，同样会被
                # 异步拒绝，所以同样需要这个关联句柄——否则拒绝到达时
                # _on_visual_rejected() 找不到人，快照不会被重新武装。
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
                # 原始 cue 图**根本没送出去**：等发送信号量 / 重压缩超大图期间路由
                # 模式翻转了，或者传输断了，send_event 返回 False。这不是"分析出
                # 空结果"那种终局失败，不能按它处置——那会把一张从未到达 provider
                # 的图记成已消费，本轮还降级成纯文本。与上面原生投递分支同一处置：
                # 摘掉关联句柄、快照留着武装，下一次主动搭话重试。
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
                    # provider 在这一轮的文字注入之前就已经把这张图拒了（原始图
                    # 走 WebSocket 事件，error.event_id 可能比写返回还早到）。
                    # 照常投文字等于让她描述一张根本没进上下文的画面。与上面原生
                    # 投递分支的 `if delivery_rejected:` 同一处置。
                    _remove_visual_rejection_handler()
                    logger.info(
                        "prompt_ephemeral: raw cue image rejected before proactive text inject"
                    )
                    return False
                # 图已经原样送进去了（描述模式下的一次性 cue 图走原生通道）。
                # 只补一句简单引导告诉模型这是什么，不再为它单独跑一次
                # VISION_MODEL 注释——省一次付费调用，也少一层转述失真。
                # 说明文字与下面描述分支同一口径：明示这不是用户说的话。
                events_before_text = ({
                    "type": "conversation.item.create",
                    "item": {
                        "id": f"item_neko_visual_{uuid.uuid4().hex}",
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
                        "id": f"item_neko_visual_{uuid.uuid4().hex}",
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
                    "id": f"item_neko_visual_{uuid.uuid4().hex}",
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
                # 判据是"这一轮到底送没送出原始帧"，不是"处在哪个投递模式"：描述
                # 模式下的一次性 cue 图现在也走原始通道，按模式判会漏掉它——那张
                # 图已经在 provider context 里，快照却还武装着，下一次主动搭话会
                # 把同一张图再发一遍。
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
