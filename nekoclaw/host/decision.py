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

* ``triage``, the first round. Local work: is this a stop request, an answer,
  an addition or a repeat of something under way, and are there candidates.
  It makes no model call, with one exception: when nothing matches locally
  and no familiar can be started, it runs the small-model coarse screen once
  before concluding that nothing can do this.
* ``select``, the second round. Chooses a plugin entry and fills in its
  arguments; plugins come first. When no entry fits or the task needs several
  steps, it hands over to a familiar. That is decided by the same request,
  never by a further one.

``LexicalDecisionModel`` is today's implementation: keyword patterns, BM25,
the small-model coarse screen and one fine selection. Its filter is as wide
as the analyzer's (``brain/task_executor.py``): the coarse stage runs only
when all plugin descriptions together exceed ``AGENT_PLUGIN_DESC_BM25_THRESHOLD``
tokens, and it keeps the union of the keyword hits, the ten best BM25 matches
and what the small model picks.

Model calls per path (asserted in ``tests/unit/nekoclaw/test_host_decision.py``):

* ``triage``: 0; 1 (small model) only when there is no local candidate and no
  familiar.
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

from nekoclaw.host.ledger import Commission
from nekoclaw.host.selection import (
    annotate_keyword_hits,
    build_desc_lines,
    keyword_hits,
    similarity,
    stage1_filter,
    token_set,
    visible_entries,
)

logger = logging.getLogger(__name__)

TIER_COARSE = "coarse"
TIER_FINE = "fine"
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
    # familiars started for themselves are not among them.
    active: list[Commission] = field(default_factory=list)
    # Commissions started by her familiars that wait for the user. Their
    # questions reach the user too, so an answer can be about them; a stop,
    # a repeat or an addition never is.
    answerable: list[Commission] = field(default_factory=list)
    familiar_available: bool = False
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
    # "nothing_active", "none" (nothing can do this) or, from a probe that may
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


@dataclass(slots=True)
class Selection:
    """Conclusion of the second round."""

    # "plugin", "familiar", "duplicate" or "none".
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
    # The plugins the fine selection was shown; a familiar starts with these.
    shown_plugin_ids: list[str] = field(default_factory=list)
    model_calls: int = 0


class DecisionLLM(Protocol):
    """The two model tiers the lexical decision model uses."""

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        """Return the text of one completion from ``TIER_COARSE`` or ``TIER_FINE``."""


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


# ---------------------------------------------------------------------------
# The configured model tiers
# ---------------------------------------------------------------------------


class ConfiguredDecisionLLM:
    """``DecisionLLM`` on the models the analyzer used.

    The coarse screen runs on the small latency-sensitive tier (``emotion``),
    the fine selection on the ``summary`` tier. Endpoints come from the user's
    configuration as they are, ``http://`` addresses included.
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[Any, ...], Any] = {}

    async def _client(self, tier: str, budget: int) -> Any:
        from utils.config_manager import get_config_manager
        from utils.llm_client import create_chat_llm_async

        slot = "emotion" if tier == TIER_COARSE else "summary"
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
        from config import AGENT_PLUGIN_COARSE_MAX_TOKENS, AGENT_PLUGIN_FULL_MAX_TOKENS
        from utils.token_tracker import set_call_type

        # Same usage bucket as the analyzer it takes over from.
        set_call_type("agent")
        budget = AGENT_PLUGIN_COARSE_MAX_TOKENS if tier == TIER_COARSE else AGENT_PLUGIN_FULL_MAX_TOKENS
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
            from config.prompts import prompts_nekoclaw_brain as texts

            cls._loaded = {
                "stop": _patterns(texts.STOP_CUE_PATTERNS),
                "vague": _patterns(texts.VAGUE_REFERENCE_PATTERNS),
                "generic": _patterns(texts.GENERIC_WORD_PATTERNS),
                "supplement": _patterns(texts.SUPPLEMENT_CUE_PATTERNS),
                "answer": _patterns(texts.ANSWER_CUE_PATTERNS),
                "deny": _patterns(texts.PERMISSION_DENY_PATTERNS),
                "always": _patterns(texts.PERMISSION_ALWAYS_PATTERNS),
                "once": _patterns(texts.PERMISSION_ONCE_PATTERNS),
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


def parse_permission_choice(text: str) -> str | None:
    """Return "deny", "always" or "once" for an answer to a permission prompt.

    A refusal wins over everything else in the sentence. None when the
    sentence says neither yes nor no.
    """
    if has_cue("deny", text):
        return "deny"
    if has_cue("always", text):
        return "always"
    if has_cue("once", text):
        return "once"
    return None


def is_capability_question(text: str) -> bool:
    """Return whether a query asks if something can be done."""
    return has_cue("capability_question", text)


def commission_text(commission: Commission) -> str:
    return " ".join(part for part in (commission.title, commission.dispatch_text, commission.user_text) if part)


def _coverage(sentence_tokens: frozenset[str], commission: Commission) -> float:
    """Return which share of the sentence's tokens occur in the commission's text."""
    if not sentence_tokens:
        return 0.0
    return len(sentence_tokens & token_set(commission_text(commission))) / len(sentence_tokens)


def _question_text(commission: Commission) -> str:
    question = commission.question() or {}
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(question)
    return " ".join(parts)


def _single_best(scored: list[tuple[float, Commission]]) -> Commission | None:
    """Return the commission with the highest score when it stands alone at the top."""
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] <= 0:
        return None
    if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
        return None
    return ranked[0][1]


def read_stop(text: str, active: list[Commission]) -> Triage | None:
    """Read a sentence with a stop cue. None means: not about stopping a commission."""
    if not has_cue("stop", text):
        return None
    # What is left once the stop words and words like "that one" are set aside
    # says which commission is meant, if the sentence says so at all.
    named = strip_cues("vague", strip_cues("stop", text))
    content = token_set(named)
    if not active:
        # "Never mind" with nothing under way; a sentence with more in it is a task.
        return None if content else Triage(kind="nothing_active")
    # Words such as "tomorrow" do not tell one commission from another. A
    # sentence that names nothing else still names something: it is not about
    # whatever happens to be under way.
    distinctive = token_set(strip_cues("generic", named)) if content else frozenset()
    scored = [(_coverage(distinctive, commission), commission) for commission in active]
    matched = [item for item in scored if item[0] > 0]
    if len(active) == 1:
        if not content or matched:
            return Triage(kind="stop", targets=[active[0].id])
        # It says what to stop, and that is not this commission: a task of its
        # own, such as cancelling an alarm.
        return None
    if matched:
        best = _single_best(matched)
        if best is not None:
            return Triage(kind="stop", targets=[best.id])
        top = max(score for score, _ in matched)
        return Triage(kind="ambiguous", targets=[commission.id for score, commission in matched if score == top])
    if not content:
        return Triage(kind="ambiguous", targets=[commission.id for commission in active])
    return None


def read_answer(text: str, active: list[Commission]) -> Triage | None:
    """Read a sentence as the answer to a commission that waits for the user."""
    waiting = [commission for commission in active if commission.status == "waiting_user"]
    if not waiting:
        return None
    tokens = token_set(strip_cues("answer", text))
    explicit = has_cue("answer", text)
    choice = parse_permission_choice(text)

    def bears_on(commission: Commission) -> float:
        score = 0.0
        if tokens:
            question_tokens = token_set(_question_text(commission))
            score = len(tokens & question_tokens) / len(tokens)
        is_permission = (commission.question() or {}).get("kind") == "permission"
        if is_permission and choice is not None:
            score += 0.5
        return score

    scored = [(bears_on(commission), commission) for commission in waiting]
    if len(waiting) == 1:
        if explicit or scored[0][0] > 0:
            return _answer(waiting[0], choice)
        return None
    best = _single_best(scored)
    if best is not None:
        return _answer(best, choice)
    if explicit or any(score > 0 for score, _ in scored):
        return Triage(kind="ambiguous", targets=[commission.id for commission in waiting])
    return None


def _answer(commission: Commission, choice: str | None) -> Triage:
    is_permission = (commission.question() or {}).get("kind") == "permission"
    return Triage(kind="answer", targets=[commission.id], choice=choice if is_permission else None)


def can_be_repeated(commission: Commission) -> bool:
    """Return whether a new dispatch can be a repeat of this commission.

    Not a commission a familiar started for itself: she never dispatched it.
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
    from config.nekoclaw_settings import DUPLICATE_SIMILARITY, DUPLICATE_SIMILARITY_SAME_USER_TEXT

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


def read_repeat_or_addition(request: DecisionRequest) -> Triage | None:
    """Read a sentence as a repeat of, or an addition to, something under way."""
    from config.nekoclaw_settings import SUPPLEMENT_MIN_OVERLAP

    text = request.dispatch_text
    best = find_duplicate(text, request.user_text, request.active)
    if best is not None:
        return Triage(kind="duplicate", targets=[best.id])

    if not has_cue("supplement", text):
        return None
    # An addition can only be passed on to a familiar; a plugin run takes none.
    familiars = [commission for commission in request.active if commission.route == "familiar"]
    if not familiars:
        return None
    content = token_set(strip_cues("vague", strip_cues("supplement", text)))
    scored = [(_coverage(content, commission), commission) for commission in familiars]
    related = [item for item in scored if item[0] >= SUPPLEMENT_MIN_OVERLAP]
    if len(familiars) == 1 and (related or not content):
        return Triage(kind="tell", targets=[familiars[0].id])
    target = _single_best(related)
    if target is not None:
        return Triage(kind="tell", targets=[target.id])
    if related:
        top = max(score for score, _ in related)
        return Triage(kind="ambiguous", targets=[commission.id for score, commission in related if score == top])
    return None


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


class LexicalDecisionModel:
    """Keyword patterns, BM25, the small-model coarse screen and one fine selection."""

    def __init__(self, llm: DecisionLLM) -> None:
        self._llm = llm

    # -- first round ------------------------------------------------------

    async def triage(self, request: DecisionRequest) -> Triage:
        text = request.dispatch_text
        stop = read_stop(text, request.active)
        if stop is not None:
            return stop
        answer = read_answer(text, [*request.active, *request.answerable])
        if answer is not None:
            return answer
        repeat = read_repeat_or_addition(request)
        if repeat is not None:
            return repeat
        return await self.probe(request)

    async def probe(self, request: DecisionRequest, *, ask_model: bool = True) -> Triage:
        plugins = request.plugins
        match_text = request.match_text
        hits = keyword_hits(match_text, plugins)
        kept, _ = stage1_filter(match_text, plugins)
        candidates = [str(plugin.get("id")) for plugin in kept]
        if candidates or request.familiar_available:
            return Triage(kind="new", keyword_hit_ids=hits, candidate_ids=candidates)
        if not plugins:
            return Triage(kind="none", reason="no_match")
        if not ask_model:
            # Asked by a query, which stays local: whether one of the plugins
            # fits after all is left open rather than answered with a no.
            return Triage(kind="unsure")
        # Nothing matched by its words and there is no familiar to fall back
        # on. Before saying it cannot be done, let the small model look once.
        coarse = await self._coarse_screen(match_text, plugins, request.lang)
        known = {str(plugin.get("id")) for plugin in plugins}
        picked = [plugin_id for plugin_id in coarse if plugin_id in known]
        if picked:
            return Triage(kind="new", candidate_ids=picked, coarse_ids=coarse, model_calls=1)
        return Triage(kind="none", reason="no_match", coarse_ids=coarse, model_calls=1)

    async def _coarse_screen(self, user_text: str, plugins: Sequence[dict[str, Any]], lang: str) -> list[str]:
        """Ask the small model which plugins might be relevant; ids only."""
        from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
        from config.prompts.prompts_agent import USER_PLUGIN_COARSE_SCREEN_PROMPT
        from config.prompts.prompts_sys import _loc
        from utils.tokenize import count_tokens, truncate_to_tokens

        summaries = []
        for plugin in plugins:
            plugin_id = plugin.get("id", "unknown")
            short = plugin.get("short_description") or plugin.get("description", "")
            if count_tokens(short) > AGENT_PLUGIN_SHORTDESC_MAX_TOKENS:
                short = truncate_to_tokens(short, AGENT_PLUGIN_SHORTDESC_MAX_TOKENS - count_tokens("...")) + "..."
            summaries.append(f"- {plugin_id}: {short}")
        system = _loc(USER_PLUGIN_COARSE_SCREEN_PROMPT, lang).format(
            plugin_summaries="\n".join(summaries),
            user_text=user_text,
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
        try:
            from utils.file_utils import robust_json_loads

            raw = await self._llm.complete(TIER_COARSE, messages)
            ids = robust_json_loads(_strip_fences(raw))
            if isinstance(ids, list):
                return [str(item) for item in ids if isinstance(item, (str, int))]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keyword and BM25 matches still stand
            logger.warning("coarse screen failed: %s", type(exc).__name__)
        return []

    # -- second round -----------------------------------------------------

    def _active_rows(self, request: DecisionRequest) -> tuple[list[str], list[Commission]]:
        """Return the commissions shown to the fine selection, as lines and as rows."""
        from config.nekoclaw_settings import DECISION_LEDGER_ROW_MAX_TOKENS, DECISION_LEDGER_ROWS
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
        from config.nekoclaw_settings import (
            DISPATCH_TEXT_MAX_TOKENS,
            DISPATCH_USER_TEXT_MAX_TOKENS,
            PREFERENCE_PROMPT_MAX_TOKENS,
        )
        from config.prompts.prompts_nekoclaw_brain import (
            NO_PLUGINS_DESC,
            TASK_BRAIN_SELECT_PROMPT,
            build_selection_request,
        )
        from config.prompts.prompts_sys import _loc
        from utils.tokenize import count_tokens, truncate_to_tokens

        fallback_route = "familiar" if request.familiar_available else "none"
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
                    (kept, _), coarse_ids = await asyncio.gather(
                        asyncio.to_thread(stage1_filter, match_text, all_plugins),
                        self._coarse_screen(match_text, all_plugins, request.lang),
                    )
                    calls += 1
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
            hint = _correction_hint(decision, valid_entries)
            if hint is not None:
                # Ask once more with the mistake pointed out, as the analyzer did.
                from config.prompts.prompts_nekoclaw_brain import SELECT_CORRECTION_TEMPLATE

                messages.append({"role": "assistant", "content": _strip_fences(raw)})
                messages.append({"role": "user", "content": SELECT_CORRECTION_TEMPLATE.format(hint=hint)})
                calls += 1
                retried = None
                try:
                    retried = parse_json_object(await self._llm.complete(TIER_FINE, messages))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the first answer is judged instead
                    logger.warning("correction request failed: %s", type(exc).__name__)
                if retried is not None:
                    decision = retried
                still_wrong = _wants_plugin(decision) and _correction_hint(decision, valid_entries) is not None
                _count("plugin_assess_correction_retry", result="fail" if still_wrong or not _wants_plugin(decision) else "success")

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

        if _wants_plugin(decision):
            plugin_id, entry_id = _identifiers(decision)
            problem = _correction_hint(decision, valid_entries)
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


def _correction_hint(decision: dict[str, Any], valid_entries: dict[str, list[str]]) -> str | None:
    """Return what is wrong with the named plugin and entry, or None when they exist."""
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
