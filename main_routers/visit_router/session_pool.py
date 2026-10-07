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

"""The isolated LLM session of one visit side (design §3.6.1, §5 PR-09a ``session_pool.py``).

A visit never touches ``mgr.session``: each side talks through its own
``OmniOfflineClient`` built from the reviewed visit persona (the caller
passes ``build_visit_instructions(...)``; the original character card is
never read here, OD-10 v3), with no tools, a short response budget and the
neutral family term as ``master_name``.

History order is the visit's total order ``(lp, side_rank)`` (host 0, guest
1), not arrival order, so both sides feed the same history to their LLM.
Every message the runtime adds is registered with its key
(:func:`append_visit_message`, :func:`tag_last_turn`); :func:`sort_visit_history`
re-sorts before each LLM turn (inside ``turn_lock``). A message without a
key stays right after the keyed message it followed; the leading system
message never moves.

Streaming: the client's ``on_text_delta`` forwards to the sink the current
line installed (:meth:`VisitSession.set_sink`); a delta that arrives with no
sink is dropped. Rerolls of the length guard are off: a retry would push a
second answer into a line whose first answer is already being spoken.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from config.prompts.prompts_visit import get_family_neutral_term
from config.visit_settings import VISIT_HISTORY_MAX_MESSAGES, VISIT_RESPONSE_MAX_TOKENS
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens

logger = get_module_logger(__name__, "Main")

SIDE_RANK = {"host": 0, "guest": 1}
SortKey = tuple[int, int]
DeltaSink = Callable[[str], Any]

_UNKEYED_FLOOR: SortKey = (-1, -1)


@dataclass(eq=False)
class VisitSession:
    """One side's isolated session: the client, its turn lock and the history keys."""

    client: Any
    side: str
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _sink: Optional[DeltaSink] = None
    # id(message) -> (message, key)：同时持有对象引用，id 不会被回收后复用
    _keys: dict[int, tuple[Any, SortKey]] = field(default_factory=dict)

    @property
    def history(self) -> list:
        return self.client._conversation_history

    def set_sink(self, sink: Optional[DeltaSink]) -> None:
        """Route LLM deltas to ``sink`` (the line being generated); None drops them."""
        self._sink = sink

    async def on_text_delta(self, text: str, is_first: bool = False, **_kwargs: Any) -> None:
        sink = self._sink
        if sink is None or not text:
            return
        result = sink(text)
        if inspect.isawaitable(result):
            await result

    def key_of(self, message: Any) -> Optional[SortKey]:
        entry = self._keys.get(id(message))
        return entry[1] if entry is not None and entry[0] is message else None

    def tag(self, message: Any, key: SortKey) -> None:
        self._keys[id(message)] = (message, (int(key[0]), int(key[1])))

    def forget_untracked(self) -> None:
        live = {id(m) for m in self.history}
        for ident in [i for i in self._keys if i not in live]:
            del self._keys[ident]


def sort_key(lp: int, side: str) -> SortKey:
    """The total-order key of a line: ``(lp, 0 if host else 1)``."""
    return int(lp), SIDE_RANK[side]


async def create_visit_session(
    name: str,
    side: str,
    *,
    instructions: str,
    lang: str | None,
    api_config: dict | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> VisitSession:
    """Build and connect one side's isolated session.

    ``instructions`` is the full system prompt built by the caller from the
    reviewed persona and the visit memory block. ``api_config`` defaults to
    the ``conversation`` model; ``client_factory`` defaults to
    ``OmniOfflineClient`` (tests pass a fake).
    """
    if side not in SIDE_RANK:
        raise ValueError("side must be 'host' or 'guest'")
    if api_config is None:
        from utils.config_manager import get_config_manager

        api_config = await get_config_manager().aget_model_api_config("conversation")
    if client_factory is None:
        from main_logic.omni_offline_client import OmniOfflineClient

        client_factory = OmniOfflineClient
    holder: dict[str, VisitSession] = {}

    async def forward(text: str, is_first: bool = False, **kwargs: Any) -> None:
        session = holder.get("session")
        if session is not None:
            await session.on_text_delta(text, is_first, **kwargs)

    client = client_factory(
        base_url=api_config.get("base_url", ""),
        api_key=api_config.get("api_key", ""),
        model=api_config.get("model", ""),
        provider_type=api_config.get("provider_type"),
        on_text_delta=forward,
        max_response_length=VISIT_RESPONSE_MAX_TOKENS,
        lanlan_name=name,
        master_name=get_family_neutral_term(lang),
        tool_definitions=[],
    )
    # 长度守卫不重 roll：重 roll 会把第二个回答推进已经在念的这一行
    client.max_response_rerolls = 0
    # 连续相似回复不清空历史：清空会抹掉按序排好的双方发言和本轮的标记
    client.repetition_reset_enabled = False
    session = VisitSession(client=client, side=side)
    holder["session"] = session
    try:
        await client.connect(instructions=instructions)
    except BaseException:
        await close_visit_session(session)
        raise
    return session


def _effective_keys(session: VisitSession, items: list) -> list[SortKey]:
    keys: list[SortKey] = []
    last = _UNKEYED_FLOOR
    for msg in items:
        key = session.key_of(msg)
        if key is not None:
            last = key
        keys.append(last)
    return keys


def _system_prefix_len(history: list) -> int:
    from utils.llm_client import SystemMessage

    n = 0
    while n < len(history) and isinstance(history[n], SystemMessage):
        n += 1
    return n


def sort_visit_history(session: VisitSession) -> None:
    """Stable-sort the history by ``(lp, side_rank)`` (call inside ``turn_lock``, before a turn).

    The leading system message(s) stay first; an unkeyed message keeps the
    key of the keyed message before it, so it moves together with it.
    """
    history = session.history
    head = _system_prefix_len(history)
    body = history[head:]
    if len(body) <= 1:
        return
    keys = _effective_keys(session, body)
    order = sorted(range(len(body)), key=lambda i: keys[i])
    if order != list(range(len(body))):
        history[head:] = [body[i] for i in order]


def append_visit_message(session: VisitSession, message: Any, key: SortKey) -> None:
    """Add a history message with its sort key, straight at its sorted position."""
    history = session.history
    head = _system_prefix_len(history)
    keys = _effective_keys(session, history[head:])
    session.tag(message, key)
    pos = len(history)
    # 插在第一个键更大的消息之前（同键保持到达顺序）
    for offset, existing in enumerate(keys):
        if existing > (int(key[0]), int(key[1])):
            pos = head + offset
            break
    history.insert(pos, message)


def tag_last_turn(session: VisitSession, *, prompt_key: SortKey | None, reply_key: SortKey | None) -> None:
    """Register the keys of what ``stream_text`` just appended (the prompt, then the AI reply)."""
    from utils.llm_client import AIMessage, HumanMessage

    history = session.history
    idx = len(history) - 1
    # 只给还没有键的消息打标：stream_text 遇到空 prompt 什么都不追加，末尾那条是上一轮已登记的回复
    if (reply_key is not None and idx >= 0 and isinstance(history[idx], AIMessage)
            and session.key_of(history[idx]) is None):
        session.tag(history[idx], reply_key)
        idx -= 1
    if prompt_key is not None:
        while idx >= 0 and not isinstance(history[idx], HumanMessage):
            idx -= 1
        if idx >= 0 and session.key_of(history[idx]) is None:
            session.tag(history[idx], prompt_key)


def trim_visit_history(session: VisitSession, max_messages: int = VISIT_HISTORY_MAX_MESSAGES) -> None:
    """Keep the system prefix plus the newest ``max_messages`` messages."""
    history = session.history
    if len(history) <= 1:
        return
    head = _system_prefix_len(history)
    excess = len(history) - head - max(0, int(max_messages))
    if excess > 0:
        del history[head:head + excess]
        session.forget_untracked()


def _content_text(message: Any) -> str:
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def pop_trailing_ai_message(session: VisitSession, expected: str) -> bool:
    """Pop the last message when it is the AI reply ``expected``; False (nothing popped) otherwise.

    Used when a line is cut off: the whole line leaves the history and the
    caller appends the released prefix plus ``VISIT_MARK_INTERRUPTED``.
    """
    from utils.llm_client import AIMessage

    history = session.history
    if not history or not isinstance(history[-1], AIMessage) or _content_text(history[-1]) != expected:
        return False
    history.pop()
    session.forget_untracked()
    return True


def estimate_turn_usage(session: VisitSession, output_text: str) -> dict:
    """Token estimate of one LLM call, for the usage record when the provider reports none.

    Called right after the call: input = the history it sent (system prompt
    included), i.e. the current history minus the reply ``stream_text``
    appended at its end; output = the streamed text.
    """
    from utils.llm_client import AIMessage

    history = list(session.history)
    if output_text and history and isinstance(history[-1], AIMessage):
        # 本轮有回复才会追加 AI 消息；空回复时末尾那条是上一轮的、属于这次的输入
        history.pop()
    sent = sum(count_tokens(_content_text(m)) for m in history)
    return {"llm_input_tokens": sent, "llm_output_tokens": count_tokens(output_text or "")}


async def close_visit_session(session: VisitSession) -> None:
    """Drop the sink and close the client (never raises)."""
    session.set_sink(None)
    close: Optional[Callable[[], Awaitable[Any]]] = getattr(session.client, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 - 收尾路径不抛
        logger.warning("visit session: close failed: %s", type(exc).__name__)
