# -*- coding: utf-8 -*-
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

"""Who spoke in the turn that is running now.

One carrier, one writer per entry point, one reader. The permission gate
(``main_logic/tool_permissions.py``) is the only consumer, and it reads nothing else,
so "who asked?" has exactly one answer per turn instead of one per call site.

Why a context variable and not a field on the tool call. ``ToolCall`` objects are built
inside the LLM client (four sites, in two clients), from provider stream data. The
sender id lives in the inbound message, which the client never sees. Threading it
through four call chains to reach a field nothing else reads would cost a parameter on
every turn in the app; the same fact already travels this way for reply attribution
(``main_logic/agent_event_bus.conversation_reply_origin``, set in
``main_logic/core/greeting.py`` and ``main_logic/core/proactive.py``).

``asyncio.create_task`` copies the current context, so a scope set before a turn starts
covers the whole task tree that turn spawns -- the connector's inbound sink, the reply
it triggers, and every tool call in it. Two limits follow, and both are live today.

It does not cross a process boundary or an event loop. The connectors run wherever their
consumer runs, and nothing in main imports them, so the sink stamp reaches no tool call
in this repo yet. It does not reach a task that started earlier: the dialog clients'
``handle_messages`` loop is long-lived and was born before any message arrived, so a
scope opened after that does not cover it. Both are step 2 work -- stamp the actor where
the turn is consumed, in the process that executes tools. Until that lands, every gated
call resolves to DEFAULT_BAND, which is exactly why the gate ships in observe mode.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional

from config.permission_settings import PERMISSION_CHANNEL_PLATFORMS

# Who stamped this turn. ``SOURCE_CONNECTOR`` is the only value this module can
# produce, because the only writer here is the connection layer handing a real inbound
# message to its sink. A future entry point adds a constant and a call, not a policy.
SOURCE_CONNECTOR = "connector"

#: Private chat, as the connectors spell it in ``InboundMessage.message_type``.
KIND_PRIVATE = "private"
#: Group chat, same source.
KIND_GROUP = "group"


@dataclass(frozen=True)
class TurnActor:
    """The speaker of the current turn, as the host learned it from the transport.

    Fields hold platform-local strings, not decisions: no band, no nickname, no
    display name. ``account_id`` builds the canonical ``platform:actor`` form the band
    maps use, and returns "" when the transport gave nothing to build it from.

    Frozen, and set only through :func:`turn_scope`. A mutable global that any caller
    could rewrite would make the band claim worthless: the whole point is that the
    speaker cannot choose their own trust level.
    """

    user_id: str = ""
    group_id: str = ""
    message_type: str = ""
    channel: str = ""
    source: str = SOURCE_CONNECTOR

    @property
    def is_group(self) -> bool:
        """True when the group band must be weighed against the speaker band."""
        return self.message_type == KIND_GROUP and bool(self.group_id)

    @property
    def account_id(self) -> str:
        """``platform:actor`` for the speaker, or "" when the stamp is unusable.

        The platform comes from the transport label, so a connector that reports an
        unknown channel yields "" and the speaker lands on DEFAULT_BAND. Refusing to
        invent a platform is deliberate: a wrong prefix would read one real person's id
        out of another platform's map.
        """
        if not self.user_id:
            return ""
        platform = PERMISSION_CHANNEL_PLATFORMS.get(self.channel)
        if not platform:
            return ""
        return f"{platform}:{self.user_id}"

    @property
    def group_account_id(self) -> str:
        """``platform:conversation`` for the group, or "" outside a group turn."""
        if not self.is_group:
            return ""
        platform = PERMISSION_CHANNEL_PLATFORMS.get(self.channel)
        if not platform:
            return ""
        return f"{platform}:{self.group_id}"

    def as_dict(self) -> dict[str, str]:
        """The shape :func:`main_logic.tool_permissions.resolve_band` reads."""
        return {
            "account_id": self.account_id,
            "group_id": self.group_account_id,
            "message_type": self.message_type,
            "channel": self.channel,
            "source": self.source,
        }


_turn_actor: contextvars.ContextVar[Optional[TurnActor]] = contextvars.ContextVar(
    "neko_turn_actor", default=None
)


def actor_from_inbound(message: Any) -> Optional[TurnActor]:
    """Stamp one normalized inbound message, or return None when it has no speaker.

    Notices (pokes, third-party bans) carry no sender the character is answering, and a
    poke from an operator is not a request to run a tool. Those return None, so the turn
    falls to DEFAULT_BAND rather than inheriting whatever actor was last in scope.
    """
    if not isinstance(message, Mapping):
        return None
    if str(message.get("message_type") or "") not in (KIND_PRIVATE, KIND_GROUP):
        return None
    user_id = str(message.get("user_id") or "").strip()
    if not user_id:
        return None
    group_id = str(message.get("group_id") or "").strip()
    return TurnActor(
        user_id=user_id,
        group_id=group_id,
        message_type=str(message.get("message_type") or ""),
        channel=str(message.get("channel") or ""),
    )


@contextlib.contextmanager
def turn_scope(actor: Optional[TurnActor]) -> Iterator[Optional[TurnActor]]:
    """Bind ``actor`` for the duration of this block, then put the old value back.

    The only writer in this module. Nesting is safe (a turn inside a turn restores the
    outer actor on exit), and ``reset`` in ``finally`` is what stops one speaker's band
    from leaking into the next speaker's turn when a task is reused.

    Passing None is meaningful: it clears the carrier for the block, so a path that
    deliberately drops identity does not inherit an old one.
    """
    token = _turn_actor.set(actor)
    try:
        yield actor
    finally:
        _turn_actor.reset(token)


def current_turn_actor() -> Optional[TurnActor]:
    """The speaker of this turn, or None when no host entry point declared one."""
    return _turn_actor.get()


@contextlib.contextmanager
def turn_for_message(message: Any) -> Iterator[Optional[TurnActor]]:
    """Bind one pulled message as the current turn's speaker.

    For a consumer that calls ``receive_message()`` in its own loop: the connection
    layer cannot scope that task, so the consumer scopes the block that answers the
    message. Same rules as the sink path -- a message with no speaker clears the scope.
    """
    with turn_scope(actor_from_inbound(message)) as actor:
        yield actor


def current_actor_payload() -> Optional[dict[str, str]]:
    """The carrier in resolver shape, or None. What the gate reads."""
    actor = current_turn_actor()
    if actor is None:
        return None
    return actor.as_dict()


def describe(actor: Optional[TurnActor]) -> str:
    """A log-safe one-liner: ids only, never a nickname or message text."""
    if actor is None:
        return "actor=(none)"
    return (
        f"actor={actor.account_id or '(unmapped)'} kind={actor.message_type or '(none)'} "
        f"channel={actor.channel or '(none)'} group={actor.group_account_id or '(none)'}"
    )
