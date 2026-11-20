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

"""One provenance record per turn: who spoke, which call chain, which trace.

There are two questions a permission decision has to answer -- "who is speaking"
(``main_logic/tool_permissions.py``) and "which plugin called whom"
(``plugin/core/call_registry.py``, PR #3780). They are the same question asked at
two points on one path, so they get one carrier instead of two parallel audit
trails:

- :class:`Provenance` is that carrier. ``actor`` is the speaker, ``chain`` is the
  plugin hop list from the root caller to the plugin running now, and
  ``trace_id`` is the same string on both sides. A plugin hop extends the
  existing record with :meth:`Provenance.child`; it does not start a new one.
- :func:`emit_audit` is the only audit writer. The default destination is the
  Main service log (the line operators already read). When the plugin audit sink
  from #3780 is available it registers itself with :func:`set_audit_sink` and
  receives the same record -- same schema, same trace id, one destination, not
  two files to reconcile by timestamp.

Why a context variable *and* a session anchor. The carrier has to survive two
different lifetimes:

- A task started by an inbound sink inherits the context (``asyncio.create_task``
  copies it), so ``provenance_scope`` covers the whole turn that task spawns.
- The conversational clients' ``handle_messages`` loop is long-lived and started
  before any message arrived, so a scope opened at ingress never reaches it.
  The session therefore keeps the last *local* provenance as an anchor, stamped
  by the local UI and microphone entry points, and reads it at tool-call time.

The anchor is a fallback, never an override: a scope set on the current task
wins. That is what keeps a connector turn from inheriting the owner's band once
the connector runs in this process -- its sink stamps the scope, and the scope
is more specific than the session.

Nothing here is a permission decision. Bands, risk classes and the table live in
``config/permission_settings.py``; this module only records where a turn came
from and who it belongs to.
"""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, Optional

from config.permission_settings import PERMISSION_LOCAL_CHANNEL
from utils.logger_config import get_module_logger
from utils.turn_actor import TurnActor

logger = get_module_logger(__name__, "Main")

# Where a turn started. These are facts about the transport or the entry point,
# never a value a speaker can choose.
ORIGIN_CONNECTOR = "connector"
ORIGIN_LOCAL_UI = "local_ui"
ORIGIN_LOCAL_VOICE = "local_voice"
ORIGIN_PLUGIN = "plugin"
ORIGIN_SYSTEM = "system"

#: Origins that mean "the owner is sitting at this machine". Only the local
#: surfaces may produce them, and only in this process.
LOCAL_ORIGINS = frozenset({ORIGIN_LOCAL_UI, ORIGIN_LOCAL_VOICE})

#: The transport label the local owner is stamped with. ``PERMISSION_SOURCE_BANDS``
#: maps this source to OWNER, so it is what turns "local" into a band; the canonical
#: account id derived from it is ``webui:owner``.
LOCAL_OWNER_CHANNEL = PERMISSION_LOCAL_CHANNEL


def new_trace_id() -> str:
    """A fresh opaque id. No timestamp, no pid, no speaker: ids only, joinable."""
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Provenance:
    """Who this turn belongs to, and which chain of calls produced it.

    Frozen. A mutable carrier would let any code on the path rewrite the speaker,
    which is the one thing the permission gate promises cannot happen.

    ``actor`` is ``None`` when the entry point knows there is a turn but not who
    it belongs to (a notice, a background job). That is different from "no
    provenance at all": the gate reads both as DEFAULT_BAND, but the audit line
    can tell the two apart.

    ``chain`` is the plugin hop list, root first, current plugin last. It is
    empty for a host-only turn. This PR never fills it -- it is the field #3780's
    registry extends -- but the wire shape and the audit line carry it now, so
    the two features share one record from the first commit instead of acquiring
    a join key later.
    """

    trace_id: str
    origin: str
    actor: Optional[TurnActor] = None
    chain: tuple[str, ...] = ()
    ref: Optional[str] = None

    @property
    def is_local(self) -> bool:
        return self.origin in LOCAL_ORIGINS

    def actor_payload(self) -> Optional[dict[str, str]]:
        """The resolver shape ``main_logic.tool_permissions.resolve_band`` reads."""
        if self.actor is None:
            return None
        return self.actor.as_dict()

    def child(self, hop: str) -> "Provenance":
        """The same turn, one plugin hop deeper. Never a new trace."""
        name = str(hop or "").strip()
        if not name:
            return self
        return replace(self, chain=(*self.chain, name), origin=ORIGIN_PLUGIN)

    def to_wire(self) -> dict[str, Any]:
        """The cross-process shape.

        ``origin`` is ``{"kind", "ref"}`` and ``speaker`` is the canonical
        account id, because that is exactly what #3780's ``CallOrigin`` decodes.
        Nothing else is added: a field the other side does not read is a field
        that will drift.
        """
        return {
            "trace_id": self.trace_id,
            "speaker": self.actor.account_id if self.actor is not None else None,
            "channel": self.actor.channel if self.actor is not None else "",
            "origin": {"kind": self.origin, "ref": self.ref},
            "chain": list(self.chain),
        }

    @classmethod
    def from_wire(cls, payload: Any) -> Optional["Provenance"]:
        """Decode a wire record, or None when it is not one.

        Deliberately strict about the trace id: a payload without one cannot be
        joined to anything, and inventing an id would create a second trail --
        the failure this module exists to prevent. A speaker that does not parse
        is dropped rather than guessed.
        """
        if not isinstance(payload, dict):
            return None
        trace_id = str(payload.get("trace_id") or "").strip()
        if not trace_id:
            return None
        origin = payload.get("origin")
        if isinstance(origin, dict):
            kind = str(origin.get("kind") or "").strip()
            ref = origin.get("ref")
        else:
            kind, ref = str(origin or "").strip(), None
        speaker = str(payload.get("speaker") or "").strip()
        actor = None
        if speaker and ":" in speaker:
            platform, actor_id = speaker.split(":", 1)
            actor = TurnActor(
                user_id=actor_id,
                channel=str(payload.get("channel") or platform),
                message_type="private",
                source=kind or ORIGIN_SYSTEM,
            )
        chain_raw = payload.get("chain")
        chain = tuple(
            str(hop) for hop in chain_raw if str(hop).strip()
        ) if isinstance(chain_raw, (list, tuple)) else ()
        return cls(trace_id=trace_id, origin=kind or ORIGIN_SYSTEM, actor=actor,
                   chain=chain, ref=str(ref) if ref else None)

def provenance_for_actor(actor: Optional[TurnActor], *, origin: str,
                         ref: Optional[str] = None) -> Optional[Provenance]:
    """Wrap a stamped actor into a fresh trace. None stays None.

    Used by the connector path, which knows the speaker but has no trace yet. The
    trace starts here and is what every later record in this turn quotes.
    """
    if actor is None:
        return None
    return Provenance(trace_id=new_trace_id(), origin=origin, actor=actor, ref=ref)


def local_owner_provenance(origin: str) -> Provenance:
    """A turn that belongs to the person at this machine.

    Only the local UI socket and the local microphone may call this. The band
    comes from ``PERMISSION_SOURCE_BANDS["webui"]``; there is no separate
    privilege path for "local", which is what keeps one ruling in one place.

    ``user_id="owner"`` is a role, not an account: it exists so the canonical
    ``webui:owner`` form resolves through the same ``platform:actor`` grammar as
    a QQ id. It is never compared against a real person's id.
    """
    if origin not in LOCAL_ORIGINS:
        raise ValueError(f"not a local origin: {origin!r}")
    return Provenance(
        trace_id=new_trace_id(),
        origin=origin,
        actor=TurnActor(
            user_id="owner",
            message_type="private",
            channel=LOCAL_OWNER_CHANNEL,
            source=origin,
        ),
    )


_CURRENT: contextvars.ContextVar[Optional[Provenance]] = contextvars.ContextVar(
    "neko_turn_provenance", default=None
)


@contextlib.contextmanager
def provenance_scope(provenance: Optional[Provenance]) -> Iterator[Optional[Provenance]]:
    """Bind ``provenance`` to this task and everything it spawns.

    ``None`` clears it for the block, which is meaningful: a path that
    deliberately drops identity must not inherit the previous speaker.
    """
    token = _CURRENT.set(provenance)
    try:
        yield provenance
    finally:
        _CURRENT.reset(token)


def current_provenance() -> Optional[Provenance]:
    """The provenance of this task, or None when no entry point stamped one."""
    return _CURRENT.get()


# ------------------------------------------------------------------ audit sink
#
# One writer, one record schema. The sink is registered by the plugin audit
# module from #3780 when that lands; until then the record is rendered into the
# Main log. Both destinations receive the same dict, so a later join is a field
# lookup, not a timestamp correlation exercise.

AuditSink = Callable[[dict[str, Any]], None]
_audit_sink: Optional[AuditSink] = None


def set_audit_sink(sink: Optional[AuditSink]) -> Optional[AuditSink]:
    """Install the shared audit destination; returns the previous one.

    Returning the old sink lets a test put it back without reaching into the
    module. Passing ``None`` restores the Main-log fallback.
    """
    global _audit_sink
    previous = _audit_sink
    _audit_sink = sink
    return previous


def emit_audit(record: dict[str, Any]) -> None:
    """Hand one record to the single audit destination.

    A failing sink must not fail the tool call: the permission gate's contract is
    that it never raises into the dispatch path. It is logged once at error level
    so a broken sink is visible without becoming a second outage.
    """
    sink = _audit_sink
    if sink is None:
        _log_record(record)
        return
    try:
        sink(record)
    except Exception:  # noqa: BLE001 - audit must not break the call it records
        logger.exception("[audit] sink failed; record dropped kind=%s", record.get("kind"))


def _log_record(record: dict[str, Any]) -> None:
    """Fallback rendering: one line, key=value, no message content.

    Not JSON on purpose. The Main log is read by people; the JSONL file #3780
    writes is read by tools. Both are the same record.

    The prefix stays ``[permission]`` for permission records: operators and the PR
    body already grep for ``[permission] would-deny``, and renaming it would make
    the documented command return nothing.
    """
    chain = record.get("chain") or []
    prefix = "[permission]" if record.get("kind") == "tool_permission" else "[audit]"
    logger.warning(
        "%s kind=%s trace=%s action=%s label=%s tool=%s band=%s risk=%s "
        "account=%s group=%s origin=%s chain=%s mode=%s reason=%s outcome=%s",
        prefix,
        record.get("kind", ""),
        record.get("trace_id", ""),
        record.get("action", ""),
        record.get("label", ""),
        record.get("tool", ""),
        record.get("band", ""),
        record.get("risk", ""),
        record.get("account_id") or "(unknown)",
        record.get("group_id") or "-",
        record.get("origin", ""),
        ">".join(chain) if chain else "-",
        record.get("mode", ""),
        record.get("reason", ""),
        record.get("outcome", ""),
    )
