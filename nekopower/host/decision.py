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

"""The decision model: what a dispatched sentence is, and who gets it done.

A replaceable part of the task brain (design section 6.7). It has no memory
of its own: every call is handed the sentence, the plugins and the current
commissions, and returns a conclusion.

``DecisionModel`` is the interface; a different implementation (an embedding
model, for instance) only has to provide its two methods:

* ``triage``, the first round. What the sentence does with the work there
  is: answers what waits for the user, stops some of it, adds to it, or is
  new; is it a repeat; and are there candidates. While there is work it may
  be about, one small-model request says which (``read_under_way``); the
  rest is local, but for the small-model coarse screen it runs once when
  nothing matches locally and no task agent can be started, before
  concluding that nothing can do this.
* ``select``, the second round. Chooses a plugin entry and fills in its
  arguments; plugins come first. When no entry fits or the task needs several
  steps, it hands over to a task agent. That is decided by the same request,
  never by a further one.

``LexicalDecisionModel`` is today's implementation: keyword patterns, BM25,
the small-model coarse screen and one fine selection. Its filter is as wide
as the analyzer's (``brain/task_executor.py``): the coarse stage runs only
when all plugin descriptions together exceed ``AGENT_PLUGIN_DESC_BM25_THRESHOLD``
tokens, and it keeps the union of the keyword hits, the ten best BM25 matches
and what the small model picks.

Model calls per path (asserted in ``tests/unit/nekopower/test_host_decision.py``):

* ``triage``: 0; 1 more while there is work the sentence may be about (her
  work under way, what waits for the user within it, her work that ended
  lately), 1 more when that reads it as the answer to a request for approval
  (what it chose, ``_read_choice``; each of the two waits up to
  ``UNDER_WAY_READ_SECONDS``), and 1 more (small model) when there is no
  local candidate and no task agent.
* ``probe`` for a query: always 0.
* ``select``: 1; 2 when the descriptions exceed the threshold (coarse screen
  plus fine selection), unless ``triage`` already ran the coarse screen, whose
  result is then reused; 0 when there is no plugin to choose from. One more
  only to correct an answer that names a plugin or entry that does not exist,
  as the analyzer did.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekopower.host.ledger import ACTIVE_STATUSES, Commission
from nekopower.host.selection import (
    annotate_keyword_hits,
    build_desc_lines,
    keyword_hits,
    similarity,
    stage1_filter,
    visible_entries,
)

logger = logging.getLogger(__name__)

TIER_COARSE = "coarse"
# Whether a commission is a side question (``LexicalDecisionModel.side_question``):
# on the small tier, like the coarse screen.
TIER_SIDE_QUESTION = "side_question"
# Whether a commission goes on with what a busy member is at, before another
# member is opened for it (``LexicalDecisionModel.goes_on_with``): the small
# tier as well, kept apart for what is counted of it.
TIER_BESIDE = "beside"
TIER_FINE = "fine"
# Where a commission belongs (``LexicalDecisionModel.belongs``): one request
# on the tier the settings name (``ORCHESTRATOR_MODEL_SLOT``), metered under
# a name of its own.
TIER_ORCHESTRATE = "orchestrate"
# The turn check: the small model again, with a budget of its own.
TIER_TURN_CHECK = "turn_check"
# What a dispatched sentence does with the work there is: answers what waits
# for the user, stops some of it, adds to it, or is new
# (``LexicalDecisionModel.read_under_way``). On the tier the settings name
# (``UNDER_WAY_READ_MODEL_SLOT``), metered under a name of its own.
TIER_UNDER_WAY = "under_way"
# What the answer to a request for approval chose, read apart once the
# sentence was read as that answer (``LexicalDecisionModel._read_choice``):
# on the same model, metered under a name of its own.
TIER_ANSWER_CHOICE = "answer_choice"
# A short description for a plugin whose manifest has none: the small model,
# once per plugin and description, in the background.
TIER_SHORT_DESCRIPTION = "short_description"
# The task agent space (``space_keeper.py``): the review of a finished
# commission, the summary of a project that is archived, and the tidying of
# the entries of one layer. All run in the background on the summary model
# and are metered under names of their own.
TIER_REVIEW = "review"
TIER_ARCHIVE = "archive"
TIER_CONSOLIDATE = "consolidate"
# The form of a new session, filled in for the user from what they wrote of
# it (``NekoPowerHost.draft_session``): one call when the user asks for it, on
# the summary model, metered under a name of its own.
TIER_SESSION_DRAFT = "session_draft"
_SUMMARY_TIERS = frozenset({TIER_FINE, TIER_REVIEW, TIER_ARCHIVE, TIER_CONSOLIDATE, TIER_SESSION_DRAFT})
_CALL_TYPES = {
    TIER_REVIEW: "task_agent_review",
    TIER_ARCHIVE: "task_agent_archive",
    TIER_CONSOLIDATE: "task_agent_consolidate",
    TIER_SESSION_DRAFT: "task_agent_session_draft",
    TIER_ORCHESTRATE: "task_agent_orchestrate",
    TIER_UNDER_WAY: "task_agent_under_way",
    TIER_ANSWER_CHOICE: "task_agent_answer_choice",
}
_FINE_ATTEMPTS = 3
_FINE_RETRY_DELAYS = (1.0, 2.0)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DecisionRequest:
    """Everything one decision is based on."""

    # The sentence she dispatched and the user's own words.
    dispatch_text: str
    user_text: str = ""
    lang: str = "en"
    # The plugins selection may choose from (running, visible).
    plugins: list[dict[str, Any]] = field(default_factory=list)
    # Her commissions that are under way, newest first. Commissions her
    # task agents started for themselves are not among them.
    active: list[Commission] = field(default_factory=list)
    # Commissions started by her task agents that wait for the user. Their
    # questions reach the user too, so an answer can be about them; a stop,
    # a repeat or an addition never is.
    answerable: list[Commission] = field(default_factory=list)
    # Commissions started by her task agents that are under way (the members
    # of a team among them): shown with the commission they work for.
    children: list[Commission] = field(default_factory=list)
    # What the user calls the familiar of each session the commissions are
    # worked on or were handed on in, by session id, in the user's language
    # (``Scout cat "links"``).
    familiars: dict[str, str] = field(default_factory=dict)
    # Her commissions a task agent worked on that are over (done, failed or
    # stopped) and not long so, newest first. A sentence that adds to one of
    # them goes on with it in its session.
    recent: list[Commission] = field(default_factory=list)
    task_agent_available: bool = False
    # Rendered task preferences for the fine selection (already within budget).
    preferences: str = ""

    @property
    def match_text(self) -> str:
        """Return the text keyword patterns and BM25 read: the user's words and hers."""
        user = self.user_text.strip()
        task = self.dispatch_text.strip()
        if user and task and user != task:
            return f"{user}\n{task}"
        return user or task


@dataclass(slots=True)
class Triage:
    """Conclusion of the first round."""

    # "new", "duplicate", "stop", "tell", "answer", "ambiguous",
    # "nothing_active", "none" (nothing can do this), "continue" (it goes on
    # with a commission that is over: the target) or, from a probe that may
    # not ask a model, "unsure".
    kind: str
    # Commission ids this is about; several for "ambiguous".
    targets: list[str] = field(default_factory=list)
    # For "none": why. For "answer" to a permission prompt: the choice.
    reason: str = ""
    choice: str | None = None
    keyword_hit_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    # What the coarse screen returned, when this round had to run it.
    coarse_ids: list[str] | None = None
    model_calls: int = 0
    # What the sentence does with the work there is could not be read (the
    # request failed, came too late or said nothing it could be): it is
    # taken for new work, and she is told it could not be told.
    unread: bool = False
    # An answer that chose "always" or "session", read from the user's words:
    # it allows the one request ("once"), and she is told the buttons do more.
    narrowed: bool = False
    # An "ambiguous" among several requests for approval: what is said cannot
    # tell which (it is asked whatever is said), and she is told the buttons
    # on each question can.
    by_buttons: bool = False
    # For "stop" of the items it names ("these") while more of their
    # project's, familiar's or plugin's work was left out of the list: those
    # commission ids, which are not stopped. The receipt says they go on.
    left_running: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Selection:
    """Conclusion of the second round."""

    # "plugin", "task_agent", "duplicate" or "none".
    route: str
    plugin_id: str | None = None
    entry_id: str | None = None
    plugin_args: dict[str, Any] | None = None
    task_description: str = ""
    # The commission this repeats, for "duplicate".
    duplicate_of: str | None = None
    # A lasting instruction the sentence stated, if any.
    preference: str | None = None
    reason: str = ""
    # The plugins the fine selection was shown; a task agent starts with these.
    shown_plugin_ids: list[str] = field(default_factory=list)
    model_calls: int = 0


@dataclass(slots=True)
class TurnVerdict:
    """What the turn check concluded about one finished turn."""

    # "none", "unable", "promised" or "claimed_done".
    kind: str
    # The action the turn was about, in one sentence; empty for "none".
    task: str = ""
    model_calls: int = 0


class DecisionLLM(Protocol):
    """The model tiers the lexical decision model uses."""

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        """Return the text of one completion of a tier (``TIER_COARSE``, ``TIER_FINE``, ``TIER_TURN_CHECK``, ``TIER_SHORT_DESCRIPTION``, ``TIER_REVIEW``, ``TIER_ARCHIVE``, ``TIER_CONSOLIDATE``, ``TIER_SESSION_DRAFT``, ``TIER_ORCHESTRATE``, ``TIER_UNDER_WAY``, ``TIER_ANSWER_CHOICE``)."""


class DecisionModel(Protocol):
    """The replaceable interface; see the module docstring."""

    async def triage(self, request: DecisionRequest) -> Triage:
        """Run the first round."""

    async def probe(self, request: DecisionRequest, *, ask_model: bool = True) -> Triage:
        """Say whether anything can take this on: "new", "none" or "unsure".

        The part of the first round that looks for candidates. A dispatch
        ends its first round here. A "can this be done" query calls it with
        ``ask_model=False``: a query never asks a model, so where a dispatch
        would run the coarse screen the answer is "unsure" instead of a
        conclusion.
        """

    async def select(self, request: DecisionRequest, triage: Triage) -> Selection:
        """Run the second round."""

    async def judge_turn(self, user_text: str, reply_text: str, lang: str) -> TurnVerdict:
        """Say what her reply did with the user's request, when she called no task tool.

        One of: nothing to do or she declined on purpose ("none"); she said she
        cannot ("unable"); she agreed to do it ("promised"); she said it is
        done ("claimed_done"). It does not judge whether it can be done.
        """


# ---------------------------------------------------------------------------
# The configured model tiers
# ---------------------------------------------------------------------------


class ConfiguredDecisionLLM:
    """``DecisionLLM`` on the models the analyzer used.

    The coarse screen, the turn check and short descriptions run on the small
    latency-sensitive tier (``emotion``); the orchestrator and the readings of
    what a request does with the work there is and of what an answer chose
    run on the models the settings name (``ORCHESTRATOR_MODEL_SLOT``,
    ``UNDER_WAY_READ_MODEL_SLOT``: ``recognition``); the fine selection, the reviews of
    finished commissions, the summaries of archived projects, the tidying
    of the task agent space and the form of a new session on the ``summary`` tier. Endpoints come from the user's configuration as they
    are, ``http://`` addresses included.
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[Any, ...], Any] = {}

    @staticmethod
    def _slot(tier: str) -> str:
        from config.nekopower_settings import (
            UNDER_WAY_READ_MODEL_SLOT,
            ORCHESTRATOR_MODEL_SLOT,
            SESSION_DRAFT_MODEL_SLOT,
            SPACE_ARCHIVE_MODEL_SLOT,
            SPACE_CONSOLIDATE_MODEL_SLOT,
            TASK_AGENT_REVIEW_MODEL_SLOT,
        )

        # The tiers of the task agent space say in the settings which model
        # they use, so that it is changed there and nowhere else.
        return {
            TIER_REVIEW: TASK_AGENT_REVIEW_MODEL_SLOT,
            TIER_ARCHIVE: SPACE_ARCHIVE_MODEL_SLOT,
            TIER_CONSOLIDATE: SPACE_CONSOLIDATE_MODEL_SLOT,
            TIER_SESSION_DRAFT: SESSION_DRAFT_MODEL_SLOT,
            TIER_ORCHESTRATE: ORCHESTRATOR_MODEL_SLOT,
            TIER_UNDER_WAY: UNDER_WAY_READ_MODEL_SLOT,
            TIER_ANSWER_CHOICE: UNDER_WAY_READ_MODEL_SLOT,
        }.get(tier, "summary" if tier in _SUMMARY_TIERS else "emotion")

    async def decide(
        self, tier: str, site: str, *, state: str, questions: dict[str, Any], rehost: bool = True,
    ) -> dict[str, Any] | None:
        """Ask a site's decision questions on the route the user's vendor allows; None to use the chat prompt.

        The vendor is the decision slot's (native decision) or the chat model
        of *tier*'s slot (rehost); see :func:`utils.decision_client.ask_site_questions`.
        """
        from utils.config_manager import get_config_manager
        from utils.decision_client import ask_site_questions, site_question_would_send

        from config.nekopower_settings import DECISION_QUESTION_TIMEOUT_SECONDS

        manager = get_config_manager(migrate=False)
        chat_config = await asyncio.to_thread(manager.get_model_api_config, self._slot(tier))
        decision_config = await asyncio.to_thread(manager.get_model_api_config, "decision")
        if not site_question_would_send(site, decision_config=decision_config, chat_config=chat_config, rehost=rehost):
            # Nothing is sent (the site's chat route, or the native endpoint
            # in its cooldown after an error): the chat prompt is the only request.
            return None
        answers = await ask_site_questions(
            site,
            state=state,
            questions=questions,
            chat_config=chat_config,
            decision_config=decision_config,
            config_manager=manager,
            rehost=rehost,
            timeout=DECISION_QUESTION_TIMEOUT_SECONDS,
            call_type=_CALL_TYPES.get(tier, "agent"),
        )
        # Sent but missed (an error, a timeout, no probabilities): empty, so a
        # caller can count the request it made.
        return answers if answers is not None else {}

    async def _client(self, tier: str, budget: int) -> Any:
        from utils.config_manager import get_config_manager
        from utils.llm_client import create_chat_llm_async

        slot = self._slot(tier)
        config = await asyncio.to_thread(get_config_manager(migrate=False).get_model_api_config, slot)
        key = (slot, config.get("api_key"), config.get("base_url"), config.get("model"), config.get("provider_type"), budget)
        client = self._clients.get(key)
        if client is None:
            # Built off the event loop: the first client of a provider loads its SDK.
            client = await create_chat_llm_async(
                model=config["model"],
                base_url=config["base_url"],
                api_key=config["api_key"],
                max_completion_tokens=budget,
                max_retries=0,
                timeout=120.0,
                provider_type=config.get("provider_type"),
            )
            self._clients[key] = client
        return client

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        from config import AGENT_PLUGIN_COARSE_MAX_TOKENS, AGENT_PLUGIN_FULL_MAX_TOKENS, AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
        from utils.token_tracker import set_call_type

        # Same usage bucket as the analyzer it takes over from; what the
        # task agent space asks is counted apart.
        set_call_type(_CALL_TYPES.get(tier, "agent"))
        from config.nekopower_settings import (
            SESSION_DRAFT_OUTPUT_MAX_TOKENS,
            SPACE_ARCHIVE_OUTPUT_MAX_TOKENS,
            SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS,
            TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS,
            TURN_CHECK_OUTPUT_MAX_TOKENS,
        )

        from config.nekopower_settings import (
            UNDER_WAY_READ_OUTPUT_MAX_TOKENS,
            BESIDE_JUDGE_OUTPUT_MAX_TOKENS,
            ORCHESTRATOR_OUTPUT_MAX_TOKENS,
            SIDE_QUESTION_OUTPUT_MAX_TOKENS,
        )

        budget = {
            TIER_COARSE: AGENT_PLUGIN_COARSE_MAX_TOKENS,
            TIER_SIDE_QUESTION: SIDE_QUESTION_OUTPUT_MAX_TOKENS,
            TIER_BESIDE: BESIDE_JUDGE_OUTPUT_MAX_TOKENS,
            TIER_TURN_CHECK: TURN_CHECK_OUTPUT_MAX_TOKENS,
            TIER_SHORT_DESCRIPTION: AGENT_PLUGIN_SHORTDESC_MAX_TOKENS,
            TIER_REVIEW: TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS,
            TIER_ARCHIVE: SPACE_ARCHIVE_OUTPUT_MAX_TOKENS,
            TIER_CONSOLIDATE: SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS,
            TIER_SESSION_DRAFT: SESSION_DRAFT_OUTPUT_MAX_TOKENS,
            TIER_ORCHESTRATE: ORCHESTRATOR_OUTPUT_MAX_TOKENS,
            TIER_UNDER_WAY: UNDER_WAY_READ_OUTPUT_MAX_TOKENS,
            TIER_ANSWER_CHOICE: UNDER_WAY_READ_OUTPUT_MAX_TOKENS,
        }.get(tier, AGENT_PLUGIN_FULL_MAX_TOKENS)
        client = await self._client(tier, budget)
        response = await client.ainvoke(messages)
        return str(getattr(response, "content", "") or "")

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            closer = getattr(client, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: BLE001 - closing is best effort
                logger.debug("closing a decision model client failed", exc_info=True)


# ---------------------------------------------------------------------------
# Local reading of a dispatched sentence
# ---------------------------------------------------------------------------


def _patterns(names: Sequence[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in names]


class _Cues:
    """Compiled cue patterns, built on first use."""

    _loaded: dict[str, list[re.Pattern[str]]] | None = None

    @classmethod
    def get(cls, name: str) -> list[re.Pattern[str]]:
        if cls._loaded is None:
            from config.prompts import prompts_nekopower_brain as texts

            cls._loaded = {
                "generic": _patterns(texts.GENERIC_WORD_PATTERNS),
                "supplement": _patterns(texts.SUPPLEMENT_CUE_PATTERNS),
                "stop_words": _patterns(texts.UNDER_WAY_STOP_WORD_PATTERNS),
                "capability_question": _patterns(texts.CAPABILITY_QUESTION_PATTERNS),
            }
        return cls._loaded[name]


def has_cue(name: str, text: str) -> bool:
    """Return whether any pattern of the named cue list occurs in ``text``."""
    return any(pattern.search(text) for pattern in _Cues.get(name))


def strip_cues(name: str, text: str) -> str:
    """Return ``text`` with every match of the named cue list removed."""
    for pattern in _Cues.get(name):
        text = pattern.sub(" ", text)
    return text


def looks_like_a_task(external_intent: float | None, user_text: str, plugins: Sequence[dict[str, Any]]) -> bool:
    """The free gate in front of the turn check: could this turn be a request to get something done?

    The same rule the analyzer applied before spending a model call. The turn
    is held back only when the input-time score is known and below the
    threshold and nothing local speaks for a task: no plugin keyword occurs in
    the user's words, and every plugin that could take a task has keywords to
    be matched by (one without any can only be recognised by a model, so its
    presence lets the turn through).
    """
    from config.nekopower_settings import TURN_CHECK_INTENT_THRESHOLD

    if external_intent is None or external_intent >= TURN_CHECK_INTENT_THRESHOLD:
        return True
    if keyword_hits(user_text, plugins):
        return True
    for plugin in plugins:
        keywords = plugin.get("keywords")
        if not (isinstance(keywords, list) and keywords) and visible_entries(plugin):
            return True
    return False


def is_capability_question(text: str) -> bool:
    """Return whether a query asks if something can be done."""
    return has_cue("capability_question", text)


def commission_text(commission: Commission) -> str:
    return " ".join(part for part in (commission.title, commission.dispatch_text, commission.user_text) if part)


# What the answer to a request for approval may choose (``LexicalDecisionModel.read_under_way``).
ANSWER_CHOICES = ("always", "session", "once", "deny")

# Where an item shown to that reading stands: her work under way, a question
# within it that waits for the user (asked by a task agent it started), or
# her work that ended lately.
_UNDER_WAY, _ASKED, _ENDED = "under_way", "asked", "ended"


def quote_data(text: str) -> str:
    """Return words the host did not write as the reading shows them: a JSON string.

    Titles, projects, commands, paths, questions and what work ended with
    are written by task agents, the user or her. Quoted, with the quotes and
    line breaks within escaped, nothing written in them can end the quote,
    start an item of its own or pass for the host's words; the prompt says
    what is quoted is what the request is sorted by, never instructions.
    Every line break ``str.splitlines`` knows is escaped: JSON leaves NEL,
    the line separator and the paragraph separator as they are.
    """
    quoted = json.dumps(text, ensure_ascii=False)
    for char in _LINE_BREAKS_JSON_KEEPS:
        quoted = quoted.replace(char, f"\\u{ord(char):04x}")
    return quoted


# Line breaks to ``str.splitlines`` (and to many a tokenizer and model) that
# ``json.dumps(..., ensure_ascii=False)`` leaves unescaped: NEL, LINE
# SEPARATOR, PARAGRAPH SEPARATOR.
_LINE_BREAKS_JSON_KEEPS = ("\x85", "\u2028", "\u2029")


def _cut(line: str, max_tokens: int) -> str:
    """Return a line of the reading cut to ``max_tokens``, a quote it cuts into closed again (``…"``)."""
    from utils.tokenize import count_tokens, truncate_to_tokens

    if count_tokens(line) <= max_tokens:
        return line
    # Room for what closes the quote, as the counter in use counts it (the
    # bytes of it where there is no tokenizer); and the whole counted again,
    # as what is put together may count otherwise than its parts.
    room = max(1, max_tokens - count_tokens('…"'))
    for _ in range(4):
        cut = _closed(truncate_to_tokens(line, room))
        over = count_tokens(cut) - max_tokens
        if over <= 0:
            break
        room = max(1, room - over)
    return cut


def _closed(cut: str) -> str:
    """Return the start of a line of the reading with a quote it ends within closed again (``…"``)."""
    within = False
    index = 0
    while index < len(cut):
        if within and cut[index] == "\\":
            # An escape is kept whole or not at all (``\"``, ``\n``, ``\u0007``).
            length = 6 if cut[index + 1 : index + 2] == "u" else 2
            if index + length > len(cut):
                cut = cut[:index]
                break
            index += length
            continue
        if cut[index] == '"':
            within = not within
        index += 1
    return f'{cut}…"' if within else cut


def _asked(commission: Commission) -> tuple[str, str]:
    """Return what a waiting commission asks, as the reading is shown it: the kind, and the question.

    A request for approval says what is to be allowed with the answers on
    offer (``describe_permission``, in English; the command or the path the
    user decides by stays as it was written). The question is quoted
    (``quote_data``) and fits ``UNDER_WAY_READ_QUESTION_MAX_TOKENS`` quoted:
    what its escapes add is taken from what is shown of it (the command or
    the path gives way first, the answers on offer stay while they fit).
    """
    from config.nekopower_settings import UNDER_WAY_READ_QUESTION_MAX_TOKENS
    from config.prompts.prompts_nekopower_brain import QUESTION_TEXTS
    from config.prompts.prompts_sys import _loc
    from nekopower.host.reports import _question_lines, describe_permission
    from utils.tokenize import count_tokens, truncate_to_tokens

    question = commission.question() or {}
    kind = question.get("kind")
    if kind == "questions":
        whole = _question_lines(question.get("questions"), _loc(QUESTION_TEXTS, "en"))
    else:
        parts = (str(question.get("question") or "").strip(), str(question.get("details") or "").strip())
        whole = " ".join(part for part in parts if part)

    def quoted(room: int) -> str:
        if kind == "permission":
            return quote_data(truncate_to_tokens(describe_permission(question, "en", max_tokens=room), room))
        return quote_data(truncate_to_tokens(whole, room))

    limit = UNDER_WAY_READ_QUESTION_MAX_TOKENS
    # The two quotes, then what the escapes add: what is shown gives way
    # until it fits (it shrinks each time, so this ends).
    room = limit - 2
    asked = quoted(room)
    while count_tokens(asked) > limit and room > 1:
        room = max(1, room - (count_tokens(asked) - limit))
        asked = quoted(room)
    shown = kind if kind in ("permission", "confirmation") else "question"
    return shown, asked


def _item_line(number: int, commission: Commission, request: DecisionRequest, *, within: Commission | None = None) -> str:
    """Return one item of the reading: where it stands, what it is, its project, who is on it, and what it asks.

    What the host did not write is quoted (``quote_data``); what the user calls
    a familiar quotes its card's title already (``TaskBrain._familiars``).
    """
    from config.nekopower_settings import UNDER_WAY_READ_LINE_MAX_TOKENS
    from config.prompts.prompts_nekopower_brain import UNDER_WAY_QUESTION_KINDS, UNDER_WAY_STATUS_WORDS

    def status(item: Commission) -> str:
        return UNDER_WAY_STATUS_WORDS.get(item.status, item.status)

    about: list[str] = []
    if commission.project:
        about.append(f"project {quote_data(commission.project)}")
    if commission.route == "plugin" and commission.plugin_id:
        about.append(f"by the plugin {quote_data(commission.plugin_id)}")
    elif request.familiars.get(commission.session_id or ""):
        about.append(f"by {request.familiars[commission.session_id or '']}")
    if request.familiars.get(commission.handed_by or ""):
        about.append(f"handed on by {request.familiars[commission.handed_by or '']}")
    if within is not None:
        about.append(f"within {quote_data(within.title)}")
    line = f"{number}. [{status(commission)}] {quote_data(commission.title)}"
    if about:
        line += f" ({'; '.join(about)})"
    members = [item for item in request.children if item.parent_id == commission.id]

    def member(item: Commission) -> str:
        familiar = request.familiars.get(item.session_id or "")
        said = f"{status(item)}; by {familiar}" if familiar else status(item)
        return f"{quote_data(item.title)} ({said})"

    if members:
        line += "; its members: " + ", ".join(member(item) for item in members)
    ended = commission.result_summary or (commission.status_reason if commission.status == "failed" else "")
    if commission.status not in ACTIVE_STATUSES and ended:
        line += f"; it ended with: {quote_data(ended)}"
    line = _cut(line, UNDER_WAY_READ_LINE_MAX_TOKENS)
    if commission.status == "waiting_user" and commission.question():
        kind, asked = _asked(commission)
        line += f"; it waits for the user, {UNDER_WAY_QUESTION_KINDS[kind]}: {asked}"
    return line


def _shown(request: DecisionRequest) -> tuple[list[tuple[str, list[str]]], list[tuple[str, Commission]]]:
    """Return the items of the reading by section, and what each number stands for.

    Her work under way, newest first: past ``UNDER_WAY_READ_ROWS``, those
    that wait for the user and those whose words are closest to the sentence.
    What waits for the user within it, asked by task agents it started. Her
    work that ended lately, which a sentence may go on with in its session:
    only her own, worked on in a session (that goes on, as the task brain
    reads them), and only while a task agent can be started.
    """
    from config.nekopower_settings import UNDER_WAY_READ_ENDED_ROWS, UNDER_WAY_READ_ROWS

    under_way = list(request.active)
    if len(under_way) > UNDER_WAY_READ_ROWS:
        closest = sorted(
            under_way,
            key=lambda item: (item.status == "waiting_user", similarity(request.match_text, commission_text(item))),
            reverse=True,
        )[:UNDER_WAY_READ_ROWS]
        under_way = [item for item in under_way if item in closest]
    asked = [item for item in request.answerable if item.status == "waiting_user" and item.question()]
    asked = asked[:UNDER_WAY_READ_ROWS]
    ended: list[Commission] = []
    if request.task_agent_available:
        ended = [
            item
            for item in request.recent
            if item.parent_id is None and item.session_id
        ][:UNDER_WAY_READ_ENDED_ROWS]
    parents = {item.id: item for item in request.active}
    numbered: list[tuple[str, Commission]] = []
    sections: list[tuple[str, list[str]]] = []
    for heading, place, items in (
        ("UNDER WAY:", _UNDER_WAY, under_way),
        ("WAITING FOR THE USER, asked within the work above:", _ASKED, asked),
        ("ENDED LATELY:", _ENDED, ended),
    ):
        lines = []
        for item in items:
            numbered.append((place, item))
            lines.append(_item_line(len(numbered), item, request, within=parents.get(item.parent_id or "")))
        sections.append((heading, lines))
    return sections, numbered


def _offered(question: dict[str, Any]) -> set[str]:
    """Return the answers a question offers, as the user would say them.

    Its own options, and those of each question an ``ask_user_question``
    call put together (``questions[].options``, labels or plain strings).
    """

    def labels(options: Any) -> set[str]:
        if not isinstance(options, list):
            return set()
        import unicodedata

        said = (str(option.get("label") or "") if isinstance(option, dict) else str(option) for option in options)
        # One answer however its accents are written ("Sí" or "Si" with a
        # combining accent), as what was said is read (``_said``).
        return {unicodedata.normalize("NFC", label.strip()).casefold() for label in said if label.strip()}

    found = labels(question.get("options"))
    for asked in question.get("questions") if isinstance(question.get("questions"), list) else []:
        if isinstance(asked, dict):
            found |= labels(asked.get("options"))
    return found


def _said(label: str, spoken: str) -> bool:
    """Whether an answer offered was said.

    As a word of its own where words are written with spaces between them:
    "no" is not in "not" nor in "Noël", "sí" is not in "así". Next to
    writing that has no spaces between its words (Chinese, Japanese,
    Korean) it is a word all the same, and an answer written that way is
    looked for as it is.
    """
    import unicodedata

    label = unicodedata.normalize("NFC", label)
    spoken = unicodedata.normalize("NFC", spoken)
    if any(_unspaced(char) for char in label):
        return label in spoken
    for found in re.finditer(re.escape(label), spoken):
        before = spoken[found.start() - 1] if found.start() else ""
        after = spoken[found.end()] if found.end() < len(spoken) else ""
        if not _joins(before) and not _joins(after):
            return True
    return False


def _joins(char: str) -> bool:
    """Whether a character next to a word makes it part of a longer one.

    A letter or digit of writing with spaces between words, or a mark that
    combines with the letter before it (an accent NFC has no single letter
    for, a vowel sign of Devanagari). Not a mark that only says how a
    character is drawn: a variation selector (the Mongolian ones too), or
    an enclosing one like the keycap of "1" in an emoji (the option "1" is
    said in it).
    """
    import unicodedata

    if not char:
        # The start or the end of what was said.
        return False
    mark = unicodedata.category(char) in ("Mn", "Mc") and "VARIATION SELECTOR" not in unicodedata.name(char, "")
    return (char.isalnum() or mark) and not _unspaced(char)


def _unspaced(char: str) -> bool:
    """Whether a character is of writing with no spaces between its words.

    Chinese, Japanese and Korean, and Thai, Lao, Khmer and Burmese.
    """
    import unicodedata

    return unicodedata.name(char, "").startswith(
        (
            "CJK", "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA", "IDEOGRAPHIC", "HANGUL", "HALFWIDTH HANGUL",
            "BOPOMOFO", "THAI", "LAO", "KHMER", "MYANMAR",
        )
    )


def _kin(one: Commission, other: Commission) -> bool:
    """Whether two commissions are of one project, one familiar's (its session) or one plugin's."""
    return bool(
        (one.project and one.project == other.project)
        or (one.session_id and one.session_id == other.session_id)
        or (one.plugin_id and one.plugin_id == other.plugin_id)
    )


def _words_name(request: DecisionRequest, items: Sequence[Commission]) -> bool:
    """Whether the sentence shares a word with what tells each of these items apart.

    What each is (its title and the words it was asked with), its project,
    the familiar on it or that handed it on, its plugin, what it asks as the
    list shows it (``_asked``), and the same of its members. Of the sentence,
    words every request has (``orchestrator.content_tokens``) and words a
    stop says without naming anything (stop, all, the work in progress:
    ``UNDER_WAY_STOP_WORD_PATTERNS``) are left out. Each item must share a
    word: one the model picked beside one the sentence names is not named
    (owner's automatic review). Read where the list was cut only: a stop the
    model says is named, of items whose words the sentence does not share,
    is asked about (``read_under_way``).
    """
    from nekopower.host.orchestrator import content_tokens

    said = content_tokens(strip_cues("stop_words", request.match_text))
    if not said:
        return False
    plugins = {str(plugin.get("id") or ""): plugin for plugin in request.plugins}

    def words(item: Commission) -> list[str]:
        # (A plugin's id as it is and in words: "weather_lookup" is said
        # either way. Greptile, third round.)
        plugin_id = item.plugin_id or ""
        found = [commission_text(item), item.project or "", plugin_id, re.sub(r"[_.\-]+", " ", plugin_id)]
        found += [request.familiars.get(session or "", "") for session in (item.session_id, item.handed_by)]
        plugin = plugins.get(item.plugin_id or "") or {}
        found.append(str(plugin.get("name") or ""))
        keywords = plugin.get("keywords")
        if isinstance(keywords, list):
            found += [str(keyword) for keyword in keywords]
        if item.question():
            # As the list shows it (``_asked``): the command or path a request
            # for approval asks about, each question one put together asks
            # and the answers they offer, a report's details (Greptile, first
            # round: "stop the npm install one"). Unquoted first: a line break
            # quoted is "\n", which would join the word after it (CodeRabbit,
            # second round).
            found.append(json.loads(_asked(item)[1]))
        return found

    for item in items:
        texts = words(item)
        for member in request.children:
            if member.parent_id == item.id:
                texts += words(member)
        if not said & content_tokens(" ".join(text for text in texts if text)):
            return False
    return True


def _numbers(value: Any, count: int) -> list[int]:
    """Return the item numbers a reading names, in range and once each."""
    seen: list[int] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and item.strip().isascii() and item.strip().isdecimal():
            item = int(item.strip())
        if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= count and item not in seen:
            seen.append(item)
    return seen


def can_be_repeated(commission: Commission) -> bool:
    """Return whether a new dispatch can be a repeat of this commission.

    Not a commission a task agent started for itself: she never dispatched it.
    Not a direct plugin call left ``blocked`` either (the plugin asked for a
    confirmation or a clarification): nothing takes it up again, so the same
    request dispatched once more is the confirmed one and has to run.
    """
    if commission.parent_id is not None:
        return False
    return not (commission.status == "blocked" and commission.route == "plugin")


def find_duplicate(dispatch_text: str, user_text: str, active: Sequence[Commission]) -> Commission | None:
    """Return the commission under way that this sentence repeats, if any.

    A repeat says nearly the same thing (``DUPLICATE_SIMILARITY``); when the
    user's own words are identical a lower bar applies, because then the same
    request was dispatched twice. Only commissions ``can_be_repeated`` accepts
    are compared.
    """
    from config.nekopower_settings import DUPLICATE_SIMILARITY, DUPLICATE_SIMILARITY_SAME_USER_TEXT

    user_text = user_text.strip()
    best: Commission | None = None
    best_score = 0.0
    for commission in active:
        if not can_be_repeated(commission):
            continue
        score = similarity(dispatch_text, commission.dispatch_text)
        bar = DUPLICATE_SIMILARITY
        if user_text and user_text == commission.user_text.strip():
            bar = DUPLICATE_SIMILARITY_SAME_USER_TEXT
        if score >= bar and score > best_score:
            best, best_score = commission, score
    return best


# ---------------------------------------------------------------------------
# Parsing a model's JSON answer
# ---------------------------------------------------------------------------

_EMBEDDED_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\}")
_OPENING_FENCE_RE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\r?\n?")
_CLOSING_FENCE_RE = re.compile(r"\r?\n?[ \t]*```\Z")


def _strip_fences(text: str) -> str:
    """Remove a Markdown fence around the whole answer; fences inside it stay."""
    text = text.strip()
    if text.startswith("```"):
        text = _CLOSING_FENCE_RE.sub("", _OPENING_FENCE_RE.sub("", text, count=1), count=1).strip()
    return text


def _drop_trailing_commas(text: str) -> str:
    """Remove commas right before a closing brace or bracket, outside strings."""
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == ",":
            ahead = index + 1
            while ahead < len(text) and text[ahead] in " \t\r\n":
                ahead += 1
            if ahead < len(text) and text[ahead] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _loads(text: str) -> Any:
    """Parse ``text`` as it is; only when that fails, again without trailing commas."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_drop_trailing_commas(text))


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Return the JSON object in a model answer, tolerating fences and trailing commas.

    The answer is parsed as it is first. Only when that fails are trailing
    commas removed, and never inside a string: what the model wrote into an
    argument reaches the plugin unchanged.
    """
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        value = _loads(text)
    except ValueError:
        match = _EMBEDDED_OBJECT_RE.search(text)
        if match is None:
            return None
        try:
            value = _loads(match.group(0))
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _count(name: str, **dims: Any) -> None:
    """Bump one of the analyzer's selection counters; never raises."""
    try:
        from utils.instrument import counter

        counter(name, **dims)
    except Exception:  # noqa: BLE001 - counting must not affect a decision
        pass


# ---------------------------------------------------------------------------
# The implementation in use
# ---------------------------------------------------------------------------


# Readings of the decision questions (owner decisions D16, D19, D28).
# A plugin of the coarse screen is kept when its choice probability reaches this.
COARSE_SCREEN_DECISION_THRESHOLD = 0.05
# P(look) at or above this makes a commission a side question.
SIDE_QUESTION_LOOK_THRESHOLD = 0.5
# Where a commission belongs: a candidate is chosen when its two options hold at
# least this much and more than "none"; it is the next round of a recurring job
# when that option holds at least this share of the candidate's.
BELONGS_ATTACH_THRESHOLD = 0.3
BELONGS_RECURRENCE_SHARE = 0.4
# The turn check's second look scales the probability of "nothing to act on" by
# this before the verdicts are compared.
TURN_CHECK_NONE_WEIGHT = 0.75


def _option_probabilities(answer: Any) -> dict[str, float]:
    """An answer's option probabilities; empty without them (a bare choice is not read)."""
    probs: dict[str, float] = {}
    for key, value in (getattr(answer, "probabilities", None) or {}).items():
        try:
            probs[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return probs


def _candidate_count(request_text: str) -> int:
    """How many numbered candidates follow ``CANDIDATES:`` in an orchestrator request."""
    lines = (request_text or "").splitlines()
    try:
        start = lines.index("CANDIDATES:")
    except ValueError:
        return 0
    count = 0
    for line in lines[start + 1:]:
        if line.startswith(f"{count + 1}. "):
            count += 1
    return count


def belongs_answer_from(answer: Any, candidates: int) -> str | None:
    """The orchestrator's JSON answer (``goes_to``, ``relation``) read off a decision answer, or None."""
    probs = _option_probabilities(answer)
    if not probs or candidates < 1:
        return None
    mass = {k: probs.get(f"c{k}_follow_up", 0.0) + probs.get(f"c{k}_recurrence", 0.0) for k in range(1, candidates + 1)}
    best = max(mass, key=lambda k: mass[k])
    if mass[best] < BELONGS_ATTACH_THRESHOLD or mass[best] <= probs.get("none", 0.0):
        return json.dumps({"goes_to": 0, "relation": "new"})
    share = probs.get(f"c{best}_recurrence", 0.0) / mass[best] if mass[best] > 0 else 0.0
    relation = "recurrence" if share >= BELONGS_RECURRENCE_SHARE else "follow_up"
    return json.dumps({"goes_to": best, "relation": relation})


def turn_verdict_from(answer: Any) -> str | None:
    """The turn check verdict the second look gives, or None without probabilities."""
    from config.prompts.prompts_nekopower_brain import TURN_CHECK_DECISION_VERDICT_OF, TURN_CHECK_VERDICTS

    probs = _option_probabilities(answer)
    if not probs:
        return None
    mass = {verdict: 0.0 for verdict in TURN_CHECK_VERDICTS}
    for option, p in probs.items():
        verdict = TURN_CHECK_DECISION_VERDICT_OF.get(option)
        if verdict is not None:
            mass[verdict] += p
    mass["none"] *= TURN_CHECK_NONE_WEIGHT
    return max(TURN_CHECK_VERDICTS, key=lambda verdict: mass[verdict])


class LexicalDecisionModel:
    """Keyword patterns, BM25, the small-model coarse screen and one fine selection."""

    def __init__(self, llm: DecisionLLM) -> None:
        self._llm = llm

    async def _decided(
        self, tier: str, site: str, state: str, questions: dict[str, Any], *, rehost: bool,
    ) -> dict[str, Any] | None:
        """Answers to a site's decision questions (D26): None when none were sent, empty when sent and missed."""
        decide = getattr(self._llm, "decide", None)
        if decide is None:
            return None
        try:
            return await decide(tier, site, state=state, questions=questions, rehost=rehost)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the chat prompt is asked instead
            logger.info("decision question for %s skipped: %s", site, type(exc).__name__)
            return {}  # may have been sent: counted as a request that missed

    async def side_question(self, dispatch_text: str, user_text: str, waiting_for: str = "") -> bool | None:
        """Say whether a commission is a side question: answered by looking, with nothing changed. None when it cannot be told.

        One request on the small tier (``TIER_SIDE_QUESTION``). Asked only
        where it decides something: a commission of a project whose session
        another one holds, which a side question need not wait for
        (``TaskAgentManager._carry_on_in_copy``). Anything else, an answer
        that cannot be read included, is work, and waits for the session.
        ``waiting_for`` is the question the session waits for the user to
        answer, if it does: a request that goes on with it is work.
        """
        from config.nekopower_settings import DISPATCH_TEXT_MAX_TOKENS, DISPATCH_USER_TEXT_MAX_TOKENS
        from config.prompts.prompts_nekopower_brain import TASK_BRAIN_SIDE_QUESTION_PROMPT, build_side_question_request
        from utils.tokenize import count_tokens

        if count_tokens(dispatch_text) > DISPATCH_TEXT_MAX_TOKENS or count_tokens(user_text) > DISPATCH_USER_TEXT_MAX_TOKENS:
            # Too long to be shown whole, and what is cut off (the end, where
            # what was said since stands) may well be work: not told, and
            # it waits for the session, with no request made.
            return None
        # On a decision model, or rehosted on a chat model that returns token
        # probabilities: what kind of request it is, side question = P(look).
        from config.prompts.prompts_nekopower_brain import (
            SIDE_QUESTION_DECISION_INSTRUCTIONS,
            SIDE_QUESTION_DECISION_OPTIONS,
            SIDE_QUESTION_DECISION_PENDING,
        )
        from utils.decision_client import ChoiceQuestion
        from utils.decision_client.routing import SITE_SIDE_QUESTION

        options = dict(SIDE_QUESTION_DECISION_OPTIONS)
        if waiting_for.strip():
            options["pending"] = SIDE_QUESTION_DECISION_PENDING
        answers = await self._decided(
            TIER_SIDE_QUESTION,
            SITE_SIDE_QUESTION,
            build_side_question_request(task=dispatch_text, user_said=user_text, waiting_for=waiting_for),
            {"kind": ChoiceQuestion(instructions=SIDE_QUESTION_DECISION_INSTRUCTIONS, criteria=options)},
            rehost=True,
        )
        kinds = _option_probabilities((answers or {}).get("kind"))
        if kinds:
            return kinds.get("look", 0.0) >= SIDE_QUESTION_LOOK_THRESHOLD
        messages = [
            {"role": "system", "content": TASK_BRAIN_SIDE_QUESTION_PROMPT},
            {
                "role": "user",
                "content": build_side_question_request(
                    task=dispatch_text,
                    user_said=user_text,
                    # (Cut already: ``TASK_AGENT_FORK_PENDING_QUESTION_MAX_TOKENS``.)
                    waiting_for=waiting_for,
                ),
            },
        ]
        try:
            raw = await self._llm.complete(TIER_SIDE_QUESTION, messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not told: it waits, as work does
            logger.warning("telling whether a commission is a side question failed: %s", type(exc).__name__)
            return None
        decision = parse_json_object(raw or "")
        verdict = decision.get("side_question") if decision is not None else None
        return verdict if isinstance(verdict, bool) else None

    async def goes_on_with(self, dispatch_text: str, user_text: str, busy: str, at_hand: Sequence[str]) -> bool | None:
        """Say whether a commission goes on with what a busy member is at. None when it cannot be told.

        One request on the small tier (``TIER_BESIDE``), asked only where
        the user would be asked whether another member is to do it, or one
        would be opened for it with nobody asked (``TaskAgentManager._beside``),
        once its words tell it apart from what the member is at. ``busy`` is
        the member's name, ``at_hand`` what it has under way. What is too long
        to be shown whole is not asked about, and neither is anything told
        by an answer that cannot be read.
        """
        from config.nekopower_settings import (
            BESIDE_JUDGE_AT_HAND_MAX_TOKENS,
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
        )
        from config.prompts.prompts_nekopower_brain import TASK_BRAIN_BESIDE_PROMPT, build_beside_request
        from utils.tokenize import count_tokens

        if (
            count_tokens(dispatch_text) > DISPATCH_TEXT_MAX_TOKENS
            or count_tokens(user_text) > DISPATCH_USER_TEXT_MAX_TOKENS
            or count_tokens("\n".join(at_hand)) > BESIDE_JUDGE_AT_HAND_MAX_TOKENS
        ):
            return None
        messages = [
            {"role": "system", "content": TASK_BRAIN_BESIDE_PROMPT},
            {
                "role": "user",
                "content": build_beside_request(task=dispatch_text, user_said=user_text, busy=busy, at_hand=at_hand),
            },
        ]
        try:
            raw = await self._llm.complete(TIER_BESIDE, messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not told
            logger.warning("telling whether a commission goes on with a busy member's work failed: %s", type(exc).__name__)
            return None
        decision = parse_json_object(raw or "")
        verdict = decision.get("goes_on") if decision is not None else None
        return verdict if isinstance(verdict, bool) else None

    async def _belongs_decided(
        self, messages: list[dict[str, Any]], candidates: int | None = None,
    ) -> tuple[str | None, bool]:
        """Where a commission belongs, as one single choice on a native decision model, and whether it was sent.

        The answer is None to ask the chat prompt.

        The options are none, and each candidate either followed up or done
        again as the next round of a recurring job. Vendors without a native
        decision model keep the chat prompt (D27).
        """
        from config.prompts.prompts_nekopower_brain import (
            ORCHESTRATOR_DECISION_FOLLOW_UP,
            ORCHESTRATOR_DECISION_NONE,
            ORCHESTRATOR_DECISION_RECURRENCE,
            ORCHESTRATOR_DECISION_TAIL,
            ORCHESTRATOR_PROMPT,
        )
        from utils.decision_client import ChoiceQuestion
        from utils.decision_client.routing import SITE_BELONGS

        if len(messages) < 2:
            return None, False
        request = str(messages[1].get("content") or "")
        if candidates is None:
            candidates = _candidate_count(request)
        if candidates < 1:
            return None, False
        criteria = {"none": ORCHESTRATOR_DECISION_NONE}
        for k in range(1, candidates + 1):
            criteria[f"c{k}_follow_up"] = ORCHESTRATOR_DECISION_FOLLOW_UP.format(k=k)
            criteria[f"c{k}_recurrence"] = ORCHESTRATOR_DECISION_RECURRENCE.format(k=k)
        # The orchestrator's guidance without its last line, the JSON reply format.
        head, _sep, _tail = ORCHESTRATOR_PROMPT.rpartition("\nReply with JSON")
        instructions = (head or ORCHESTRATOR_PROMPT) + ORCHESTRATOR_DECISION_TAIL
        answers = await self._decided(
            TIER_ORCHESTRATE,
            SITE_BELONGS,
            request,
            {"goes": ChoiceQuestion(instructions=instructions, criteria=criteria)},
            rehost=False,
        )
        return belongs_answer_from((answers or {}).get("goes"), candidates), answers is not None

    async def belongs_counted(
        self, messages: list[dict[str, Any]], candidates: int | None = None,
    ) -> tuple[str | None, int]:
        """``belongs``, with the requests it made: two when a decision question was sent and missed."""
        decided, sent = await self._belongs_decided(messages, candidates)
        if decided is not None:
            return decided, 1
        return await self._belongs_chat(messages), 2 if sent else 1

    async def belongs(self, messages: list[dict[str, Any]], candidates: int | None = None) -> str | None:
        """Ask which of a few projects and task agents a new commission belongs with; returns the answer as it came, or None.

        One request (``TIER_ORCHESTRATE``), with the messages
        ``orchestrator.question`` put together; ``candidates`` is how many
        it shows (counted off the request when not given). Asked only where the words
        of the commission neither name nor clearly match or miss what is
        there (``families.Families.decide``). None when the request
        failed: the commission is then a new one of its own.

        On a native decision model it is one single choice instead (D19),
        whose reading is answered in the same JSON the chat prompt asks for.
        """
        answer, _calls = await self.belongs_counted(messages, candidates)
        return answer

    async def _belongs_chat(self, messages: list[dict[str, Any]]) -> str | None:
        try:
            return await self._llm.complete(TIER_ORCHESTRATE, messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not told: a new loose commission
            logger.warning("asking where a commission belongs failed: %s", type(exc).__name__)
            return None

    async def read_under_way(self, request: DecisionRequest) -> tuple[Triage | None, int, bool]:
        """Say what a sentence does with the work there is: returns it (None for new work), the requests made, and whether it could not be told.

        One request (``TIER_UNDER_WAY``), made only while there is work it
        may be about (``_shown``): her work under way, what waits for the
        user within it, her work that ended lately. Each item says where it
        stands, its project, the familiar on it (by the name the user calls
        it), what its members are at, and what it asks. The model answers
        with a few labels and numbers: "answer" (to a waiting item; what a
        request for approval chose is read by a request of its own,
        ``_read_choice``), "stop" (the items it means, and
        whether it says which: one that does not, with several under way,
        stops nothing and the user is asked which),
        "add" (to one item: told to it if under way, gone on with in its
        session if ended), "unsure" (one of these, of several items) or
        "new". A stop says as well how much it means ("scope"): the items it
        names, all of a project's, or all the work under way. The decision
        model could give the same. The words of the sentence are handed on
        as they are. What task agents wrote (titles, commands, paths,
        questions, what work ended with) is shown quoted, and the prompt says
        it is what the sentence is sorted by, not instructions.

        What does not come in time (``UNDER_WAY_READ_SECONDS``), cannot be
        read, or names nothing it could be is taken for new work, with the
        question still waiting, and said to be so (the third value): her
        receipt tells her. A stop, or a sentence that is about one of several
        items, that names none of them while several are under way asks
        which instead. So does a stop of all that names less than all of
        her work under way, and, where the list was cut, a stop whose words
        share none with what it names (``_words_name``).
        """
        from config.nekopower_settings import UNDER_WAY_READ_SECONDS
        from config.prompts.prompts_nekopower_brain import TASK_BRAIN_UNDER_WAY_PROMPT, build_under_way_request

        sections, numbered = _shown(request)
        if not numbered:
            return None, 0, False
        messages = [
            {"role": "system", "content": TASK_BRAIN_UNDER_WAY_PROMPT},
            {
                "role": "user",
                "content": build_under_way_request(
                    sections=sections, task=request.dispatch_text, user_said=request.user_text
                ),
            },
        ]
        try:
            raw = await asyncio.wait_for(self._llm.complete(TIER_UNDER_WAY, messages), UNDER_WAY_READ_SECONDS)
        except Exception as exc:  # noqa: BLE001 - not told: it goes on as new work, and she is told so
            logger.warning("reading what a request does with the work under way failed: %s", type(exc).__name__)
            return None, 1, True
        reading = parse_json_object(raw or "") or {}
        does = str(reading.get("does") or "").strip().lower()
        picked = [numbered[number - 1] for number in _numbers(reading.get("items"), len(numbered))]
        if does == "new":
            return None, 1, False
        # What waits for the user, all of it: the list may have left some out,
        # and those count as much when the user is to be asked which.
        all_waiting = {
            item.id: item for item in (*request.active, *request.answerable)
            if item.status == "waiting_user" and item.question()
        }
        waiting = list(all_waiting)
        approvals = [item_id for item_id, item in all_waiting.items() if (item.question() or {}).get("kind") == "permission"]

        def which(targets: list[str], *, words_cannot: bool = False) -> tuple[Triage | None, int, bool]:
            # Which, the user is asked. Among several requests for approval, or
            # questions that offer the same answers, words would say no more
            # when asked again: she is told the buttons on each question do.
            by_buttons = words_cannot or (len(targets) > 1 and all(target in approvals for target in targets))
            return Triage(kind="ambiguous", targets=targets, by_buttons=by_buttons, model_calls=1), 1, False

        # One commission on two rows (it ended while the lists were read) is
        # one: what goes on with it takes the row of it that ended, anything
        # else the first.
        by_id: dict[str, tuple[str, Commission]] = {}
        for place, item in picked:
            if item.id not in by_id or (does == "add" and place == _ENDED):
                by_id[item.id] = (place, item)
        picked = list(by_id.values())
        # Her work under way, as it was shown; and all of it, which the list
        # may have cut (what the user is asked to choose among when nothing
        # names which: one left out of the list may be the one meant).
        under_way = [item.id for place, item in numbered if place == _UNDER_WAY]
        everything = [item.id for item in request.active]
        if does == "unsure" and len(picked) == 1:
            # Unsure, of one (on one row or on the two of one that ended): that
            # one, the user is asked of.
            return which([picked[0][1].id])
        if (does == "unsure" or does in ("answer", "add")) and len(picked) > 1:
            # It means one of these and cannot say which (an answer or an
            # addition is to one): which, the user is asked.
            return which([item.id for _place, item in picked])
        if does in ("unsure", "stop") and reading.get("items") in (None, []):
            # Some of the work is meant, and none is named ("stop it" read with
            # no items). (Numbers it gave that are none of the items are no
            # reading.) A stop is of her work under way, all of it, the list
            # may have cut; one unsure of which may answer a question as well,
            # so what waits within her work counts too.
            if does == "stop":
                if not everything:
                    return Triage(kind="nothing_active", model_calls=1), 1, False
                if len(everything) == 1:
                    # One thing under way: stopped, as a stop that does not say
                    # which is ("cancel the task").
                    return Triage(kind="stop", targets=everything, model_calls=1), 1, False
                return Triage(kind="ambiguous", targets=everything, model_calls=1), 1, False
            candidates = [*everything, *(item_id for item_id in waiting if item_id not in everything)]
            if candidates:
                # Which, the user is asked (of the one there is, as of one it named).
                return which(candidates)
        # Whether the sentence itself says which item it means (names its
        # work, project, familiar, command, file or an option it picks).
        said_which = reading.get("named") is True

        def answered(target: Commission, choice: str | None, calls: int = 1) -> tuple[Triage | None, int, bool]:
            # (A choice is read for a request for approval alone.) Read from
            # what was typed or said, an answer allows the one
            # request at most: "always" (written to the settings) and
            # "session" come from the buttons alone, so that a reading gone
            # wrong, or steered, never opens more than that one request. The
            # receipt says the buttons do more.
            narrowed = choice in ("always", "session")
            # Narrowed all the same where the question does not offer it
            # (always asked: once or deny), with nothing said of buttons it has not.
            offered_here = narrowed and choice in ((target.question() or {}).get("options") or [])
            return (
                Triage(
                    kind="answer", targets=[target.id], choice="once" if narrowed else choice,
                    narrowed=offered_here, model_calls=calls,
                ),
                calls,
                False,
            )

        # (One: an answer or an addition that names several was asked about above.)
        if does == "answer" and picked:
            _place, target = picked[0]
            if target.status == "waiting_user" and target.question():
                # When another question waiting offers an answer the target
                # offers too ("Cats" to two that offer Cats / Dogs, or "the dog
                # one"), the answer names neither, whatever the model makes of
                # it, unless what was said holds one only the target offers
                # ("Dogs for the post" when the other offers Cats / Birds).
                offered = _offered(target.question() or {})
                elsewhere: set[str] = set()
                for other_id, other in all_waiting.items():
                    if other_id != target.id:
                        elsewhere |= _offered(other.question() or {})
                spoken = f"{request.user_text} {request.dispatch_text}".casefold()
                mentioned = {label for label in offered if _said(label, spoken)}
                # (Requests for approval all offer the same answers: which of
                # them, the rule for several of those below asks.)
                shared = (
                    (target.question() or {}).get("kind") != "permission"
                    and bool(offered & elsewhere)
                    and not (mentioned - elsewhere)
                )
                if (not said_which or shared) and len(waiting) > 1:
                    # A plain "yes" with several questions waiting: which, the
                    # user is asked, and none of them is answered on a guess.
                    # (Where the answers are shared, naming the question again
                    # would be asked about again: the buttons say which.)
                    return which(waiting, words_cannot=shared)
                if (target.question() or {}).get("kind") == "permission":
                    if len(approvals) > 1:
                        # Several requests for approval wait: which this allows
                        # or refuses, the user is asked, however plainly the
                        # model says it is named. (Their commands are written by
                        # task agents and are what it read that by; none is
                        # decided by a reading words a task agent wrote could
                        # steer.) Asked again, the words would say no more:
                        # she is told the buttons on each question do.
                        return which(approvals)
                    # What it chose is read apart, from the user's words alone.
                    return answered(target, await self._read_choice(request, target), calls=2)
                return answered(target, None)
        if does == "stop" and picked:
            stopped = [item.id for place, item in picked if place == _UNDER_WAY]
            hidden = [item for item in request.active if item.id not in under_way]
            # How much it means: "these" (the items it names), "project" (all
            # of a project's, a familiar's or a plugin's work, however much of
            # it was shown) or "all" (all the work under way). "These" is
            # taken at its word where the list left some out: what it names is
            # stopped, and what of their project's work was not shown goes on
            # and is said to (the model has read "stop all the shop site work"
            # as "these", measured: that is not left running unsaid). A stop
            # that does not say how much may be either of the first two, and
            # is asked about as before. Which items are stopped is still what
            # it names, and whether it says which: a bare "stop it" is read as
            # all of it as often as "cancel all of it" is (measured), so "all"
            # is no naming of its own.
            scope = str(reading.get("scope") or "").strip().lower()
            if not said_which and len(everything) > 1:
                # "Stop it" with several under way: which, the user is asked,
                # not everything stopped on a guess. ("Wait", "not now" said to
                # what waits for the user is its answer, a refusal, as the
                # prompt has it; a stop is a stop.) Among all of her work, the
                # list may have cut, and counted so: one shown of several is
                # not taken for the one meant.
                return Triage(kind="ambiguous", targets=everything, model_calls=1), 1, False
            shown_stopped = [item for place, item in picked if place == _UNDER_WAY]
            if hidden and shown_stopped and not _words_name(request, shown_stopped):
                # Named, the model says, while the list was cut, and the words
                # say nothing of what it picked (of one of them, if several):
                # no word of the sentence, beyond the stopping, the "all" and
                # the work under way, is in what it is, its project, familiar
                # or plugin. The
                # model has read "cancel what is under way" as the items it
                # saw at work, and those alone (measured: it leaves out what
                # waits for the user), with the rest not shown: one was stopped
                # ("these") or asked about with its project's ("project"), and
                # the rest went on. Which, the user is asked, among all.
                return Triage(kind="ambiguous", targets=everything, model_calls=1), 1, False
            if (scope == "all" and set(stopped) != set(everything)) or (
                hidden and len(stopped) > 1 and scope not in ("project", "these")
            ):
                # All of it, said of less than all her work under way (some of
                # it left out of the list, or what waits for the user left out
                # of a whole one: "cancel all the ongoing work" read as the
                # items at work, measured), or a stop of several that does not
                # say how much while some of her work was left out of the list:
                # it may mean the rest as well. Which, the user is asked, among
                # all (for "all", though one item was named).
                return Triage(kind="ambiguous", targets=everything, model_calls=1), 1, False
            kin = [item.id for item in hidden if any(_kin(item, other) for other in shown_stopped)]
            if kin and scope != "these":
                # A project's or a familiar's work is stopped while more of it
                # was left out ("stop the shop site work", one of it shown):
                # which, the user is asked, among those.
                return Triage(kind="ambiguous", targets=[*stopped, *kin], model_calls=1), 1, False
            if stopped:
                # For "these", where some of their project's work was left out,
                # all of it that goes on is said, shown or not ("the footer
                # one" of the shop site's, or "all the shop site work" read as
                # the one item shown: what was shown and not named goes on as
                # well, owner's automatic review). Where none of it was left
                # out, what it names is what was meant, as when the list is
                # whole: nothing is said. So for each it names: a stop of the
                # shop site's one and the blog's one says nothing of the blog,
                # all of which was shown.
                cut = [other for other in shown_stopped if any(_kin(item, other) for item in hidden)]
                going_on = [
                    item.id for item in request.active
                    if item.id not in stopped and any(_kin(item, other) for other in cut)
                ]
                return Triage(kind="stop", targets=stopped, left_running=going_on, model_calls=1), 1, False
            # It means work that is over, or questions within others' work:
            # nothing of hers it names is under way.
            if not under_way:
                return Triage(kind="nothing_active", model_calls=1), 1, False
            return Triage(kind="ambiguous", targets=everything, model_calls=1), 1, False
        if does == "add" and picked:
            place, target = picked[0]
            if place == _ENDED:
                return Triage(kind="continue", targets=[target.id], model_calls=1), 1, False
            if place == _UNDER_WAY:
                if find_duplicate(request.dispatch_text, request.user_text, [target]) is target:
                    return Triage(kind="duplicate", targets=[target.id], model_calls=1), 1, False
                if target.route == "task_agent":
                    return Triage(kind="tell", targets=[target.id], model_calls=1), 1, False
            # A plugin run takes no addition, and what a task agent of hers
            # started is that one's business: work of its own.
            return None, 1, False
        # (What the model wrote is not logged: it may repeat what was said. A
        # label it gave is one of those known, and is.)
        if not does:
            why = "nothing said"
        elif does in ("answer", "stop", "add", "unsure"):
            why = f"{does} names nothing it can be"
        else:
            why = "unknown reading"
        logger.warning("what a request does with the work under way could not be read: %s", why)
        return None, 1, True

    async def _read_choice(self, request: DecisionRequest, target: Commission) -> str | None:
        """Read what an answer to a request for approval chose (``ANSWER_CHOICES``); None for an answer not understood.

        One request (``TIER_ANSWER_CHOICE``), made once ``read_under_way``
        has read the sentence as its answer: asked within that longer
        request, the small model read a plain yes as "always" more often.
        It is shown what kind of thing is asked in the host's own words
        (``reports.permission_kind``), the answers on offer and what the
        user said (her words only where the user's are not there), and
        nothing a task agent wrote: not the command, the path or
        the title of what asks, which could otherwise say what to choose.
        The choice comes from the user's words alone. One that says it is no answer after
        all, names no choice, fails or comes too late is an answer not
        understood: the user is asked again.
        """
        from config.nekopower_settings import UNDER_WAY_READ_SECONDS
        from config.prompts.prompts_nekopower_brain import (
            PERMISSION_OPTION_NAMES,
            TASK_BRAIN_ANSWER_PROMPT,
            build_choice_request,
        )

        from nekopower.host.reports import permission_kind

        question = target.question() or {}
        names = PERMISSION_OPTION_NAMES["en"]
        offered = [names[option] for option in question.get("options") or [] if option in names]
        # Hers is the REQUEST (the part of the turn that answers: "ok" of "ok,
        # and check the weather"), with the user's own words beside it when
        # they differ, which the prompt reads what was meant by.
        asked = build_choice_request(
            asks=permission_kind(question), offered=offered, task=request.dispatch_text, user_said=request.user_text
        )
        messages = [{"role": "system", "content": TASK_BRAIN_ANSWER_PROMPT}, {"role": "user", "content": asked}]
        try:
            raw = await asyncio.wait_for(self._llm.complete(TIER_ANSWER_CHOICE, messages), UNDER_WAY_READ_SECONDS)
        except Exception as exc:  # noqa: BLE001 - not understood: the user is asked again
            logger.warning("reading what an answer chose failed: %s", type(exc).__name__)
            return None
        reading = parse_json_object(raw or "") or {}
        choice = str(reading.get("choice") or "").strip().lower()
        said = reading.get("answers")
        # The one question, as a number or its digit ("true" and 1.0 are no answer it gave).
        answers_it = said == "1" or (type(said) is int and said == 1)
        return choice if answers_it and choice in ANSWER_CHOICES else None

    # -- first round ------------------------------------------------------

    async def triage(self, request: DecisionRequest) -> Triage:
        reading, calls, unread = await self.read_under_way(request)
        if reading is not None:
            if reading.kind == "continue":
                # It becomes a commission of its own: the plugins its words
                # point at go along, as they do for any new one.
                match_text = request.match_text
                reading.keyword_hit_ids = keyword_hits(match_text, request.plugins)
                kept, _ = stage1_filter(match_text, request.plugins)
                reading.candidate_ids = [str(plugin.get("id")) for plugin in kept]
            return reading
        best = find_duplicate(request.dispatch_text, request.user_text, request.active)
        if best is not None:
            return Triage(kind="duplicate", targets=[best.id], model_calls=calls, unread=unread)
        probed = await self.probe(request)
        probed.model_calls += calls
        probed.unread = unread
        return probed

    async def probe(self, request: DecisionRequest, *, ask_model: bool = True) -> Triage:
        plugins = request.plugins
        match_text = request.match_text
        hits = keyword_hits(match_text, plugins)
        kept, _ = stage1_filter(match_text, plugins)
        candidates = [str(plugin.get("id")) for plugin in kept]
        if candidates or request.task_agent_available:
            return Triage(kind="new", keyword_hit_ids=hits, candidate_ids=candidates)
        if not plugins:
            return Triage(kind="none", reason="no_match")
        if not ask_model:
            # Asked by a query, which stays local: whether one of the plugins
            # fits after all is left open rather than answered with a no.
            return Triage(kind="unsure")
        # Nothing matched by its words and there is no task agent to fall back
        # on. Before saying it cannot be done, let the small model look once.
        coarse, coarse_calls = await self._coarse_screen(match_text, plugins, request.lang)
        known = {str(plugin.get("id")) for plugin in plugins}
        picked = [plugin_id for plugin_id in coarse if plugin_id in known]
        if picked:
            return Triage(kind="new", candidate_ids=picked, coarse_ids=coarse, model_calls=coarse_calls)
        return Triage(kind="none", reason="no_match", coarse_ids=coarse, model_calls=coarse_calls)

    async def _coarse_screen_decided(
        self, user_text: str, shorts: list[tuple[str, str]],
    ) -> tuple[list[str] | None, bool]:
        """The coarse screen as one single choice on a native decision model (D16), and whether it was sent.

        The ids are None to ask the chat prompt.

        Each plugin whose choice probability reaches
        ``COARSE_SCREEN_DECISION_THRESHOLD`` is kept, in catalog order.
        Vendors without one keep the chat prompt: a single choice read by
        its probabilities did not hold up for several plugins on chat models.
        """
        from config import DECISION_STATE_MAX_TOKENS
        from config.prompts.prompts_nekopower_brain import (
            COARSE_SCREEN_DECISION_INSTRUCTIONS,
            COARSE_SCREEN_DECISION_NONE,
            COARSE_SCREEN_DECISION_STATE,
        )
        from utils.decision_client import ChoiceQuestion
        from utils.decision_client.routing import SITE_COARSE_SCREEN
        from utils.tokenize import truncate_to_tokens

        if not shorts:
            return None, False
        criteria = {f"p{i}": f"[{pid}] {truncate_to_tokens(short, 24)}" for i, (pid, short) in enumerate(shorts)}
        criteria["none"] = COARSE_SCREEN_DECISION_NONE
        state = COARSE_SCREEN_DECISION_STATE.format(user_text=truncate_to_tokens(user_text, DECISION_STATE_MAX_TOKENS // 4))
        answers = await self._decided(
            TIER_COARSE,
            SITE_COARSE_SCREEN,
            state,
            {"pick": ChoiceQuestion(instructions=COARSE_SCREEN_DECISION_INSTRUCTIONS, criteria=criteria)},
            rehost=False,
        )
        sent = answers is not None
        probs = _option_probabilities((answers or {}).get("pick"))
        if not probs:
            return None, sent
        return [
            pid for i, (pid, _short) in enumerate(shorts)
            if probs.get(f"p{i}", 0.0) > 0 and probs.get(f"p{i}", 0.0) >= COARSE_SCREEN_DECISION_THRESHOLD
        ], sent

    async def _coarse_screen(self, user_text: str, plugins: Sequence[dict[str, Any]], lang: str) -> tuple[list[str], int]:
        """Ask the small model which plugins might be relevant: the ids, and the requests made for them.

        Two requests when a decision question was sent and missed, then the chat prompt.
        """
        from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
        from config.prompts.prompts_nekopower_brain import TASK_BRAIN_COARSE_SCREEN_PROMPT
        from config.prompts.prompts_sys import _loc
        from utils.tokenize import count_tokens, truncate_to_tokens

        summaries = []
        shorts: list[tuple[str, str]] = []
        for plugin in plugins:
            plugin_id = plugin.get("id", "unknown")
            short = plugin.get("short_description") or plugin.get("description", "")
            if count_tokens(short) > AGENT_PLUGIN_SHORTDESC_MAX_TOKENS:
                short = truncate_to_tokens(short, AGENT_PLUGIN_SHORTDESC_MAX_TOKENS - count_tokens("...")) + "..."
            summaries.append(f"- {plugin_id}: {short}")
            shorts.append((str(plugin_id), short))
        decided, sent = await self._coarse_screen_decided(user_text, shorts)
        if decided is not None:
            return decided, 1
        calls = 2 if sent else 1
        system = _loc(TASK_BRAIN_COARSE_SCREEN_PROMPT, lang).format(
            plugin_summaries="\n".join(summaries),
            user_text=user_text,
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
        try:
            from utils.file_utils import robust_json_loads

            raw = await self._llm.complete(TIER_COARSE, messages)
            ids = robust_json_loads(_strip_fences(raw))
            if isinstance(ids, list):
                return [str(item) for item in ids if isinstance(item, (str, int))], calls
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keyword and BM25 matches still stand
            logger.warning("coarse screen failed: %s", type(exc).__name__)
        return [], calls

    # -- turn check -------------------------------------------------------

    async def judge_turn(self, user_text: str, reply_text: str, lang: str) -> TurnVerdict:
        """Ask the small model once; anything unreadable counts as "none"."""
        from config.nekopower_settings import TURN_CHECK_TASK_MAX_TOKENS
        from config.prompts.prompts_nekopower_brain import (
            TURN_CHECK_INPUT_TEMPLATE,
            TURN_CHECK_PROMPT,
            TURN_CHECK_VERDICTS,
        )
        from config.prompts.prompts_sys import _loc
        from utils.tokenize import truncate_to_tokens

        messages = [
            {"role": "system", "content": _loc(TURN_CHECK_PROMPT, lang)},
            {"role": "user", "content": TURN_CHECK_INPUT_TEMPLATE.format(user_text=user_text, reply_text=reply_text)},
        ]
        try:
            raw = await self._llm.complete(TIER_TURN_CHECK, messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an unanswered check changes nothing
            logger.warning("turn check failed: %s", type(exc).__name__)
            return TurnVerdict(kind="none", model_calls=1)
        answer = parse_json_object(raw) or {}
        kind = str(answer.get("verdict") or "").strip().lower()
        if kind not in TURN_CHECK_VERDICTS:
            kind = "none"
        task = answer.get("task")
        task = " ".join(task.split()) if isinstance(task, str) else ""
        calls = 1
        if kind != "none":
            # A second look on a decision model (or rehosted on a chat model
            # with token probabilities) before anything is acted on: its
            # verdict stands, with the task the chat wrote (owner decision D28).
            asked, checked = await self._turn_second_look(messages[1]["content"])
            if asked:
                calls = 2  # counted whether or not its answer could be read
            if checked is not None:
                kind = checked
        return TurnVerdict(
            kind=kind,
            task="" if kind == "none" else truncate_to_tokens(task, TURN_CHECK_TASK_MAX_TOKENS),
            model_calls=calls,
        )

    async def _turn_second_look(self, state: str) -> tuple[bool, str | None]:
        """Whether the turn check's decision question was sent, and its verdict (None when not sent or not read)."""
        from config.prompts.prompts_nekopower_brain import TURN_CHECK_DECISION_INSTRUCTIONS, TURN_CHECK_DECISION_OPTIONS
        from utils.decision_client import ChoiceQuestion
        from utils.decision_client.routing import SITE_TURN_CHECK

        answers = await self._decided(
            TIER_TURN_CHECK,
            SITE_TURN_CHECK,
            state,
            {"verdict": ChoiceQuestion(instructions=TURN_CHECK_DECISION_INSTRUCTIONS, criteria=dict(TURN_CHECK_DECISION_OPTIONS))},
            rehost=True,
        )
        return answers is not None, turn_verdict_from((answers or {}).get("verdict"))

    # -- second round -----------------------------------------------------

    def _active_rows(self, request: DecisionRequest) -> tuple[list[str], list[Commission]]:
        """Return the commissions shown to the fine selection, as lines and as rows."""
        from config.nekopower_settings import DECISION_LEDGER_ROW_MAX_TOKENS, DECISION_LEDGER_ROWS
        from utils.tokenize import truncate_to_tokens

        # Only rows a "duplicate" answer can point at.
        ranked = sorted(
            (commission for commission in request.active if can_be_repeated(commission)),
            key=lambda commission: similarity(request.match_text, commission_text(commission)),
            reverse=True,
        )[:DECISION_LEDGER_ROWS]
        lines = []
        for commission in ranked:
            target = (
                f"{commission.plugin_id}.{commission.entry_id}"
                if commission.route == "plugin" and commission.plugin_id
                else (commission.route or "undecided")
            )
            line = f"[{commission.status}] {commission.dispatch_text or commission.title} ({target})"
            lines.append(truncate_to_tokens(line, DECISION_LEDGER_ROW_MAX_TOKENS))
        return lines, ranked

    async def select(self, request: DecisionRequest, triage: Triage) -> Selection:
        from config import AGENT_PLUGIN_DESC_BM25_THRESHOLD
        from config.nekopower_settings import (
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
            PREFERENCE_PROMPT_MAX_TOKENS,
        )
        from config.prompts.prompts_nekopower_brain import (
            NO_PLUGINS_DESC,
            TASK_BRAIN_SELECT_PROMPT,
            build_selection_request,
        )
        from config.prompts.prompts_sys import _loc
        from utils.tokenize import count_tokens, truncate_to_tokens

        fallback_route = "task_agent" if request.task_agent_available else "none"
        all_plugins = request.plugins
        if not all_plugins:
            return Selection(route=fallback_route, reason="No active plugins")

        match_text = request.match_text
        calls = 0
        lines = build_desc_lines(all_plugins)
        plugins_desc = "\n".join(lines) if lines else NO_PLUGINS_DESC
        hits = keyword_hits(match_text, all_plugins)
        shown = list(all_plugins)

        if count_tokens(plugins_desc) > AGENT_PLUGIN_DESC_BM25_THRESHOLD:
            try:
                if triage.coarse_ids is not None:
                    # The first round already paid for the coarse screen.
                    coarse_ids = list(triage.coarse_ids)
                    kept, _ = await asyncio.to_thread(stage1_filter, match_text, all_plugins)
                else:
                    (kept, _), (coarse_ids, coarse_calls) = await asyncio.gather(
                        asyncio.to_thread(stage1_filter, match_text, all_plugins),
                        self._coarse_screen(match_text, all_plugins, request.lang),
                    )
                    calls += coarse_calls
                selected = {plugin.get("id") for plugin in kept} | set(coarse_ids) | set(hits)
                shown = [plugin for plugin in all_plugins if plugin.get("id") in selected]
                lines = build_desc_lines(shown)
                plugins_desc = "\n".join(lines) if lines else NO_PLUGINS_DESC
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - as before: fall back to the full list
                logger.warning("first stage failed, showing every plugin: %s", type(exc).__name__)
                shown = list(all_plugins)

        shown_ids = [str(plugin.get("id")) for plugin in shown if plugin.get("id")]
        if not shown:
            # Nothing passed the first stage, so there is no entry to choose.
            return Selection(route=fallback_route, reason="No plugin candidates", model_calls=calls)

        plugins_desc = annotate_keyword_hits(plugins_desc, hits)
        system = _loc(TASK_BRAIN_SELECT_PROMPT, request.lang).format(plugins_desc=plugins_desc)
        active_lines, active_rows = self._active_rows(request)
        user = build_selection_request(
            task=truncate_to_tokens(request.dispatch_text, DISPATCH_TEXT_MAX_TOKENS),
            user_said=truncate_to_tokens(request.user_text, DISPATCH_USER_TEXT_MAX_TOKENS),
            active_tasks=active_lines,
            preferences=truncate_to_tokens(request.preferences, PREFERENCE_PROMPT_MAX_TOKENS),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        raw, error = await self._fine(messages)
        calls += 1
        if raw is None:
            return Selection(
                route=fallback_route, reason=f"Assessment error: {error}", shown_plugin_ids=shown_ids, model_calls=calls
            )
        _count("plugin_assess_stage2")
        decision = parse_json_object(raw)
        if decision is None:
            reason = "Empty LLM response" if not raw.strip() else "JSON parse error"
            return Selection(route=fallback_route, reason=reason, shown_plugin_ids=shown_ids, model_calls=calls)

        valid_entries = {
            str(plugin.get("id")): [str(entry.get("id")) for entry in visible_entries(plugin) if entry.get("id")]
            for plugin in shown
            if plugin.get("id")
        }
        if _wants_plugin(decision):
            _count("plugin_assess_stage2_actionable")
        # An answer naming a plugin or an entry that does not exist is not
        # asked about again: the task goes to a task agent, or cannot be done
        # when there is none (``_conclude``). The analyzer's second request
        # could not catch an entry that exists and is the wrong one, which is
        # the mistake that does harm.
        return self._conclude(decision, valid_entries, active_rows, fallback_route, shown_ids, calls)

    async def _fine(self, messages: list[dict[str, Any]]) -> tuple[str | None, str]:
        """Run the fine selection, retrying transient provider errors like the analyzer."""
        from utils.llm_client import openai_retry_error_types

        retryable = openai_retry_error_types()
        for attempt in range(_FINE_ATTEMPTS):
            try:
                return await self._llm.complete(TIER_FINE, messages), ""
            except asyncio.CancelledError:
                raise
            except retryable as exc:
                if attempt < _FINE_ATTEMPTS - 1:
                    await asyncio.sleep(_FINE_RETRY_DELAYS[attempt])
                    continue
                return None, type(exc).__name__
            except Exception as exc:  # noqa: BLE001 - reported as the reason
                return None, f"{type(exc).__name__}: {exc}"
        return None, "no answer"

    def _conclude(
        self,
        decision: dict[str, Any],
        valid_entries: dict[str, list[str]],
        active_rows: list[Commission],
        fallback_route: str,
        shown_ids: list[str],
        calls: int,
    ) -> Selection:
        description = str(decision.get("task_description") or "")
        reason = str(decision.get("reason") or "")
        preference = decision.get("preference")
        preference = preference.strip() if isinstance(preference, str) and preference.strip() else None

        route = str(decision.get("route") or "").strip().lower()
        if route == "duplicate":
            number = decision.get("duplicate_of")
            if isinstance(number, str) and number.strip().isdigit():
                number = int(number.strip())
            if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(active_rows):
                return Selection(
                    route="duplicate",
                    duplicate_of=active_rows[number - 1].id,
                    task_description=description,
                    reason=reason,
                    shown_plugin_ids=shown_ids,
                    model_calls=calls,
                )
            # Otherwise it named no line that was shown: not a repeat that can
            # be acted on, and the answer is read as below.

        if route == "none":
            # Neither a plugin entry nor a task agent can do it, or it is not
            # something to get done: nothing is started.
            return Selection(
                route="none",
                task_description=description,
                preference=preference,
                reason=reason,
                shown_plugin_ids=shown_ids,
                model_calls=calls,
            )

        if _wants_plugin(decision):
            plugin_id, entry_id = _identifiers(decision)
            problem = _identifier_problem(decision, valid_entries)
            if problem is None and plugin_id is not None and entry_id is not None:
                args = decision.get("plugin_args")
                return Selection(
                    route="plugin",
                    plugin_id=plugin_id,
                    entry_id=entry_id,
                    plugin_args=args if isinstance(args, dict) else None,
                    task_description=description,
                    preference=preference,
                    reason=reason,
                    shown_plugin_ids=shown_ids,
                    model_calls=calls,
                )
            reason = problem or reason

        return Selection(
            route=fallback_route,
            task_description=description,
            preference=preference,
            reason=reason,
            shown_plugin_ids=shown_ids,
            model_calls=calls,
        )


def _identifiers(decision: dict[str, Any]) -> tuple[str | None, str | None]:
    plugin_id = decision.get("plugin_id")
    if isinstance(plugin_id, int) and not isinstance(plugin_id, bool):
        plugin_id = str(plugin_id)
    entry_id = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")
    if isinstance(entry_id, int) and not isinstance(entry_id, bool):
        entry_id = str(entry_id)
    return (
        plugin_id if isinstance(plugin_id, str) and plugin_id else None,
        entry_id if isinstance(entry_id, str) and entry_id else None,
    )


def _wants_plugin(decision: dict[str, Any]) -> bool:
    """Return whether the answer asks for a direct plugin call.

    ``route == "plugin"`` says so. An answer in the analyzer's older shape
    (``has_task`` and ``can_execute``) is read the same way.
    """
    route = str(decision.get("route") or "").strip().lower()
    if route:
        return route == "plugin"
    return bool(decision.get("has_task")) and bool(decision.get("can_execute"))


def _identifier_problem(decision: dict[str, Any], valid_entries: dict[str, list[str]]) -> str | None:
    """Return what is wrong with the named plugin and entry, or None when they exist.

    The text is kept as the reason of the decision, so the record shows how
    often a model names something that is not there.
    """
    plugin_id, entry_id = _identifiers(decision)
    if plugin_id is None:
        return f"plugin_id is required for route=plugin. Available plugins: {list(valid_entries)}"
    if plugin_id not in valid_entries:
        return (
            f"plugin_id '{plugin_id}' is not available in the current candidate set. "
            f"Available plugins: {list(valid_entries)}"
        )
    entries = valid_entries[plugin_id]
    if not entries:
        return f"plugin '{plugin_id}' has no Agent-visible entries."
    if entry_id is None:
        return f"entry_id is required for plugin '{plugin_id}'. Available entries: {entries}"
    if entry_id not in entries:
        return f"entry_id '{entry_id}' does not exist in plugin '{plugin_id}'. Available entries: {entries}"
    return None
