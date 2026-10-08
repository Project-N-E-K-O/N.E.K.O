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
import inspect
import uuid

from main_logic.agent_event_bus import (
    publish_conversation_turn_observed_best_effort,
)
from main_logic.proactive_delivery import (
    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
    fit_images_to_turn_budget,
)
from main_logic.session_state import session_reply_in_progress
from utils.llm_client import (
    peek_dialog_slop_lang,
    reset_dialog_slop_lang,
    set_dialog_slop_lang,
)
from utils.slop_filter import resolve_dialog_slop_lang

from ._media import _FRAME_SOURCE_PROACTIVE
from ._shared import (
    AIMessage,
    Any,
    Awaitable,
    Callable,
    HumanMessage,
    Optional,
    SystemMessage,
    _REPLY_GENERATION_ATTR,
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


async def _close_genai_client(genai_client) -> None:
    """Close both halves of a ``genai.Client``.

    Replies stream on ``genai_client.aio``, whose async httpx client only its
    own ``aclose()`` releases; ``close()`` (sync, so run in a thread) releases
    just the sync one. Both run even if the first one fails.
    """
    aio = getattr(genai_client, "aio", None)
    try:
        if aio is not None and hasattr(aio, "aclose"):
            await aio.aclose()
    finally:
        if hasattr(genai_client, "close"):
            await asyncio.to_thread(genai_client.close)


async def _retire_replaced_clients(client, closers) -> None:
    """Close the clients ``switch_model`` replaced on ``client``, once nothing
    can still be streaming on them.

    ``closers`` are the async callables that close them. A reply call keeps
    streaming on the client it read when it sent its request, so a switch
    made meanwhile by another call (a proactive or user turn's vision switch,
    a tool image's) must not close that client under it. While any reply call
    is in flight the closes wait; the last call to return runs them
    (``_tracked_reply_call``), and ``close()`` runs any still waiting.
    """
    waiting = getattr(client, "_retired_client_closers", None)
    if waiting is None:
        waiting = client._retired_client_closers = []
    waiting.extend(closers)
    if not getattr(client, "_reply_calls_in_flight", 0):
        await _close_retired_clients(client)


async def _close_retired_clients(client) -> None:
    """Run the closes waiting on ``client`` (``_retire_replaced_clients``).

    Takes the batch waiting now; closes queued while it runs wait for the
    next sweep, since a reply call that started meanwhile may be streaming
    on those clients. When this is cancelled midway (``close()`` or an
    interruption cancelling the task that runs it), the closes not yet
    started go back to the front of the queue, for ``close()``'s own sweep
    or the next time no reply call is in flight.
    """
    closers = getattr(client, "_retired_client_closers", None)
    if not closers:
        return
    client._retired_client_closers = []
    try:
        while closers:
            close = closers.pop(0)
            try:
                await close()
            except Exception as e:
                logger.warning("OmniOfflineClient: closing a replaced client failed: %s", e)
    finally:
        if closers:
            waiting = getattr(client, "_retired_client_closers", None)
            if waiting is None:
                waiting = client._retired_client_closers = []
            waiting[:0] = closers


def _tracked_reply_call(method):
    """Count a ``stream_text`` / ``prompt_ephemeral`` call as reply work in
    flight on this client, from its entry (before its pre-generation awaits)
    until it has fully returned (after its completion callback and its whole
    finally, including when either raises or is cancelled).

    A cancelled generation keeps running after the interruption let go of it
    (a tool handler, its cancelled-reply history commit), and a claimed one
    is still inside its cleanup; ``is_idle`` reads both as busy through this
    count. When the last call returns, ``on_idle`` is told: an interrupted
    reply's owed wrap-up has no completion of its own to ride on. Then the
    clients ``switch_model`` replaced meanwhile are closed: no call is left
    that can still be streaming on one (``_retire_replaced_clients``).
    """
    @functools.wraps(method)
    async def _wrapper(self, *args, **kwargs):
        self._reply_calls_in_flight = int(getattr(self, "_reply_calls_in_flight", 0)) + 1
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._reply_calls_in_flight = max(0, self._reply_calls_in_flight - 1)
            if not self._reply_calls_in_flight:
                self._notify_idle()
                await _close_retired_clients(self)
    return _wrapper


class InterruptedReply(str):
    """A reply whose close was handed over, as the kind of turn end its
    skipped completion would have sent ("response" / "agent_callback").

    ``finished`` is True only for a claimed completion of a reply that was
    not cut: it streamed to the end and nothing newer has streamed since, so
    the frontend's current bubble is still its own and may be sealed with
    the normal turn end. A cancelled (live or guard-paused) reply is not
    finished. ``owner`` is the opaque token the caller handed to the reply
    (``reply_owner``), so its closer closes that reply rather than whatever
    the caller's shared per-turn state holds by then; None when the reply was
    started without one. A ``str``, so every ``== "response"`` / truthiness
    check holds.
    """

    finished: bool
    owner: Any

    def __new__(cls, kind: str, *, finished: bool = False, owner: Any = None):
        obj = super().__new__(cls, kind or "response")
        obj.finished = bool(finished)
        obj.owner = owner
        return obj


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
            published = bool(await publish_conversation_turn_observed_best_effort(
                lanlan_name,
                content=text,
                turn_type=turn_type,
                conversation_id=conversation_id,
                source=_BUS_CONVERSATION_SOURCE,
                message_count=message_count,
            ))
            return published
        except asyncio.CancelledError:
            raise
        except Exception as publish_error:
            logger.debug(
                "conversation turn not copied to the plugin bus: %s",
                publish_error,
            )
            return False

    def _begin_response_generation(
        self, completion_kind: str = "response", owner: Any = None,
    ) -> int:
        """Start a generation. ``completion_kind`` names the turn end its
        completion sends ("response", or "agent_callback" for a proactive
        reply completed by ``on_proactive_done``); an interruption reports it
        so the interrupted turn is closed the same way.

        Every generation is closed exactly once: by its own completion, or,
        when that is skipped, by whoever took the close over -- an
        interrupter (``handle_interruption``) or the generation that
        displaced it here. A user reply can begin over one still live,
        guard-paused or waiting on its completion without any interruption
        in between (the text path's setup awaits, an independent-ASR submit
        at the final). That one is taken over the same way and reported to
        ``on_response_displaced`` synchronously, before this generation can
        emit anything, so its owner closes it while the AI-turn buffer still
        holds only its text. Whatever the owner still has to send (it may
        return an async callable) is awaited by ``_run_displaced_followup``
        right after the begin, before this reply sends anything.
        (``prompt_ephemeral`` never displaces: it declines to begin over
        another reply.)
        """
        displaced = self._take_displaced_reply()
        generation = int(getattr(self, "_response_generation", 0)) + 1
        self._response_generation = generation
        self._active_response_generation = generation
        self._active_completion_kind = completion_kind
        self._active_reply_owner = owner
        self._is_responding = True
        self._displaced_followup = None
        if displaced:
            notify = getattr(self, "on_response_displaced", None)
            if notify is not None:
                try:
                    followup = notify(displaced)
                except Exception:
                    logger.exception("on_response_displaced callback failed")
                else:
                    if callable(followup):
                        self._displaced_followup = followup
        return generation

    async def _run_displaced_followup(self) -> None:
        """Await what the displaced reply's owner still has to send (its
        frontend turn end, or ``turn abandoned``), before this reply sends
        anything: the begin runs ``on_response_displaced`` synchronously and
        can only take that part back from it."""
        followup = getattr(self, "_displaced_followup", None)
        self._displaced_followup = None
        if followup is None:
            return
        try:
            pending = followup()
            if inspect.isawaitable(pending):
                await pending
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("on_response_displaced follow-up failed")

    def _take_displaced_reply(self) -> str:
        """Take over the reply a beginning generation displaces; "" if none.

        The same hand-over as ``handle_interruption``: a live or guard-paused
        generation is recorded as handed over (its own generation check stops
        it and its completion does not run), a finished one waiting on its
        completion is claimed. The begin that follows owns the busy flag.
        """
        live = getattr(self, "_active_response_generation", None)
        if live is not None:
            self._record_handed_over(live)
            return InterruptedReply(
                getattr(self, "_active_completion_kind", "response") or "response",
                owner=getattr(self, "_active_reply_owner", None),
            )
        return self._claim_pending_completion()

    def _record_handed_over(self, generation: int) -> None:
        owned = getattr(self, "_interrupter_owned_generations", None)
        if owned is None:
            owned = self._interrupter_owned_generations = set()
        owned.add(generation)

    def _claim_pending_completion(self) -> str:
        """Claim a finished generation's completion before it runs; "" if
        none is pending. The window's busy flag was that generation's
        (``_mark_completion_pending``) and is retired with it: its own
        ``_take_completion`` finds the mark gone and leaves the flag alone."""
        if getattr(self, "_completion_pending_generation", None) is None:
            return ""
        claimed = InterruptedReply(
            getattr(self, "_completion_pending_kind", "response") or "response",
            finished=getattr(self, "_completion_pending_finished", True),
            owner=getattr(self, "_completion_pending_owner", None),
        )
        self._close_completion_window()
        if getattr(self, "_active_response_generation", None) is None:
            self._is_responding = False
        return claimed

    def _close_completion_window(self) -> None:
        """End the completion window (claimed, or taken by its own
        generation) together with the owner it was opened with, so a closed
        reply's ``_ReplyTurn`` and its meta are not kept reachable."""
        self._completion_pending_generation = None
        self._completion_pending_owner = None

    def has_reply_in_progress(self) -> bool:
        """A reply is live, guard-paused or waiting on its completion.

        What a proactive turn must not start over: ``prompt_ephemeral``
        declines on it, and the core's proactive gates read the same check
        (``session_reply_in_progress``) before they rotate the speech id.
        Narrower than ``is_idle``, which also counts reply calls that are
        only still returning."""
        return session_reply_in_progress(self)

    def _declines_over_another_reply(self, completion_mode: str) -> bool:
        """Whether a ``prompt_ephemeral`` must not start: another reply is
        live, guard-paused or waiting on its completion."""
        if not self.has_reply_in_progress():
            return False
        logger.info(
            "prompt_ephemeral: another reply is still in progress, not starting "
            "(completion_mode=%s)",
            completion_mode,
        )
        return True

    def is_idle(self) -> bool:
        """No reply is live, guard-paused, waiting on its completion, or still
        inside a ``stream_text`` / ``prompt_ephemeral`` call: its
        pre-generation awaits, or a cancelled one finishing its tool handler
        and its history commit after the interruption let go of it."""
        return not (
            self.has_reply_in_progress()
            or getattr(self, "_reply_calls_in_flight", 0)
        )

    def _notify_idle(self) -> None:
        callback = getattr(self, "on_idle", None)
        if callback is None or not self.is_idle():
            return
        try:
            callback()
        except Exception:
            logger.exception("on_idle callback failed")

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
        self._retire_active_generation()
        return True

    def _retire_active_generation(self) -> None:
        """No generation is live any more (it finished, or was cancelled or
        closed): drop its owner with it, so a closed reply's ``_ReplyTurn``
        and its meta are not kept reachable. A displacing begin needs no call
        here: it overwrites the owner."""
        self._active_response_generation = None
        self._active_reply_owner = None
        self._is_responding = False

    def _mark_completion_pending(
        self, generation: int, kind: str = "response", *, finished: bool = True,
        owner: Any = None,
    ) -> None:
        """A finished generation whose completion callback has not run yet.

        ``handle_interruption`` (or a displacing begin) can claim it in that
        window (the cleanup awaits), so the claimer closes it instead of a
        late callback that would land inside the new turn. The session reads
        as busy for the window: the turn is not over until its completion
        runs. Only called for a generation nobody took over, and no newer one
        can have begun without taking it over, so the flag is this turn's to
        set. ``finished`` is False for a reply cut by ``close()``; only
        ``prompt_ephemeral`` reports it, since ``stream_text``'s window opens
        only for a reply that said nothing, which is never sealed.
        """
        self._completion_pending_generation = generation
        self._completion_pending_kind = kind
        self._completion_pending_finished = finished
        self._completion_pending_owner = owner
        self._is_responding = True

    def _take_completion(self, generation: int) -> bool:
        """Whether ``generation`` may still run its completion callback:
        False when it never marked its window (it was taken over mid-reply)
        or someone claimed it there.

        Clears the pending mark, so an interruption arriving after this point
        sees the callback as already running and leaves it alone, and drops
        the window's busy flag. Only its own flag: a generation that holds no
        mark leaves ``_is_responding`` to whoever set it (a claim retires the
        flag itself, a newer generation owns its own).
        """
        pending = getattr(self, "_completion_pending_generation", None) == generation
        if pending:
            self._close_completion_window()
            if getattr(self, "_active_response_generation", None) is None:
                self._is_responding = False
        return pending

    def _take_interrupter_ownership(self, generation: int) -> bool:
        """Whether an interrupter (``cancel_response``) took over closing
        ``generation``'s turn; its completion then does not run."""
        owned = getattr(self, "_interrupter_owned_generations", None)
        if not owned or generation not in owned:
            return False
        owned.discard(generation)
        return True

    def _cancel_response_generation(self) -> bool:
        if getattr(self, "_active_response_generation", None) is None:
            return False
        self._retire_active_generation()
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

    @_tracked_reply_call
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
        response_done_callback: Optional[Callable[[], Awaitable[None]]] = None,
        reply_owner: Any = None,
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
          injected instruction itself ephemeral. ``response_done_callback``
          replaces it for this invocation only (the caller binds the
          completion to this reply), and runs whenever it would.
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

        # Early decline, before any await: nothing below (the anti-repeat
        # preload, the vision switch, image fitting) is worth doing for a
        # turn that cannot begin, and the vision switch replaces the client a
        # live reply is streaming on (it checks again for itself, below). The
        # check right before the begin stays authoritative: these awaits can
        # let another reply begin.
        if self._declines_over_another_reply(completion_mode):
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
            # 不要求 vision_model != model：同一个模型 id 配了不同的视觉 URL / Key
            # 也要切；id 和端点都相同时 switch_model 自己会直接返回。
            if self.vision_model:
                # The preload above can have let a reply begin: check again
                # with no await before the switch. The switch also drops
                # itself if one begins while it builds the new client, so a
                # turn that will not begin never moves a live reply onto
                # another client.
                if self._declines_over_another_reply(completion_mode):
                    return False
                logger.info(
                    f"🖼️ prompt_ephemeral: switching to vision model {self.vision_model} (from {self.model}) for proactive media"
                )
                if not await self.switch_model(
                    self.vision_model, use_vision_config=True,
                    abandon_if=self.has_reply_in_progress,
                ):
                    logger.info(
                        "prompt_ephemeral: another reply began during the vision "
                        "switch, not starting (completion_mode=%s)",
                        completion_mode,
                    )
                    return False
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
        # The tool loop runs on history itself, as stream_text's does: this
        # turn's tool rounds are saved where they ran, so the next request
        # still sees which calls really happened. The instruction is not: it
        # rides each request view only (``_dialog_messages_for_provider``). A
        # reply that is not kept runs on a copy and leaves nothing behind.
        _turn_messages = (
            self._conversation_history if persist_response
            else list(self._conversation_history)
        )
        # This turn's place in history: the instruction itself is never saved,
        # so a cancelled reply is anchored to the last message it was shown.
        # Not a tool-round dict: a round still running may leave history with
        # its turn, and stepping over rounds is what _cancelled_turn_end does
        # anyway. A cancelled reply is dropped once the list was replaced.
        _history_anchor = next(
            (m for m in reversed(self._conversation_history) if not isinstance(m, dict)),
            None,
        )
        _turn_history = self._conversation_history
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
        # The tool rounds this turn appended, across attempts: they place the
        # instruction in the request view, and a cancelled turn trims only its
        # own (``_last_tool_round_of``). ``segment_round`` is the round the
        # last sentinel reported (see stream_text).
        _turn_tool_rounds: list = []
        segment_round = None

        # A non-user reply never begins over another reply that is live,
        # guard-paused or waiting on its completion: it would displace (cut)
        # it, and the displaced turn's close would carry this reply's avatar
        # meta, which is set just before this call. This is the authoritative
        # check: before the diagnostics reset below (they are the live
        # reply's) and with no await between here and the begin, so it cannot
        # go stale.
        if self._declines_over_another_reply(completion_mode):
            return False

        # Retry 策略与 stream_text 对偶（max_retries=3, [1, 2]s 间隔）。
        # 但主动搭话语义不同：用户没在等回复，retry 用尽时**静默吞掉**，
        # 不发任何 status_message 给前端 —— 失败 = 这一轮 AI 根本没想说话。
        # 唯一例外：欠费 / API Key / 配额这类账户级错误必须上报，否则用户
        # 永远不知道为什么主动搭话不工作。
        max_retries = 3
        retry_delays = [1, 2]
        # 与 stream_text 同：assistant_message 只装最后一段（上一个工具轮之后）
        # 的可见文本，落进历史的就是它；工具轮之前的那段已经随工具轮写进历史。
        # 整轮判定（提交了没有、提交了什么）看 assistant_message_total。
        assistant_message = ""
        assistant_message_total = ""
        # Empty-completion 诊断重置：与 stream_text 对偶。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None
        # Open a new reasoning-pulse scope like stream_text does and capture the
        # ownership token: the finally clear below must fire ONLY for this turn's
        # own pulse, never for a newer user stream_text that interleaved and
        # re-pulsed under a fresher seq (Codex P2).
        _reasoning_owner_seq = self._begin_reasoning_stream()
        _completion_kind = (
            "agent_callback"
            if completion_mode != "response" and getattr(self, "on_proactive_done", None)
            else "response"
        )
        response_generation = self._begin_response_generation(_completion_kind, reply_owner)
        if persist_response:
            # Its rounds go to shared history ahead of its reply, which is
            # what a cancelled reply placed later must stop before
            # (``_proactive_round_generation``). An entry is kept while any of
            # its rounds is still in history, however long an older reply
            # stays suspended; a turn that saved no round drops its own.
            in_history = {
                id(message) for message in self._conversation_history
                if isinstance(message, dict)
            }
            self._proactive_turn_rounds = [
                (generation, rounds)
                for generation, rounds in getattr(self, "_proactive_turn_rounds", ())
                if any(id(round_) in in_history for round_ in rounds)
            ]
            self._proactive_turn_rounds.append((response_generation, _turn_tool_rounds))

        async def _flush_prefix_buffer() -> None:
            """Emit what the name-prefix buffer still holds once its segment
            ends: at the end of the stream, or at a tool round, which has
            already saved that text."""
            nonlocal prefix_checked, assistant_message, assistant_message_total
            nonlocal is_first_chunk, emitted_any
            if not prefix_buffer or prefix_checked:
                return
            prefix_checked = True
            master_match = self._match_name_prefix(prefix_buffer, self.master_name)
            lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
            if master_match:
                logger.info("OmniOfflineClient.prompt_ephemeral: 段末剥离主人名前缀")
                flush_text = prefix_buffer[master_match:]
            elif lanlan_match:
                logger.info("OmniOfflineClient.prompt_ephemeral: 段末剥离角色名前缀")
                flush_text = prefix_buffer[lanlan_match:]
            else:
                flush_text = prefix_buffer
            if flush_text and flush_text.strip():
                assistant_message += flush_text
                assistant_message_total += flush_text
                if self.on_text_delta:
                    await self.on_text_delta(flush_text, is_first_chunk)
                is_first_chunk = False
                emitted_any = True
                # 短于前缀缓冲阈值的回复整段走这条路，chunk 分支一次都不进
                # —— 少了这一行，那类回合会照常提交却永远不报裁剪。
                await _emit_pending_budget_notice()

        # Streaming state the flush reads; every attempt resets it.
        task_cancelled = False
        prefix_buffer = ""
        prefix_checked = True
        is_first_chunk = True
        emitted_any = False

        try:
            await self._run_displaced_followup()
            set_call_type("proactive")
            for attempt in range(max_retries):
                # 每次 attempt 重置流式状态（assistant_message / prefix /
                # is_first_chunk 全部归零）。
                assistant_message = ""
                assistant_message_total = ""
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
                        _turn_messages,
                        _instruction=_ephemeral_msg,
                        _tool_rounds=_turn_tool_rounds,
                        # 与 stream_text 同：跨 attempt 存活，由下面的 finally
                        # 统一释放。
                        _tool_image_slots=_turn_tool_image_slots,
                        _tool_bus_frames=_turn_tool_bus_frames,
                        # 这一轮里工具返回的图也归这一轮：没有它，主动搭话轮
                        # 里的工具图会以 turn_id=None 上总线，插件没法把它和
                        # 同一轮的指令/回复对上——而那正是 turn_id 存在的理由。
                        _tool_frames_turn_id=_bus_turn_id,
                        _response_generation=response_generation,
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

                        # tool 轮 sentinel：与 stream_text 同一契约，工具轮之前的
                        # 文本已经随 tool_calls 写进历史，最后一段要从头攒，否则
                        # 收尾的 AIMessage 会把它再写一遍。
                        if getattr(chunk, "tool_round_persisted", False):
                            if self._response_generation_is_active(response_generation):
                                # 还压在名字前缀缓冲里的工具前文本已经在工具轮
                                # 里了，现在补发给用户，历史与所见一致。
                                await _flush_prefix_buffer()
                            else:
                                # A cancelled round keeps only what was shown.
                                self._trim_cancelled_round_text(
                                    assistant_message, _turn_tool_rounds,
                                )
                            assistant_message = ""
                            segment_round = self._last_tool_round_of(_turn_tool_rounds)
                            # 下一段是新的语义单元，名字前缀重新检测。
                            prefix_buffer = ""
                            prefix_checked = not bool(self._prefix_buffer_size)
                            continue
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
                            assistant_message_total += emit_content
                            if self.on_text_delta:
                                await self.on_text_delta(emit_content, is_first_chunk)
                            is_first_chunk = False
                            emitted_any = True
                            # 这一轮确实开口了，挂起的裁剪提示现在才该出现。
                            await _emit_pending_budget_notice()

                    # ── flush 前缀缓冲区（流提前结束时） ──
                    if self._response_generation_is_active(response_generation):
                        await _flush_prefix_buffer()

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
                        assistant_message_total = ""
                        return False
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                        assistant_message = ""
                        assistant_message_total = ""
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
                    assistant_message_total = ""
                    return False
        except Exception as e:
            if _is_api_key_rejected_error(e):
                logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                assistant_message = ""
                assistant_message_total = ""
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
            assistant_message_total = ""
            return False
        except asyncio.CancelledError:
            # The task itself was cancelled (close(), a torn-down caller) with
            # its generation possibly still live: the commit below then
            # places the shown text like a cancelled reply (see stream_text).
            task_cancelled = True
            raise
        finally:
            # 先于其它收尾：把 base64 从历史里摘掉。跨 attempt 存活的代价
            # 就是必须由这里统一释放，否则它会跟着这一轮之后的每次请求走。
            response_cancelled = not self._response_generation_is_active(response_generation)
            interrupter_owned = self._take_interrupter_ownership(response_generation)
            self._release_tool_image_slots(_turn_tool_image_slots)
            self._finish_response_generation(response_generation)
            if not _turn_tool_rounds:
                # Nothing of this turn can mark a boundary.
                self._proactive_turn_rounds = [
                    entry for entry in getattr(self, "_proactive_turn_rounds", ())
                    if entry[1] is not _turn_tool_rounds
                ]
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            committed_text = _strip_nonverbal_directives(assistant_message_total).strip()
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
                # Greetings, agent/topic callbacks and voice nudges answer an
                # instruction, not the user. Mark them like finish_proactive_
                # delivery does so the screen-history guard never joins them
                # with the reply to the user's turn (utils/screen_comment_guard).
                reply = AIMessage(
                    content=assistant_message,
                    additional_kwargs={"dialog_source": "proactive"},
                )
                # Tells a cancelled reply committed later whether this one
                # began after it (_cancelled_turn_end).
                setattr(reply, _REPLY_GENERATION_ATTR, response_generation)
                if response_cancelled or task_cancelled:
                    # Whoever cancelled it may already have appended its own
                    # user message; the half that was shown goes before it.
                    # A round whose sentinel the cancellation lost already
                    # holds that half instead.
                    self._keep_shown_text_of_cut_stream(
                        _history_anchor, reply, segment_round,
                        response_generation, _turn_tool_rounds,
                        turn_history=_turn_history,
                    )
                elif _strip_nonverbal_directives(assistant_message).strip():
                    # Only the segment after the last tool round: the text
                    # before it is saved with that round.
                    self._conversation_history.append(reply)
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
            # The cleanup await is the window in which an interruption (or a
            # displacing user reply) can claim this finished turn's
            # completion: mark it only for that window and always take the
            # mark back, even if the await raises. A turn taken over mid-reply
            # never marks, so its take reports it as taken over too.
            if not interrupter_owned:
                self._mark_completion_pending(
                    response_generation,
                    _completion_kind,
                    finished=not response_cancelled,
                    owner=reply_owner,
                )
            try:
                await self._notify_reasoning_done(_reasoning_owner_seq)
            finally:
                completion_taken_over = not self._take_completion(response_generation)
            # The callback below is skipped exactly when someone else took the
            # close over: an interrupter (cancel_response mid-reply, or a claim
            # during the cleanup await) or a user reply that displaced this
            # one; that owner closes the turn. A close() cancels without
            # taking anything over, so the callback still runs, as on main.
            #
            # The return value says what happened to the reply, not whether the
            # callback ran: a reply that was fully delivered stays True even if
            # its completion was skipped. Only a cancellation mid-reply makes a
            # response-mode turn report False. Callers that hand state to the
            # completion (the avatar path's turn meta) check it themselves.
            reported_committed = content_committed and not (
                completion_mode == "response" and response_cancelled
            )
            if completion_mode == "response":
                done_callback = response_done_callback or self.on_response_done
                if not completion_taken_over and done_callback:
                    await done_callback()
                # 只录常规 reply（completion_mode == "response"）。proactive 路径
                # 已经在 ``core.finish_proactive_delivery`` 上录，这里再录会双写。
                # 与 core.finish_proactive_delivery 同因同治：摘下来不 await。下面的
                # `return reported_committed` 是调用方判断这轮有没有提交的依据，在它
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
                if not completion_taken_over and proactive_done_cb:
                    await proactive_done_cb(content_committed)
                elif not completion_taken_over and self.on_response_done:
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

        return reported_committed

    async def cancel_response(self) -> bool:
        """Cancel the current response; the caller takes over closing its turn.

        The generation is recorded as interrupter-owned, so its completion
        callback does not run: the interrupter closes the turn
        (``TurnMixin._interrupt_offline_reply``). ``close()`` cancels through
        ``_cancel_response_generation`` instead and owns nothing, so a reply
        cut by a session close still runs its completion, as on main.
        """
        live = getattr(self, "_active_response_generation", None)
        cancelled = self._cancel_response_generation()
        if cancelled and live is not None:
            self._record_handed_over(live)
        return cancelled

    async def _cancel_external_voice_submit_task(self) -> bool:
        """Cancel the narrow external-ASR child task, if another task owns it.

        A generation the child is streaming runs its ``finally`` as soon as
        the child is cancelled: its own completion, unless it was taken over
        first. ``handle_interruption`` takes it over before calling this;
        ``close()`` takes nothing over, so the completion still runs, as on
        main.
        """

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

    async def handle_interruption(self) -> str:
        """Handle user interruption - cancel current response.

        Returns what was interrupted, as an ``InterruptedReply``: the kind of
        turn end its completion would have sent ("response" or
        "agent_callback"), or "" when nothing was. Either a live generation
        (streaming or paused by a guard) was cancelled, or a finished one had
        not yet run its completion callback, which is claimed here and will
        not run (``finished`` is then True). The caller now owns that turn's
        close; it closes the interrupted AI turn only then, so a reply whose
        completion is already running never gets a second turn end. The
        value is truthy exactly when something was interrupted.

        Only one reply is ever taken over, as a displacing begin does
        (``_take_displaced_reply``): a pending completion is claimed only
        when no generation is live. One still pending beside a live
        generation belongs to a reply cut by ``close()``, which took nothing
        over, so it is left to run its own completion rather than claimed
        and then closed by nobody.

        The reply is taken over before the independent-ASR child task is
        cancelled, since that task may be the one streaming it. Taken over,
        its ``stream_text`` skips its completion and keeps the part it
        already showed (``_keep_shown_text_of_cut_stream``); otherwise its
        ``finally`` would run the whole completion (turn end, wrap-up) inside
        this interruption and leave nothing to report here.
        """
        if getattr(self, "_active_response_generation", None) is not None:
            logger.info("Handling text mode interruption")
            # Read before the cancel: retiring the generation drops its owner.
            interrupted = InterruptedReply(
                getattr(self, "_active_completion_kind", "response") or "response",
                owner=getattr(self, "_active_reply_owner", None),
            )
            await self.cancel_response()
        else:
            interrupted = self._claim_pending_completion()
        try:
            if await self._cancel_external_voice_submit_task():
                logger.info("Cancelling pending external voice submit")
        except asyncio.CancelledError as exc:
            # Cancelled itself while waiting for the child task: the reply is
            # taken over already, so whoever awaited this still has to close
            # it (``interrupted_reply`` on the cancellation).
            if interrupted:
                exc.interrupted_reply = interrupted
            raise
        return interrupted

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
        # 同 switch_model：genai.Client 的同步、异步两半各持有一个 httpx
        # 连接池，两个都关（_close_genai_client）。
        if self._genai_client is not None:
            try:
                await _close_genai_client(self._genai_client)
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: genai client close failed: {e}")
            self._genai_client = None
        # Clients a switch replaced while a reply was streaming: the session
        # is going, so nothing waits for that reply to return any more.
        await _close_retired_clients(self)
        self._genai_tools_unsupported = False
        self._openai_tools_unsupported = False
        self._openai_tools_unsupported_with_images = False
        logger.info("OmniOfflineClient closed")
