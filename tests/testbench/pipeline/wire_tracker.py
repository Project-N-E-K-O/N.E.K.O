"""Single-writer chokepoint for ``Session.last_llm_wire``.

Background (P25 Day 2 polish r4 / LESSONS_LEARNED §7.25 第五次同族证据)
-----------------------------------------------------------------------
The Prompt Preview panel promises the tester "this is exactly what the
LLM sees". Early implementation built the preview by **re-deriving** the
wire from ``session.messages`` every time ``GET /api/chat/prompt_preview``
was called. That works for ``POST /api/chat/send`` (where the real wire
and ``session.messages`` are by design in lock-step), but silently breaks
for every "ephemeral instruction" path:

* External events (``/api/session/external-event``) build an avatar /
  callback / proactive instruction as a one-shot ``HumanMessage`` tail,
  send it to the LLM, and only store a short human-readable ``memory_note``
  back into ``session.messages``. The preview showed the note; the LLM
  actually saw the full instruction.
* Auto-dialog nudge / simulated_user likewise craft transient wires
  that never land in ``session.messages``.
* Any future "prompt_ephemeral"-style path (main program has several)
  has the same shape.

Root cause: the **preview answered the wrong question** — "what will the
next ``/send`` wire look like?" instead of "what did the last LLM
actually see?". For a testing platform the second question is the
primary one.

Fix (structural, not procedural)
--------------------------------
Every LLM call site *must* stamp ``session.last_llm_wire`` with the exact
messages it is about to send, **before** the LLM call starts. Then the
preview panel reads from this field directly — no more re-derivation.

This module is the **only** writer. Call sites wrap the pre-invocation
stamping in :func:`record_last_llm_wire`; the bundle consumer reads via
:func:`get_last_llm_wire`. A ``cursor rule`` + smoke audit the matrix
"LLM call sites × call to record_last_llm_wire" so adding a new
ephemeral path without updating the preview invariant fails loudly.

Shape of the stored dict (see also ``session_store.Session.last_llm_wire``
docstring — the two are the authoritative reference)::

    {
      "wire_messages": list[{"role": str, "content": Any}],
      "source": str,         # "chat.send" / "avatar_event" / ...
      "recorded_at_real": iso_str,
      "recorded_at_virtual": iso_str,
      "reply_chars": int,    # -1 while in-flight, set by update_last_llm_wire_reply
      "note": str | None,
    }

Notes
-----
* The snapshot is **deep-copied** so concurrent mutation of the
  caller's local wire list (e.g. tail-appending instruction after the
  stamp) does not retroactively mutate what the preview shows.
* ``reply_chars`` starts at -1 (in-flight). Call
  :func:`update_last_llm_wire_reply` after the LLM reply is known.
* Failures in the stamping **must not** block the LLM call — this is
  an observability hook. We ``try/except`` every call at the call site
  and fall back to ``python_logger().debug`` (L31-style best-effort).
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, TYPE_CHECKING

from tests.testbench.logger import python_logger

if TYPE_CHECKING:
    from tests.testbench.session_store import Session


# ── Known sources — keep this frozenset authoritative ─────────────────

# The preview UI renders a human-friendly label per source (see
# ``static/ui/chat/preview_panel.js`` + ``static/core/i18n.js``). Keep
# these strings stable — renaming one silently falls back to the raw
# slug on the UI. Adding a new source requires both:
#   (1) append to this set (runtime validation)
#   (2) add an i18n key ``chat.preview.last_wire.source.<slug>``
KNOWN_SOURCES: frozenset[str] = frozenset({
    "chat.send",             # /api/chat/send stream
    "avatar_event",          # /api/session/external-event kind=avatar
    "agent_callback",        # /api/session/external-event kind=agent_callback
    "proactive_chat",        # /api/session/external-event kind=proactive
    "auto_dialog_target",    # auto_dialog.py → target (character) LLM
    "judge.llm",             # judge_runner.py — displayed on Evaluation/Run
    "memory.llm",            # memory_runner.py — displayed on Memory subpages
})
# ── Removed in P25 r7 (2026-04-23) ─────────────────────────────────
# ``"simulated_user"`` + ``"auto_dialog_simuser"`` used to live here
# but were removed when SimUser LLM calls switched to ``NOSTAMP``.
# Rationale: the simulated user is a *conversation source*, not an
# object under test — its wire has no diagnostic value for the tester
# and only pollutes the Chat Preview Panel (which promises "what the
# target AI sees"). See ``simulated_user.py::generate_simuser_message``
# and ``LESSONS_LEARNED §L44`` for full justification. If you ever need
# to re-enable SimUser wire stamping (e.g. for a future SimUser-focused
# diagnostic view), add it back here + add the i18n source label.


def record_last_llm_wire(
    session: "Session",
    wire_messages: list[dict[str, Any]],
    *,
    source: str,
    note: str | None = None,
) -> None:
    """Stamp ``session.last_llm_wire`` with the wire about to be sent.

    Must be called **before** the LLM request starts. After the LLM call
    returns (success or failure), call :func:`update_last_llm_wire_reply`
    to fill in ``reply_chars``.

    Parameters
    ----------
    session
        The active session. May be in any state except destroyed.
    wire_messages
        The exact list about to be sent. Deep-copied internally; the
        caller is free to mutate its local list afterwards (e.g. to
        retry with a different tail).
    source
        Which LLM call path this is. Must be a member of
        :data:`KNOWN_SOURCES` — raises ``ValueError`` otherwise so a
        typo at the call site fails loudly in smoke.
    note
        Optional short tag (e.g. ``"avatar:fist+context"``) surfaced in
        the UI metadata strip for fast visual diffing.
    """
    if source not in KNOWN_SOURCES:
        raise ValueError(
            f"record_last_llm_wire: unknown source {source!r}; add to "
            f"wire_tracker.KNOWN_SOURCES + i18n + smoke audit"
        )

    # Deep copy — the caller may hand us their live working list. A
    # shallow copy would still share the inner content dicts (for
    # multimodal content that's a real list<dict>), and a later mutation
    # (e.g. clearing a batch) would retroactively scramble the preview.
    snapshot = copy.deepcopy(wire_messages)

    virtual_iso: str | None = None
    try:
        virtual_iso = session.clock.now().isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001 — best-effort telemetry
        python_logger().debug(
            "record_last_llm_wire: virtual clock read failed: %s: %s",
            type(exc).__name__, exc,
        )

    session.last_llm_wire = {
        "wire_messages": snapshot,
        "source": source,
        "recorded_at_real": datetime.now().isoformat(timespec="seconds"),
        "recorded_at_virtual": virtual_iso,
        "reply_chars": -1,
        "note": note,
    }


def update_last_llm_wire_reply(
    session: "Session",
    *,
    reply_chars: int,
) -> None:
    """Update the most recent ``last_llm_wire`` with reply size.

    Best-effort: if ``last_llm_wire`` was never set (shouldn't happen in
    practice, but could during unit tests), silently returns. The
    ``reply_chars`` value persists until the next
    :func:`record_last_llm_wire` call — the preview UI uses it to show
    e.g. "0 chars → Gemini returned empty" indicators.

    Parameters
    ----------
    session
        The active session whose last wire we're annotating.
    reply_chars
        Character count of the assistant reply. 0 for empty reply,
        -1 for failure (the default left by :func:`record_last_llm_wire`).
    """
    slot = getattr(session, "last_llm_wire", None)
    if not isinstance(slot, dict):
        return
    slot["reply_chars"] = int(reply_chars)


def get_last_llm_wire(session: "Session") -> dict[str, Any] | None:
    """Return the stamped wire snapshot (or ``None`` if never set).

    Used by :func:`pipeline.prompt_builder.build_prompt_bundle` to
    embed into the returned :class:`PromptBundle`.
    """
    slot = getattr(session, "last_llm_wire", None)
    if not isinstance(slot, dict):
        return None
    # Return a defensive copy — the caller (PromptBundle → JSON) will
    # serialize it immediately so a shared reference is safe, but future
    # consumers who might mutate should also see a fresh view.
    return copy.deepcopy(slot)
