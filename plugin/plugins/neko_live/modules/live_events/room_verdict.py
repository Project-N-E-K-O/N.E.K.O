"""Bounded tally of the room's collective answer to a hook NEKO just posed.

Why this exists
---------------
The plugin already knows how to ASK. Whole material catalogs are built around
`either_or`, `tiny_choice`, `one_word_call` and `micro_challenge` shapes, and
`core/active_hook_answers.py` can recognise a single short viewer reply as an
answer. What was missing is the half that makes an ask worth making: nothing
counted what the room actually chose, so NEKO asked "A or B?", eight viewers
answered, and she never learned the result.

`docs/live-effect-literature-research-report.md` describes the basic unit of a
popular stream as:

    NEKO makes an event
    -> a group of viewers form a similar reaction
    -> NEKO recognises the group reaction
    -> it gets upgraded into a shared bit

This module owns the third step. Its bounded tally can count short answers by
DISTINCT viewer and report one privacy-safe verdict the prompt can announce.
It deliberately does not open a ballot from `status="pushed"`: that status
only proves handoff to the host, so lifecycle backflow is required before the
plugin can know the audience actually received the question.

Rules that keep it honest:

* one viewer, one vote — the first answer from a uid counts, later ones do not,
  so a single person cannot manufacture a landslide;
* a verdict needs at least two distinct viewers, mirroring the repeated-signal
  rule, because one reply is a reply and not a room verdict;
* a ballot expires; a result announced a minute late is not a payoff;
* a verdict is announced at most once, then the ballot closes — reading the
  same result twice is exactly the repetition the anti-repeat windows exist to
  prevent.

Boundaries: session-scoped and bounded, no timer, no model call, no network, no
persistence. It never schedules a turn and never grants speaking authority.
Answer tokens are short normalized strings; like the RoomPulse representative
example they may reach the prompt but never `status()` or audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


ROOM_VERDICT_VERSION = 0
# How long a ballot stays open. Long enough for a slow room to answer, short
# enough that the announcement still belongs to the moment that prompted it.
ROOM_VERDICT_WINDOW_SECONDS = 60.0
# A verdict needs a room, not a viewer.
ROOM_VERDICT_MIN_VOTERS = 2
# Distinct answer tokens tracked per ballot; beyond this the ballot is treated
# as free-form chatter rather than a choice.
ROOM_VERDICT_MAX_OPTIONS = 6
# Voters recorded per ballot.
ROOM_VERDICT_MAX_VOTERS = 64
# A winner needs this share of votes to be called decisive rather than split.
ROOM_VERDICT_DECISIVE_RATIO = 0.6
ROOM_VERDICT_ANSWER_MAX_CHARS = 8
ROOM_VERDICT_PROMPT_MAX_CHARS = 96

# Host-beat shapes that actually ask the room to pick something. Kept aligned
# with SceneState's viewer-choice shapes; a beat that does not ask must never
# open a ballot, or ordinary short danmaku would be miscounted as votes.
CHOICE_SHAPES = {
    "either_or",
    "micro_challenge",
    "one_word_call",
    "small_challenge",
    "tiny_choice",
}
_HOOK_SOURCES = {"active_engagement", "idle_hosting", "warmup_hosting"}


@dataclass(frozen=True, slots=True)
class RoomVerdictPrompt:
    text: str = ""
    reason: str = "no_ballot"
    winner: str = ""
    voters: int = 0

    @property
    def characters(self) -> int:
        return len(self.text)


class RoomVerdict:
    """One open ballot at a time; pure state, no scheduling or output."""

    def __init__(self, *, now: Callable[[], float]) -> None:
        self._now = now
        self.reset()

    def reset(self) -> None:
        self._close_ballot()
        self._ballots_opened = 0
        self._delivery_unconfirmed_count = 0
        self._verdicts_announced = 0
        self._last_reason = ""

    def _close_ballot(self) -> None:
        self._open = False
        self._opened_at = 0.0
        self._shape = ""
        self._votes: dict[str, int] = {}
        self._voters: set[str] = set()
        self._announced = False

    # ── ballot lifecycle ────────────────────────────────────────────────
    def observe_result(self, result: Any) -> bool:
        """Suppress an unconfirmed ballot after host handoff.

        ``status="pushed"`` does not prove that the question was played. A
        valid choice-shaped handoff therefore closes any previous ballot and
        records a privacy-safe reason, but never opens a new ballot.
        """
        if not isinstance(result, Mapping):
            return False
        if str(result.get("status") or "") != "pushed":
            return False
        event = result.get("event")
        if not isinstance(event, Mapping):
            return False
        source = str(event.get("source") or "")
        if source not in _HOOK_SOURCES:
            return False
        shape = self._shape_of(event)
        if shape not in CHOICE_SHAPES:
            # A beat that did not ask must not turn later short danmaku into
            # votes for a question nobody was asked.
            return False
        self._close_ballot()
        self._delivery_unconfirmed_count += 1
        self._last_reason = "delivery_unconfirmed"
        return False

    def _open_confirmed_ballot(self, shape: str) -> bool:
        """Open after correlated playback confirmation becomes available.

        This private seam keeps the tally independently testable without
        inventing a host lifecycle event or treating handoff as playback.
        """
        if shape not in CHOICE_SHAPES:
            return False
        self._close_ballot()
        self._open = True
        self._opened_at = self._now()
        self._shape = shape
        self._ballots_opened += 1
        return True

    def observe_answer(self, *, uid: Any, text: Any) -> bool:
        """Count one short viewer reply. Returns True when it was counted."""
        if not self._open:
            return False
        if self._expired():
            self._close_ballot()
            return False
        token = self._normalize(text)
        if not token:
            return False
        voter = str(uid or "").strip()
        if not voter:
            return False
        if voter in self._voters:
            # One viewer, one vote: a landslide must come from the room.
            return False
        if len(self._voters) >= ROOM_VERDICT_MAX_VOTERS:
            return False
        if token not in self._votes and len(self._votes) >= ROOM_VERDICT_MAX_OPTIONS:
            # Too many distinct answers: this is chatter, not a choice.
            return False
        self._voters.add(voter)
        self._votes[token] = self._votes.get(token, 0) + 1
        return True

    # ── verdict ─────────────────────────────────────────────────────────
    def verdict_prompt(self) -> RoomVerdictPrompt:
        """Announce the room's choice at most once per ballot."""
        if not self._open:
            # A later prompt projection must not erase the more useful reason
            # that explains why a choice-shaped handoff never opened a ballot.
            if self._last_reason == "delivery_unconfirmed":
                return RoomVerdictPrompt(reason="no_ballot")
            return self._skip("no_ballot")
        if self._announced:
            return self._skip("already_announced")
        if self._expired():
            self._close_ballot()
            return self._skip("expired")
        total = sum(self._votes.values())
        if total < ROOM_VERDICT_MIN_VOTERS:
            return self._skip("too_few_voters")

        winner, top = max(self._votes.items(), key=lambda item: (item[1], item[0]))
        decisive = top / total >= ROOM_VERDICT_DECISIVE_RATIO
        tied = sum(1 for count in self._votes.values() if count == top) > 1
        text = self._render(
            winner,
            top=top,
            total=total,
            decisive=decisive,
            tied=tied,
        )
        if not text:
            return self._skip("empty")
        self._announced = True
        self._verdicts_announced += 1
        self._last_reason = ""
        return RoomVerdictPrompt(
            text=text,
            reason="announced",
            winner=winner,
            voters=total,
        )

    def winning_answer(self) -> str:
        """Winning token of the current ballot once it has a real verdict.

        Offered to RitualMemory: a phrase the room collectively converged on is
        the strongest ritual candidate a stream produces.
        """
        if not self._open or self._expired():
            return ""
        total = sum(self._votes.values())
        if total < ROOM_VERDICT_MIN_VOTERS:
            return ""
        winner, top = max(self._votes.items(), key=lambda item: (item[1], item[0]))
        return winner if top / total >= ROOM_VERDICT_DECISIVE_RATIO else ""

    # ── projection ──────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Counts and reason codes only — never an answer token."""
        return {
            "room_verdict_version": ROOM_VERDICT_VERSION,
            "room_verdict_ballot_open": bool(self._open and not self._expired()),
            "room_verdict_ballots_opened": self._ballots_opened,
            "room_verdict_delivery_unconfirmed_count": (
                self._delivery_unconfirmed_count
            ),
            "room_verdict_announced_count": self._verdicts_announced,
            "room_verdict_current_voters": len(self._voters),
            "room_verdict_current_options": len(self._votes),
            "room_verdict_last_reason": self._last_reason or "",
        }

    # ── internals ───────────────────────────────────────────────────────
    def _skip(self, reason: str) -> RoomVerdictPrompt:
        self._last_reason = reason
        return RoomVerdictPrompt(reason=reason)

    def _expired(self) -> bool:
        return self._now() - self._opened_at > ROOM_VERDICT_WINDOW_SECONDS

    def _render(
        self,
        winner: str,
        *,
        top: int,
        total: int,
        decisive: bool,
        tied: bool,
    ) -> str:
        if not winner:
            return ""
        if decisive:
            body = f"房间基本都选了「{winner}」（{top}/{total}）"
        elif tied:
            body = f"房间意见分开了，目前打平（最高票 {top}/{total}）"
        else:
            body = f"房间意见分开了，「{winner}」暂时领先（{top}/{total}）"
        text = f"[room verdict] {body}，认一下这个结果再往下走，不要重新提问。"
        return text[:ROOM_VERDICT_PROMPT_MAX_CHARS]

    @staticmethod
    def _shape_of(event: Mapping[str, Any]) -> str:
        for key in ("topic_shape", "host_beat_shape"):
            value = str(event.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _normalize(text: Any) -> str:
        dense = "".join(ch for ch in str(text or "") if not ch.isspace())
        if not dense or len(dense) > ROOM_VERDICT_ANSWER_MAX_CHARS:
            # Long replies are conversation, not ballots.
            return ""
        return dense.casefold()
