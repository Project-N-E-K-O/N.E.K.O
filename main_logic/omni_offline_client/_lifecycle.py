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
import contextlib
import functools
import uuid

from main_logic.agent_event_bus import (
    publish_conversation_turn_observed_best_effort,
)
from main_logic.proactive_delivery import (
    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
    fit_images_to_turn_budget,
)
from utils.llm_client import (
    peek_dialog_slop_lang,
    reset_dialog_slop_lang,
    set_dialog_slop_lang,
)
from utils.slop_filter import resolve_dialog_slop_lang

from ._media import _FRAME_SOURCE_PROACTIVE
from ._shared import (
    AIMessage,
    Callable,
    HumanMessage,
    Optional,
    SystemMessage,
    _is_api_key_rejected_error,
    _llm_retry_error_types,
    _strip_nonverbal_directives,
    asyncio,
    json,
    logger,
    set_call_type,
)


def _with_dialog_slop(method):
    """Arm prompt-only slop reduction for one offline dialog turn."""
    @functools.wraps(method)
    async def _wrapper(self, *args, **kwargs):
        try:
            lang = resolve_dialog_slop_lang(
                getattr(self, "_user_language_provider", None)
            )
        except Exception:
            lang = None
        token = set_dialog_slop_lang(lang)
        try:
            return await method(self, *args, **kwargs)
        finally:
            reset_dialog_slop_lang(token)
    return _wrapper


@contextlib.contextmanager
def _suspend_dialog_slop():
    """Disarm dialog rewriting while an in-turn tool handler runs."""
    token = set_dialog_slop_lang(None)
    try:
        yield
    finally:
        reset_dialog_slop_lang(token)


def _slop_reduced_for_genai(messages):
    """Return a rewritten message copy for the native Gemini SDK path."""
    try:
        lang = peek_dialog_slop_lang()
        if not lang:
            return messages
        from utils.slop_filter import apply_slop_reduction
        return apply_slop_reduction(messages, lang)
    except Exception:
        return messages


# ``turn_type`` on the conversation bus. Two records per ephemeral turn, and
# the pair is what makes the store readable: the instruction is what the model
# was actually handed, the reply is what she actually said in answer to it.
_BUS_TURN_TYPE_INSTRUCTION = "proactive_instruction"
_BUS_TURN_TYPE_REPLY = "proactive_reply"

# ``source`` on those records. ``prompt_ephemeral`` marks every one of its calls
# as a proactive call type (``set_call_type("proactive")`` below) regardless of
# ``completion_mode``, so this is the label that is already true at this layer.
_BUS_CONVERSATION_SOURCE = "proactive"


class _LifecycleMixin:
    async def _publish_conversation_turn(
        self,
        content: str,
        *,
        turn_type: str,
        conversation_id: str,
        message_count: int,
    ) -> bool:
        """Copy one message of this turn onto the plugin conversation bus.

        Best effort, and never raises into the turn -- the dual of
        ``_publish_provider_frames``. A bus that is absent, down or slow must
        not cost the user a greeting. Cancellation is deliberately NOT
        swallowed: that is the session being torn down and it belongs to the
        caller.

        The caller owns the delivery judgement, not this helper. It publishes
        whatever it is handed, so every call site has to have already
        established that the thing being copied really happened: the provider
        streamed a chunk (the instruction), or the reply committed.

        Returns whether the record actually reached the socket. Swallowing the
        failure is right -- it must not reach the turn -- but swallowing it
        *silently* is not: the proactive reply is only allowed onto the bus
        behind its instruction, and "the instruction task finished" says
        nothing about whether the instruction landed. ``False`` covers the
        publisher refusing, the publisher raising, and an empty text that was
        never sent at all.
        """
        text = str(content or "")
        if not text.strip():
            return False
        # __new__-built instances (tests, legacy callers) never ran __init__,
        # read the name the same defensive way the media helpers read it.
        lanlan_name = str(getattr(self, "lanlan_name", "") or "") or None
        try:
            return bool(await publish_conversation_turn_observed_best_effort(
                lanlan_name,
                content=text,
                turn_type=turn_type,
                conversation_id=conversation_id,
                source=_BUS_CONVERSATION_SOURCE,
                message_count=message_count,
            ))
        except asyncio.CancelledError:
            raise
        except Exception as publish_error:
            logger.debug(
                "conversation turn not copied to the plugin bus: %s",
                publish_error,
            )
            return False

    def _begin_response_generation(self) -> int:
        generation = int(getattr(self, "_response_generation", 0)) + 1
        self._response_generation = generation
        self._active_response_generation = generation
        self._is_responding = True
        return generation

    def _response_generation_is_active(self, generation: int) -> bool:
        return (
            getattr(self, "_active_response_generation", None) == generation
            and bool(getattr(self, "_is_responding", False))
        )

    def _pause_response_generation(self, generation: int) -> bool:
        if getattr(self, "_active_response_generation", None) != generation:
            return False
        self._is_responding = False
        return True

    def _resume_response_generation(self, generation: int) -> bool:
        if getattr(self, "_active_response_generation", None) != generation:
            return False
        self._is_responding = True
        return True

    def _finish_response_generation(self, generation: int) -> bool:
        if getattr(self, "_active_response_generation", None) != generation:
            return False
        self._active_response_generation = None
        self._is_responding = False
        return True

    def _cancel_response_generation(self) -> bool:
        if getattr(self, "_active_response_generation", None) is None:
            return False
        self._active_response_generation = None
        self._is_responding = False
        return True

    async def prime_context(self, text: str, skipped: bool = False) -> None:
        """Append context to the system prompt at session start.

        Called during hot-swap to inject incremental conversation cache
        and/or task summaries into a freshly created session.  The *text*
        is concatenated to the existing SystemMessage at position 0 —
        format naturally continues the ``role | text`` lines already
        present in the initial prompt, followed by ``======`` delimiters.

        This method MUST only be called before any user interaction on the
        session (i.e. the conversation history contains only the initial
        SystemMessage from ``connect()``).

        Args:
            text: Context to append (incremental cache + summary/ready).
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if not text or not text.strip():
            return

        if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
            self._conversation_history[0] = SystemMessage(
                content=self._conversation_history[0].content + text
            )
        else:
            # Defensive: should never happen — connect() always sets [0].
            self._conversation_history.insert(0, SystemMessage(content=text))

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Inject a persistent message and trigger an LLM response.

        Appends *instructions* as a HumanMessage to the conversation
        history.  Both the instruction and the LLM's reply persist across
        turns.  This mirrors the OpenAI Realtime API's
        ``conversation.item.create`` (role=user) + ``response.create``
        pattern.

        Unlike ``prime_context`` (system-prompt level, session start only)
        and ``prompt_ephemeral`` (instruction discarded after response),
        messages injected here become permanent conversation history.

        No active callers at present; kept as a stable interface for
        future mid-conversation injection needs.

        Args:
            instructions: Text to inject as a HumanMessage.
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if instructions and instructions.strip():
            self._conversation_history.append(HumanMessage(content=instructions))

    @_with_dialog_slop
    async def prompt_ephemeral(
        self,
        instruction: str,
        *,
        images: Optional[list] = None,
        completion_mode: str = "proactive",
        persist_response: bool = True,
        on_committed: Optional[Callable[[], None]] = None,
        on_committed_text: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Send a fire-and-forget instruction to the LLM and stream the response.

        The *instruction* (typically wrapped in ``======...======`` delimiters)
        is appended as a temporary HumanMessage for this single LLM call
        but is **not** persisted to ``_conversation_history``.  The
        AI's natural-language response (AIMessage) is kept in history only
        when ``persist_response`` is True.

        This is the correct channel for agent task notifications, greeting
        nudges, and any scenario where the AI should respond to a stage
        direction that must not pollute long-term context.

        Unlike ``prime_context`` (appends to system prompt, session start)
        and ``create_response`` (persistent HumanMessage), the instruction
        here is truly ephemeral — it exists only for the duration of this
        single LLM inference call.

        Completion behaviour is caller-selectable:

        - ``completion_mode="proactive"``:
          Uses ``on_proactive_done(content_committed)`` when available.
          This keeps the existing lightweight proactive / agent-callback
          completion path while exposing whether any content was actually
          emitted.
        - ``completion_mode="response"``:
          Uses ``on_response_done()`` so the reply goes through the
          regular user-visible completion path while still keeping the
          injected instruction itself ephemeral.
        - ``on_committed``:
          Called after visible text is confirmed but before completion
          callbacks flush proactive state.
        - ``on_committed_text``:
          Called at the same commit boundary with the sanitized visible text
          (nonverbal directives removed). Callback failures never fail the turn.

        Returns True if any user-visible text was generated, False if aborted
        or only nonverbal directives were emitted.
        """
        if not instruction or not instruction.strip():
            return False

        # A regular visible response stages anti-repeat memory immediately
        # before terminal callbacks. That commit boundary cannot gain an await,
        # so perform the first disk-backed load before generation starts.
        if completion_mode == "response" and persist_response:
            try:
                from memory.anti_repeat import get_anti_repeat_corpus
                await get_anti_repeat_corpus().apreload(self.lanlan_name)
            except Exception as exc:  # pragma: no cover - best-effort cache
                logger.debug("[AntiRepeat] response preload skipped: %s", exc)

        # 临时注入：instruction 已由调用方用 ======== 格式封装，作为 HumanMessage 发送，
        # 不持久化到 _conversation_history，避免污染长期上下文。
        # Proactive media is passed EXPLICITLY via ``images`` (per-callback,
        # carried on cb.media_images by the caller) — it is NOT pulled from
        # self._pending_images. _pending_images is the USER's screen/camera
        # staging queue for the next stream_text; consuming it here would steal
        # the user's pending frame into this proactive/greeting turn and rob the
        # user's next message of its visual context (Codex P2). When proactive
        # images are present we switch to the vision model exactly like
        # stream_text does (一旦带图就永久切 vision — 既定设计；vision model 也能跑
        # 后续纯文本轮). The instruction itself stays ephemeral (not persisted).
        # 在 `if images:` 之外初始化：纯文本的主动轮根本不进这个分支，而下面
        # 的 emit 点无条件要读它。放在分支里的后果不是函数直接报错，而是文本已经发给
        # 用户了、函数却因 UnboundLocalError 走到失败分支返回 False，主动
        # 调度状态跟着被污染（CodeRabbit）。
        _pending_budget_notice = None
        # 这一轮的身份。帧记录的 turn_id 和两条对话记录的 conversation_id 用**同
        # 一个值**，插件才能把「模型看到的那几张图」和「她因此说了什么」拼回同一
        # 轮。主动轮没有外部 turn id（那是独立 ASR 那条路才有的），只能现铸一个。
        _bus_turn_id = uuid.uuid4().hex
        # 阶梯之后真正附上的那批帧。在 `if images:` 之外初始化 —— 纯文本的主动轮
        # 根本不进那个分支，而下面的发布点无条件要读它。
        _bus_frames: list = []

        async def _emit_pending_budget_notice() -> None:
            """Send the staged trim notice, at most once, once the turn speaks.

            Called from EVERY path that delivers visible text -- both the
            per-chunk emit and the end-of-stream prefix flush. The flush is not
            a rare corner: any reply shorter than ``_prefix_buffer_size`` is
            delivered entirely by it, and an inline copy of this block on the
            chunk path alone silently skipped the notice for exactly those
            turns while still committing them (Codex). Anything added later
            that emits visible text has to call this too.
            """
            nonlocal _pending_budget_notice
            if _pending_budget_notice is None:
                return
            payload = _pending_budget_notice
            # Clear BEFORE awaiting: the emit paths run per delta, and a slot
            # still set while the send is in flight re-fires on the next one.
            _pending_budget_notice = None
            if not self.on_status_message:
                return
            try:
                await self.on_status_message(json.dumps({
                    "code": "TURN_IMAGES_TRIMMED",
                    "details": payload,
                }))
            except Exception as _notice_error:
                logger.warning(
                    "could not report the proactive image trim to the user: %s",
                    _notice_error,
                )

        if images:
            # 一旦带图就永久切到 vision model（既定设计，见上）。vision model 也能
            # 跑后续纯文本轮，且凝神不再因 vision 而关闭思考。
            if self.vision_model and self.vision_model != self.model:
                logger.info(
                    f"🖼️ prompt_ephemeral: switching to vision model {self.vision_model} (from {self.model}) for proactive media"
                )
                await self.switch_model(self.vision_model, use_vision_config=True)
            # 走和 stream_text 同一条预算阶梯：归一化到模型档位 → 抽样 → 重压 →
            # 最后才丢。这条路以前是仓库里**唯一**一个带图却完全没有预算闸的模型
            # 调用——images 里的每一张都逐条原样贴成 data URL 就发出去了。
            # 而它恰恰是最容易堆量的一条：调用方（core/proactive.py）把这一批
            # 里**所有** callback 的 media_images 拼在一起传进来，每个 callback
            # 自己已经能带到 8 张，合批之后没有任何人再看总量。它和用户轮共用同
            # 一个 provider、同一个单请求上限，超了是整条请求被拒——只是这里被拒
            # 的后果更隐蔽：用户根本不知道刚才有一轮主动搭话没发出去。
            try:
                _budget_images, _budget_notice = await fit_images_to_turn_budget(
                    images,
                    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
                )
            except Exception as _fit_error:
                # 阶梯内部已经逐张兜底（单张失败就原样留着），能漏到这里的只剩
                # 编程错误。这种情况下按原样送出，而不是让异常炸穿 prompt_ephemeral
                # ——最坏是 provider 拒一条主动搭话，正好落在下面那条「失败就静默
                # 放弃」的既定语义里；而炸穿会连带把调用方的 proactive 状态机
                # 一起掀了。
                logger.warning(
                    "prompt_ephemeral: 图片预算阶梯异常，按原样附图: %s",
                    _fit_error,
                )
                _budget_images, _budget_notice = list(images), None
            if _budget_notice:
                # 与 _streaming.py 同一判据：真丢了东西才 warning，纯归一化走
                # info。rung 0 无条件执行，主动轮又是自发的，全按 warning 打等于
                # 让「图小了一点」和「有几张没送出去」在日志里长得一模一样。
                _budget_log = (
                    logger.warning
                    if _budget_notice.get("user_visible")
                    else logger.info
                )
                _budget_log(
                    "prompt_ephemeral images fitted for the %d-byte budget: "
                    "%d -> %d image(s) (normalized=%s sampled=%s compressed=%s dropped=%d)",
                    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
                    _budget_notice["original_count"],
                    _budget_notice["final_count"],
                    _budget_notice.get("normalized"),
                    _budget_notice["sampled"],
                    _budget_notice["compressed"],
                    _budget_notice["dropped"],
                )
                # 这里确实向前端弹了一条 —— 和下面「主动搭话失败静默吞掉」的立场
                # 不冲突，因为两者说的不是一回事：
                #   * 那条立场管的是**这一轮没能发生**。用户没在等回复，也从不知道
                #     本来会有这么一轮，告诉他「刚才有句话没说出来」只是制造焦虑。
                #   * 这里管的是**这一轮发生了，但内容缺了一块**。角色接下来会围着
                #     她看到的图讲话，而被丢掉的那几张用户很可能还看得见——插件推
                #     的图只要带 visibility=["chat"] 就同时渲染进了聊天气泡。不说，
                #     用户面对的就是「她怎么对着这张图答非所问」，一个他无从解释、
                #     只会归因于模型变笨的现象。
                # 判据本身仍然是全仓统一的那条：只有**整张图被丢掉**才打扰用户，
                # 归一化 / 抽样 / 重压一律只进日志（见 fit_images_to_turn_budget）。
                #
                # 但**暂存**，不在这里发。上面那段论证有个它自己没写出来的前提：
                # 「这一轮发生了」。主动轮可能被取消、也可能 retry 用尽后按既定
                # 立场静默放弃，这时先发出去的裁剪提示就成了「为一次从未发生的
                # 回复报告它缺了什么」—— 用户看到一条孤零零的「图片已调整」，
                # 而屏幕上根本没有与之相关的发言，比不提示更费解（Codex）。
                # 改为挂起，等真正 emit 出第一段可见文本时再补发；那一刻
                # 「这一轮发生了」才第一次成立。
                _pending_budget_notice = (
                    _budget_notice if _budget_notice.get("user_visible") else None
                )
            if _budget_images:
                _ephemeral_content: list = []
                for img_b64 in _budget_images:
                    _ephemeral_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    })
                _ephemeral_content.append({"type": "text", "text": instruction})
                logger.info(
                    f"prompt_ephemeral: attaching {len(_budget_images)} proactive image(s)"
                )
                _ephemeral_msg = HumanMessage(content=_ephemeral_content)
                # 只**存**，不发。存的是阶梯之后的字节（不是调用方给的原图）：
                # 归一化几乎每轮都会重编码，总线上必须是模型真正看到的那一张。
                # 发布点在下面「第一个 chunk 到达」处 —— 与 stream_text 同一条
                # 判据。这里发布就是在赌：本函数往下还有 retry 阶梯、取消检查、
                # 三次 attempt 全失败的静默放弃，每一条都能让这一轮一个字节都没
                # 到过 provider。
                _bus_frames = list(_budget_images)
            else:
                # 阶梯永远至少保住最后一张，所以走到这里只可能是调用方传了一串空
                # 字符串。退回纯文本消息，别发一条只有 text block 的多模态壳子。
                _ephemeral_msg = HumanMessage(content=instruction)
        else:
            _ephemeral_msg = HumanMessage(content=instruction)
        messages_to_send = self._conversation_history + [_ephemeral_msg]
        # 送达之后要抄给插件总线的东西：这一轮的指令，以及（若有）真正附上的那批
        # 图。一个槽装两样，是为了让「一轮只发一次」只有一处清标记 —— 两个槽两处
        # 清，就是下一次有人只清了其中一个的地方。None = 已经发过了。
        _pending_bus_delivery = (instruction, _bus_frames)
        # 指令那条抄送的任务句柄。回复在 finally 末尾发布，两条属于同一轮，
        # 插件读到的顺序必须是「指令、回复」——发布一旦离开回合的返回路径，
        # 这个顺序就只剩调度保证，而调度不保证任何事。
        _bus_instruction_task = None
        # 这一轮的工具图槽位，跨 attempt 存活（见 _astream_visible_with_tools）。
        _turn_tool_image_slots: list = []
        # 同上：跨 attempt 存活的待抄送工具帧。
        _turn_tool_bus_frames: list = []

        # Retry 策略与 stream_text 对偶（max_retries=3, [1, 2]s 间隔）。
        # 但主动搭话语义不同：用户没在等回复，retry 用尽时**静默吞掉**，
        # 不发任何 status_message 给前端 —— 失败 = 这一轮 AI 根本没想说话。
        # 唯一例外：欠费 / API Key / 配额这类账户级错误必须上报，否则用户
        # 永远不知道为什么主动搭话不工作。
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""
        # Empty-completion 诊断重置：与 stream_text 对偶。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None
        # Open a new reasoning-pulse scope like stream_text does and capture the
        # ownership token: the finally clear below must fire ONLY for this turn's
        # own pulse, never for a newer user stream_text that interleaved and
        # re-pulsed under a fresher seq (Codex P2).
        _reasoning_owner_seq = self._begin_reasoning_stream()
        response_generation = self._begin_response_generation()

        try:
            set_call_type("proactive")
            for attempt in range(max_retries):
                # 每次 attempt 重置流式状态（assistant_message / prefix /
                # is_first_chunk 全部归零）。
                assistant_message = ""
                is_first_chunk = True
                prefix_buffer = ""
                prefix_checked = not bool(self._prefix_buffer_size)
                emitted_any = False  # 本 attempt 是否已经向前端 emit 过文本

                # close() 是唯一会把 self.llm 设为 None 的路径。它若在前一次
                # APIConnectionError 后的 retry sleep 期间触发（用户切模式 /
                # 断连 / session 熔断），不再做这次 attempt —— 否则会对 None
                # 调 .astream 触发 AttributeError，且就算重试 client 也已不在。
                # 用 hasattr 守卫：单元测试用 __new__ 绕过 __init__ 不会设这个
                # 属性，但真实代码 __init__ 必设。
                if (
                    (hasattr(self, "llm") and self.llm is None)
                    or not self._response_generation_is_active(response_generation)
                ):
                    break

                try:
                    # 主动搭话同样走 tool-aware streaming —— agent 注入的 stage
                    # direction 也可能让模型决定调用工具（比如 "讲一下今天天气"）。
                    async for chunk in self._astream_visible_with_tools(
                        messages_to_send,
                        # 与 stream_text 同：跨 attempt 存活，由下面的 finally
                        # 统一释放。
                        _tool_image_slots=_turn_tool_image_slots,
                        _tool_bus_frames=_turn_tool_bus_frames,
                        # 这一轮里工具返回的图也归这一轮：没有它，主动搭话轮
                        # 里的工具图会以 turn_id=None 上总线，插件没法把它和
                        # 同一轮的指令/回复对上——而那正是 turn_id 存在的理由。
                        _tool_frames_turn_id=_bus_turn_id,
                    ):
                        # 插件总线：provider 已经吐出东西了 —— 这一轮的指令连同那
                        # 批图确凿地被它收下了。这是本函数里最早能这么断言的地方，
                        # astream 是惰性的，请求要到第一次 __anext__ 才真正发出去。
                        # 清标记在 await 之前：一轮只发一次，三次 attempt 的重试不
                        # 会把同一批图、同一条指令再抄一遍。
                        #
                        # 刻意在下面那句 `_response_generation_is_active` 之前：那
                        # 是**本地**取消，与 provider 收没收到无关。它已经收到了。
                        if _pending_bus_delivery is not None:
                            _bus_instruction, _bus_frames_to_send = _pending_bus_delivery
                            _pending_bus_delivery = None
                            # 与 stream_text 同一判据：抄送不占用回复的返回
                            # 路径。两者都在上面冻结过了，任务里不读活状态。
                            if _bus_frames_to_send:
                                self._fire_bus_task(
                                    self._publish_provider_frames(
                                        _bus_frames_to_send,
                                        [_FRAME_SOURCE_PROACTIVE]
                                        * len(_bus_frames_to_send),
                                        turn_id=_bus_turn_id,
                                    )
                                )
                            _bus_instruction_task = self._fire_bus_task(
                                self._publish_conversation_turn(
                                    _bus_instruction,
                                    turn_type=_BUS_TURN_TYPE_INSTRUCTION,
                                    conversation_id=_bus_turn_id,
                                    message_count=1,
                                )
                            )
                        if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                            logger.debug(f"🔍 [Usage-Proactive] {chunk.usage_metadata}")
                        if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                            if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                logger.debug(f"🔍 [Meta-Proactive] {chunk.response_metadata}")

                        if not self._response_generation_is_active(response_generation):
                            break
                        content = chunk.content if hasattr(chunk, "content") else str(chunk)
                        if content and content.strip():
                            emit_content = content

                            # ── 前缀检测阶段：缓冲初始输出，剥离角色名前缀 ──
                            if not prefix_checked:
                                prefix_buffer += emit_content
                                if len(prefix_buffer) >= self._prefix_buffer_size:
                                    prefix_checked = True
                                    master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                                    lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                                    if master_match:
                                        logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离主人名前缀 '{prefix_buffer[:master_match]}'")
                                        emit_content = prefix_buffer[master_match:]
                                    elif lanlan_match:
                                        logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                        emit_content = prefix_buffer[lanlan_match:]
                                    else:
                                        emit_content = prefix_buffer
                                    if not (emit_content and emit_content.strip()):
                                        continue
                                else:
                                    continue  # 缓冲区未满，等更多 chunk

                            assistant_message += emit_content
                            if self.on_text_delta:
                                await self.on_text_delta(emit_content, is_first_chunk)
                            is_first_chunk = False
                            emitted_any = True
                            # 这一轮确实开口了，挂起的裁剪提示现在才该出现。
                            await _emit_pending_budget_notice()

                    # ── flush 前缀缓冲区（流提前结束时） ──
                    if prefix_buffer and not prefix_checked:
                        prefix_checked = True
                        master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                        lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                        if master_match:
                            logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离主人名前缀")
                            flush_text = prefix_buffer[master_match:]
                        elif lanlan_match:
                            logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离角色名前缀")
                            flush_text = prefix_buffer[lanlan_match:]
                        else:
                            flush_text = prefix_buffer
                        if flush_text and flush_text.strip():
                            assistant_message += flush_text
                            if self.on_text_delta:
                                await self.on_text_delta(flush_text, is_first_chunk)
                            is_first_chunk = False
                            emitted_any = True
                            # 短于前缀缓冲阈值的回复整段走这条路，chunk 分支
                            # 一次都不进 —— 少了这一行，那类回合会照常提交却
                            # 永远不报裁剪。
                            await _emit_pending_budget_notice()

                    break  # 流正常结束，跳出 retry 循环

                except _llm_retry_error_types() as e:
                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    logger.info(f"ℹ️ prompt_ephemeral 捕获到 {error_type} 错误")

                    # 账户级错误必须上报：欠费 / API Key 直接放弃 retry，
                    # 配额错误上报后继续 retry（与 stream_text 对偶）。
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"prompt_ephemeral: 检测到欠费错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                        assistant_message = ""
                        return False
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                        assistant_message = ""
                        return False
                    elif 'quota' in error_str_lower or 'time limit' in error_str_lower:
                        logger.warning(f"prompt_ephemeral: 检测到配额错误，上报前端: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_QUOTA_TIME"}))

                    # 已经吐过文本就不能再 retry —— 否则前端会拼出"半截 + 重新生成"
                    # 的怪异回复。直接 break 让半截文本走 finally 的 persist 路径。
                    if emitted_any:
                        logger.info(
                            "prompt_ephemeral: %s 发生时已 emit 文本，放弃 retry",
                            error_type,
                        )
                        break

                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(
                            "prompt_ephemeral: LLM 调用失败 (尝试 %d/%d)，%d 秒后重试: %s",
                            attempt + 1, max_retries, wait_time, error_type,
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    # Retry 用尽：B 部分语义 —— 静默放弃。主动搭话失败用户
                    # 不需要知道，只 log 一条 warning（截断 str(e) 防 HTML
                    # 错误页淹没日志）。
                    logger.warning(
                        "prompt_ephemeral: %s 重试 %d 次后仍失败，静默放弃: %s",
                        error_type, max_retries, str(e)[:200],
                    )
                    assistant_message = ""
                    return False
        except Exception as e:
            if _is_api_key_rejected_error(e):
                logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                assistant_message = ""
                return False
            # 兜底：非 API 错误（编程错误 / 数据异常）静默吞掉，截断错误文本
            # 防 HTML 错误页之类淹没日志。和上方 (APIConnectionError 等) 分支
            # 语义对偶 —— 都不向前端发 status_message。
            logger.error(
                "OmniOfflineClient.prompt_ephemeral 未分类异常 %s: %s",
                type(e).__name__, str(e)[:200],
                exc_info=True,
            )
            assistant_message = ""
            return False
        finally:
            # 先于其它收尾：把 base64 从历史里摘掉。跨 attempt 存活的代价
            # 就是必须由这里统一释放，否则它会跟着这一轮之后的每次请求走。
            self._release_tool_image_slots(_turn_tool_image_slots)
            self._finish_response_generation(response_generation)
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            committed_text = _strip_nonverbal_directives(assistant_message).strip()
            content_committed = bool(committed_text)
            # 一条可见的 ephemeral 回复（greeting / agent 回调 / 戳头像的 quip）是
            # 用户接下来要回应的「新一条 AI 轮」，它让之前为「下一条用户回复」暂存的
            # 屏幕截图过时——清掉它。persist_response=False 的回复（如头像 quip）不进
            # 历史、历史长度不变，stream_text 的 history-len marker 看不到，必须在这条
            # ephemeral 回复的 choke point 清（Codex P2）。只在真吐了可见文本时清，
            # 半途 abort / 无文本的尝试不丢一张仍有效的暂存屏。
            if content_committed:
                self._proactive_image_to_inject = None
                self._proactive_image_staged_at = 0.0
                self._proactive_image_history_len = 0
            # Empty-completion 诊断：和 stream_text 的兜底 warning 对偶。
            # 主动搭话语义上是"静默放弃"，所以不发 status_message，但 INFO
            # 一行 finish_reason 让日志能复盘——上次出问题就是因为没法区分
            # "trigger_greeting 静默失败 = LLM 被 safety 拦" vs "LLM 真的觉得
            # 这一轮不该说话"。
            if not content_committed:
                logger.info(
                    "OmniOfflineClient.prompt_ephemeral: 无可提交文本 "
                    "(finish_reason=%s block_reason=%s prompt_tokens=%s model=%s "
                    "completion_mode=%s)",
                    getattr(self, "_last_finish_reason", None),
                    getattr(self, "_last_block_reason", None),
                    getattr(self, "_last_prompt_tokens", None),
                    getattr(self, "model", None),
                    completion_mode,
                )
            else:
                if on_committed_text:
                    try:
                        on_committed_text(committed_text)
                    except Exception:
                        logger.exception(
                            "prompt_ephemeral on_committed_text callback failed"
                        )
                if on_committed:
                    try:
                        on_committed()
                    except Exception:
                        logger.exception("prompt_ephemeral on_committed callback failed")
            if content_committed and persist_response:
                self._conversation_history.append(AIMessage(content=assistant_message))
            # 防复读 corpus 拆成两半：内存更新在收尾信号**之前**（同步，不含 await，
            # 所以不是取消点），落盘在**之后**。客户端看到 turn end 就可能立刻发下一
            # 条，那一轮的打分必须已经看得到刚提交的这句；而落盘那个 await 一旦被取消
            # 就会跳过 on_response_done 里的 TTS 收尾 / turn 结束 / request-id 清理。
            # 两个要求方向相反，只有拆开才能同时满足。
            staged_anti_repeat = None
            if completion_mode == "response" and content_committed and persist_response:
                try:
                    from memory.anti_repeat import get_anti_repeat_corpus
                    staged_anti_repeat = get_anti_repeat_corpus().stage_output(
                        self.lanlan_name, committed_text, is_proactive=False,
                    )
                except Exception as _exc:  # pragma: no cover
                    logger.debug("[AntiRepeat] stage reply skipped: %s", _exc)
            # Everything above is synchronous commit-point bookkeeping.  Keep it
            # before the first cleanup await so cancellation cannot make visible
            # text disappear from callbacks/history while still reaching the user.
            # Passing the owner seq suppresses the reasoning-bubble clear when a
            # newer user turn interleaved and re-pulsed; the call is otherwise
            # idempotent when nothing pulsed or the first token already cleared it.
            await self._notify_reasoning_done(_reasoning_owner_seq)
            if completion_mode == "response":
                if self.on_response_done:
                    await self.on_response_done()
                # 只录常规 reply（completion_mode == "response"）。proactive 路径
                # 已经在 ``core.finish_proactive_delivery`` 上录，这里再录会双写。
                # 与 core.finish_proactive_delivery 同因同治：摘下来不 await。下面的
                # `return content_committed` 是调用方判断这轮有没有提交的依据，在它
                # 之前留一个取消点，就会让一次已经发出去的回复被记成没发。
                if staged_anti_repeat is not None:
                    try:
                        from memory.anti_repeat import get_anti_repeat_corpus
                        get_anti_repeat_corpus().flush_staged_detached(staged_anti_repeat)
                    except Exception as _exc:  # pragma: no cover
                        logger.debug(
                            "[AntiRepeat] flush reply skipped: %s", _exc,
                        )
            else:
                proactive_done_cb = getattr(self, "on_proactive_done", None)
                if proactive_done_cb:
                    await proactive_done_cb(content_committed)
                elif self.on_response_done:
                    await self.on_response_done()
            # 对话总线的第二条：她真正说出口的那句。判据和上面那条指令不同 ——
            # 指令问的是「provider 收到了吗」（第一个 chunk），这条问的是「她说了
            # 吗」（提交）。开始 streaming 不算数：只吐了非语言指令、或者半截被
            # 丢弃的那种回合，committed_text 是空的，插件此时该读到的是「没有回
            # 复」，而不是一条空记录或一句从未被承认的话。发的是 committed_text
            # ——剥掉 [play_music:] 之后的那份，与 on_committed_text / 防复读
            # corpus 拿到的是同一个字符串。
            #
            # 位置在整个 finally 的**最后**，收尾回调之后：这是一次礼节性抄送，
            # 不能在任何用户看得见的东西（TTS 收尾、轮次结束、request-id 清理）
            # 前面新开一个取消点。
            if content_committed and _bus_instruction_task is not None:
                # 顺序靠**串联**，不靠等待。指令和回复是同一轮，插件必须按这个
                # 顺序读到；但在 finally 里 await 那条任务，会让一个卡住的
                # bridge 直接挂住主动搭话的收尾——这正是上一轮把发布挪出返回
                # 路径要避免的事，在这里又长回来了。
                #
                # 所以把回复的抄送挂在指令那条任务**后面**，整串一起 fire：
                # 顺序保住了，回合一步都不用等，也不用为此发明一个超时。
                #
                # 任务为 None 时**整条回复都不发**（上面的判据）。None 有两种来
                # 源，而两种的结论一样：要么这一轮压根没走到指令发布点，要么在
                # 途抄送已经到顶、指令被拒——两种情况下总线上都没有那条指令，
                # 而带着 message_count=2 的回复会告诉插件「你手上是完整的一轮」。
                # 那是一句撒谎的记录，比少一条记录糟得多。
                self._fire_bus_task(
                    self._publish_reply_after_instruction(
                        _bus_instruction_task,
                        committed_text,
                        turn_type=_BUS_TURN_TYPE_REPLY,
                        conversation_id=_bus_turn_id,
                        # 这一轮到此为止总共两条：指令 + 这句回复。读到这条的
                        # 插件因此知道自己手上的是完整的一轮。
                        message_count=2,
                    )
                )

        return content_committed

    async def cancel_response(self) -> None:
        """Cancel the current response if possible"""
        self._cancel_response_generation()

    async def _cancel_external_voice_submit_task(self) -> bool:
        """Cancel the narrow external-ASR child task, if another task owns it."""

        submit_task = getattr(self, "_external_voice_submit_task", None)
        if (
            submit_task is None
            or submit_task is asyncio.current_task()
        ):
            return False
        if not submit_task.done():
            submit_task.cancel()
        await asyncio.gather(submit_task, return_exceptions=True)
        if getattr(self, "_external_voice_submit_task", None) is submit_task:
            self._external_voice_submit_task = None
        return True

    async def handle_interruption(self):
        """Handle user interruption - cancel current response"""
        if await self._cancel_external_voice_submit_task():
            logger.info("Cancelling pending external voice submit")
        if not self._is_responding:
            return

        logger.info("Handling text mode interruption")
        await self.cancel_response()

    async def handle_messages(self) -> None:
        """
        Compatibility method for OmniRealtimeClient interface.
        In text mode, this is a no-op as we don't have a persistent connection.
        """
        # Keep this task alive to match the interface
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Text mode message handler cancelled")

    async def _publish_reply_after_instruction(
        self,
        instruction_task,
        content: str,
        **kwargs,
    ) -> None:
        """Publish the proactive reply, but never before its instruction.

        The two are one round and a plugin reads them in order. Waiting for the
        instruction on the turn itself would let a stalled bridge hang the
        turn's teardown, so the wait lives here, inside the copy that is
        already off the response path.

        The instruction task swallows its own failures, so this only ever ends
        when it does -- and if it is cancelled (``close()``), that cancellation
        propagates and this reply is dropped with it. Correct: a reply on the
        bus with no instruction in front of it reads as a sentence with no
        cause.

        Waiting is not enough, though: the task **finishing** says nothing
        about whether the instruction was even sent. The publisher swallows a
        refusal and a raise alike, so the result is read here and a reply whose
        instruction never reached the socket is dropped rather than sent out
        carrying ``message_count=2``.

        This gate reaches only as far as the socket, and must not be read as
        "no orphan reply is possible". ``publish_conversation_turn_observed
        _best_effort`` says so itself: its ``True`` means "handed to the
        socket", never "a plugin will see it". Three hops follow -- the
        agent-side ``_forward_conversation_turn``, the bridge, and ingest -- and
        the reply has already been released by the time any of them can drop
        the instruction. Closing that window for good needs an acknowledgement
        from the store hop, or publishing the pair atomically; neither belongs
        in this change.

        ``instruction_task`` is never ``None`` here -- the caller drops the
        whole reply in that case rather than publishing an orphan. The guard
        stays as a belt for a direct caller, and it is the reason the caller's
        check cannot be relaxed into "wait if we have one".
        """
        if instruction_task is not None:
            if not await instruction_task:
                logger.debug(
                    "proactive reply not copied: its instruction never reached "
                    "the bus",
                )
                return
        await self._publish_conversation_turn(content, **kwargs)

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        # ``_cancel_bus_copies`` latches before it drains, which is what makes
        # this safe: draining alone races -- a stream parked on its first chunk
        # wakes up after the drain, fires a fresh copy, and that one outlives
        # the closed session, the exact thing the drain exists to prevent.
        await self._cancel_bus_copies()
        await self._cancel_external_voice_submit_task()
        # Supersedes the bare ``_is_responding = False``: retiring the active
        # generation also stops a mid-flight turn from resuming (its
        # ``_resume_response_generation`` no longer matches) against a closed
        # client.
        self._cancel_response_generation()
        self._conversation_history = []
        self._pending_images.clear()
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0
        if self.llm:
            try:
                await self.llm.aclose()
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: aclose failed: {e}")
            self.llm = None
        # 同 switch_model：genai.Client 持有 httpx 连接池，关掉它的
        # 同步 close()（SDK 没暴露 aclose，放 to_thread 不阻事件循环）。
        if self._genai_client is not None and hasattr(self._genai_client, "close"):
            try:
                await asyncio.to_thread(self._genai_client.close)
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: genai client close failed: {e}")
            self._genai_client = None
        self._genai_tools_unsupported = False
        logger.info("OmniOfflineClient closed")
