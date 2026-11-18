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

The carrier itself is :class:`utils.provenance.Provenance`, which holds this actor
together with the trace id and the plugin call chain. This module only builds the
actor half and keeps the historical ``turn_scope`` / ``current_turn_actor`` names
working: a second context variable for the same fact would be the parallel trail
the carrier exists to prevent.

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
scope opened after that does not cover it. The session anchor
(``utils/provenance.local_owner_provenance`` plus the manager's ``_session_provenance``)
is what covers the local UI and voice paths; the connector path still needs its own
stamp in the process that executes tools.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional

from config.permission_settings import (
    PERMISSION_CHANNEL_PLATFORMS,
    PERMISSION_LOCAL_CHANNEL,
)

# How a stamped turn is labelled. ``SOURCE_CONNECTOR`` is the default this module
# writes when the caller did not say; a local entry point passes its own origin
# through ``TurnActor.source`` so the record names the surface honestly.
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
    channel = str(message.get("channel") or "").strip()
    if channel == PERMISSION_LOCAL_CHANNEL:
        # Reserved for the host's own UI and microphone, which stamp through
        # ``utils.provenance.local_owner_provenance`` instead. A message arriving
        # through a connector that claims this channel would otherwise resolve to
        # OWNER -- a stranger writing one field. Refuse the actor and let the turn
        # fall to DEFAULT_BAND rather than trusting the label.
        return None
    group_id = str(message.get("group_id") or "").strip()
    return TurnActor(
        user_id=user_id,
        group_id=group_id,
        message_type=str(message.get("message_type") or ""),
        channel=channel,
    )


@contextlib.contextmanager
def turn_scope(actor: Optional[TurnActor]) -> Iterator[Optional[TurnActor]]:
    """Bind ``actor`` for the duration of this block, then put the old value back.

    Thin wrapper over ``utils.provenance.provenance_scope``: one carrier, so a
    connector stamp and a local-owner stamp cannot end up in two different
    variables that the gate reads in a fixed order.

    The only writer at this level. Nesting is safe (a turn inside a turn restores the
    outer actor on exit), and ``reset`` in ``finally`` is what stops one speaker's band
    from leaking into the next speaker's turn when a task is reused.

    Passing None is meaningful: it clears the carrier for the block, so a path that
    deliberately drops identity does not inherit an old one.
    """
    from utils.provenance import ORIGIN_CONNECTOR, provenance_for_actor, provenance_scope

    if actor is None:
        with provenance_scope(None) as none_prov:
            yield None if none_prov is None else none_prov.actor
        return
    with provenance_scope(
        provenance_for_actor(actor, origin=actor.source or ORIGIN_CONNECTOR)
    ) as prov:
        yield prov.actor if prov is not None else None


def current_turn_actor() -> Optional[TurnActor]:
    """The speaker of this turn, or None when no host entry point declared one."""
    from utils.provenance import current_provenance

    prov = current_provenance()
    return prov.actor if prov is not None else None


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
    """The carrier in resolver shape, or None. What the gate reads.

    Kept as the actor-only view of ``utils.provenance.current_provenance``; callers
    that also need the trace id or the plugin chain should read the record directly.
    """
    from utils.provenance import current_provenance

    prov = current_provenance()
    return prov.actor_payload() if prov is not None else None


def describe(actor: Optional[TurnActor]) -> str:
    """A log-safe one-liner: ids only, never a nickname or message text."""
    if actor is None:
        return "actor=(none)"
    return (
        f"actor={actor.account_id or '(unmapped)'} kind={actor.message_type or '(none)'} "
        f"channel={actor.channel or '(none)'} group={actor.group_account_id or '(none)'}"
    )
