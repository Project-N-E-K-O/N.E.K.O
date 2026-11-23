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

"""The orchestrator: which task agent a newly dispatched commission goes to.

A project is a matter, not a directory: work that comes up again and again
becomes one, and until then every commission is a loose one. So where a new
sentence goes is decided by what it is about, against what is there: the
projects (their names and what they are for), the task agents (their cards
and what they did lately), loose ones included.

Four layers, each asked only when the one before it could not tell:

1. rules: a project or a task agent is named; the user asked for a new one.
   No model, no score.
2. retrieval: how much of the sentence's own words occur in what a project
   or a task agent is about (``rank``). A match that is clear attaches the
   sentence, one that is clearly none makes it a loose commission. No model.
3. the small model, for what lies in between: it is shown the few best
   matches and says which one, if any (``question`` and ``read_answer``).
4. the leader: a project that has one gets the sentence whatever it is
   about in that project, and its leader says who does it.

``settle`` turns what the layers found (a project, a task agent, or
nothing) into where the commission goes: one of ``ACTIONS``.

Everything here is a pure function of what it is given. Nothing reads the
ledger or asks a model itself.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from config import nekopower_settings as settings
from nekopower.host.selection import _PAIRED_CHAR_RE, token_set

_HIRAGANA_RE = re.compile("[\u3040-\u309f]")

# Where a commission goes.
CONTINUE = "continue"  # a task agent that is there goes on with it
SUMMON_IN_PROJECT = "summon_in_project"  # a new task agent, in a project
SUMMON_STRAY = "summon_stray"  # a new loose task agent
TO_LEADER = "to_leader"  # the leader of a project (made when it has none yet)
FORM_FAMILY = "form_family"  # loose task agents that did this before become a project
ACTIONS = (CONTINUE, SUMMON_IN_PROJECT, SUMMON_STRAY, TO_LEADER, FORM_FAMILY)

# How a sentence relates to the matter it belongs to.
NEW = "new"  # it belongs to none
FOLLOW_UP = "follow_up"  # it changes, fixes, extends or asks about what was made
RECURRENCE = "recurrence"  # the same job once more, for another date, version or item
RELATIONS = (NEW, FOLLOW_UP, RECURRENCE)

LAYER_RULES = "rules"
LAYER_RETRIEVAL = "retrieval"
LAYER_MODEL = "model"

PROJECT_KEY = "project:"
AGENT_KEY = "agent:"

# The preset of a task agent that works and may start others: what a loose
# commission gets, and a project that has no leader.
WORKING_PRESET = "general"
# How many of the candidates a verdict was chosen among are kept on record.
RECORDED_CANDIDATES = 5


# ---------------------------------------------------------------------------
# What is there
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Done:
    """A commission a task agent worked on: what it was, what came of it, how long ago, and how it was filed."""

    title: str
    result: str = ""
    age_seconds: float = 0.0
    filed: str = NEW


@dataclass(frozen=True, slots=True)
class Agent:
    """A task agent as the orchestrator sees it: its card and what it did lately, the newest first."""

    id: str
    project: str | None = None
    preset: str = WORKING_PRESET
    title: str = ""
    duty: str = ""
    summary: str = ""
    # How long ago its last commission ended; 0 while one is under way.
    idle_seconds: float = 0.0
    done: tuple[Done, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectView:
    """A project as the orchestrator sees it."""

    name: str
    description: str = ""
    in_git: bool = False
    # The session of its leader; None when it has none.
    leader: str | None = None


@dataclass(frozen=True, slots=True)
class Standing:
    """Everything a sentence is held against."""

    projects: tuple[ProjectView, ...] = ()
    agents: tuple[Agent, ...] = ()

    def project(self, name: str) -> ProjectView | None:
        return next((item for item in self.projects if item.name == name), None)

    def agent(self, agent_id: str) -> Agent | None:
        return next((item for item in self.agents if item.id == agent_id), None)

    def members(self, project: str) -> list[Agent]:
        return [item for item in self.agents if item.project == project]

    def strays(self) -> list[Agent]:
        return [item for item in self.agents if item.project is None]


@dataclass(frozen=True, slots=True)
class Bars:
    """The numbers the layers go by (``config/nekopower_settings.py``)."""

    # At or above this score, on this many of the sentence's words, and
    # ahead of the next match by ``margin``, a match is clear: the sentence
    # is attached, and no model is asked.
    attach: float = settings.ORCHESTRATOR_ATTACH_SCORE
    attach_hits: int = settings.ORCHESTRATOR_ATTACH_HITS
    margin: float = settings.ORCHESTRATOR_ATTACH_MARGIN
    # With fewer of its own words found in any match, nothing matches: a
    # loose commission, and no model is asked.
    min_hits: int = settings.ORCHESTRATOR_MIN_HITS
    # How many matches the small model is shown.
    shown: int = settings.ORCHESTRATOR_CANDIDATES_SHOWN
    # How often a matter has to have come up, this time included, for the
    # loose task agents that did it to become a project.
    family_min: int = settings.ORCHESTRATOR_FAMILY_MIN_OCCURRENCES
    # A loose task agent that scores this much of the one a sentence goes
    # with, on this many words at least, did the same thing.
    peer: float = settings.ORCHESTRATOR_PEER_SHARE
    peer_hits: int = settings.ORCHESTRATOR_PEER_HITS
    # A name shorter than this (white space removed) is not looked for in a sentence.
    name_min_chars: int = settings.SPACE_PROJECT_MATCH_MIN_CHARS
    # A task agent whose last commission ended longer ago takes no more work.
    retire_seconds: float = settings.SESSION_SINGLE_LIFETIME_DAYS * 24 * 3600.0


@dataclass(frozen=True, slots=True)
class Match:
    """One project or loose task agent, and how well a sentence matches it."""

    key: str
    # The share of the sentence's own words that occur there, weighed (0 to 1).
    score: float
    # How many of them do.
    hits: int = 0


@dataclass(slots=True)
class Verdict:
    """Where a commission goes, and how that was found."""

    action: str
    # The task agent that takes it (``CONTINUE``; with ``FORM_FAMILY``, the member that does, None for a new one).
    agent: str | None = None
    project: str | None = None
    # The preset of the task agent that is started, when one is.
    preset: str | None = None
    # ``FORM_FAMILY``: the loose task agents that become the project's.
    members: tuple[str, ...] = ()
    relation: str = NEW
    layer: str = LAYER_RULES
    # The matches it was chosen among, the best first.
    candidates: list[Match] = field(default_factory=list)
    model_calls: int = 0
    reason: str = ""
    # How long finding it took, the small model's answer included.
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# Reading the sentence
# ---------------------------------------------------------------------------


def _cue(name: str) -> list[re.Pattern[str]]:
    from config.prompts import prompts_nekopower_brain as texts

    patterns = {"refers_back": texts.REFERS_BACK_PATTERNS, "fresh_start": texts.FRESH_START_PATTERNS}[name]
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _has(name: str, text: str) -> bool:
    return any(pattern.search(text) for pattern in _cue(name))


def sentence_of(dispatch_text: str, user_text: str = "") -> str:
    """Return what is matched: the user's words and hers (as ``DecisionRequest.match_text``)."""
    user, task = user_text.strip(), dispatch_text.strip()
    if user and task and user != task:
        return f"{user}\n{task}"
    return user or task


def _filler(token: str) -> bool:
    """Whether a token says nothing of what a sentence is about: a particle, a function word, a lone character."""
    from config.prompts.prompts_nekopower_brain import ORCHESTRATOR_FILLER_CHARS, ORCHESTRATOR_FILLER_WORDS

    if len(token) == 1 or token in ORCHESTRATOR_FILLER_WORDS:
        return True
    if _PAIRED_CHAR_RE.fullmatch(token[0]) and _PAIRED_CHAR_RE.fullmatch(token[-1]):
        # A piece of text written without spaces: one that holds a particle,
        # or is hiragana only (endings and particles), carries no word.
        return any(char in ORCHESTRATOR_FILLER_CHARS for char in token) or all(
            _HIRAGANA_RE.fullmatch(char) for char in token
        )
    return False


def content_tokens(text: str) -> frozenset[str]:
    """Return the tokens of a sentence that say what it is about: without words every request has."""
    from nekopower.host.decision import strip_cues

    tokens = token_set(strip_cues("generic", strip_cues("supplement", text)))
    return frozenset(token for token in tokens if not _filler(token))


def _lines_of_agent(agent: Agent) -> list[str]:
    lines = [agent.title, agent.duty, agent.summary]
    for item in agent.done:
        lines.append(item.title)
        lines.append(item.result)
    return [line for line in lines if line and line.strip()]


def _lines_of_project(project: ProjectView, members: Sequence[Agent]) -> list[str]:
    lines = [project.name, project.description]
    for member in members:
        lines.extend(_lines_of_agent(member))
    return [line for line in lines if line and line.strip()]


def named(sentence: str, standing: Standing, bars: Bars) -> str | None:
    """Return the key of the project or task agent a sentence names, or None.

    A project by its name, a task agent by the title on its card. The
    longest name wins, a project before a task agent of the same length;
    two task agents of one title name neither.
    """
    from nekopower.host.space import name_occurs, normalize_name

    best: tuple[int, int, str] | None = None
    tied = False
    for project in standing.projects:
        length = len(normalize_name(project.name))
        if length < bars.name_min_chars or not name_occurs(project.name, sentence):
            continue
        rank = (length, 1, PROJECT_KEY + project.name)
        if best is None or rank[:2] > best[:2]:
            best, tied = rank, False
    for agent in standing.agents:
        length = len(normalize_name(agent.title))
        if length < bars.name_min_chars or not name_occurs(agent.title, sentence):
            continue
        rank = (length, 0, AGENT_KEY + agent.id)
        if best is None or rank[:2] > best[:2]:
            best, tied = rank, False
        elif rank[:2] == best[:2]:
            tied = True
    if best is None or tied:
        return None
    return best[2]


def relation_by_words(sentence: str, *, names_agent: bool = False) -> str:
    """Say how a sentence relates to the matter it was matched with, by its words alone.

    A sentence that points back at something made ("that list", "the one
    from last time"), that adds to it, or that speaks to a task agent by
    its title, follows it up. Any other is the same job once more.
    """
    from nekopower.host.decision import has_cue

    if names_agent or _has("refers_back", sentence) or has_cue("supplement", sentence):
        return FOLLOW_UP
    return RECURRENCE


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def rank(sentence: str, standing: Standing) -> list[Match]:
    """Return every project and loose task agent with how well the sentence matches it, the best first.

    A project is matched by its name, what it is for, and the cards and
    late commissions of its members; a loose task agent by its card and
    its late commissions. The score is the share of the sentence's own
    words that occur there, each word weighed by how few of the others
    have it (as BM25 weighs them), so that a word every card has counts
    for little.
    """
    words = content_tokens(sentence)
    documents: list[tuple[str, frozenset[str]]] = []
    for project in standing.projects:
        lines = _lines_of_project(project, standing.members(project.name))
        documents.append((PROJECT_KEY + project.name, token_set("\n".join(lines))))
    for agent in standing.strays():
        documents.append((AGENT_KEY + agent.id, token_set("\n".join(_lines_of_agent(agent)))))
    if not words or not documents:
        return [Match(key=key, score=0.0) for key, _tokens in documents]

    whole = [tokens for _key, tokens in documents]
    count = len(documents)

    def weight(token: str) -> float:
        have = sum(1 for tokens in whole if token in tokens)
        return math.log((count - have + 0.5) / (have + 0.5) + 1.0)

    weights = {token: weight(token) for token in words}
    total = sum(weights.values()) or 1.0
    matches: list[Match] = []
    for key, tokens in documents:
        shared = words & tokens
        matches.append(Match(key=key, score=sum(weights[token] for token in shared) / total, hits=len(shared)))
    matches.sort(key=lambda match: (match.score, match.hits), reverse=True)
    return matches


_SCRIPTS = (
    ("han", re.compile("[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")),
    ("kana", re.compile("[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")),
    ("hangul", re.compile("[\uac00-\ud7af\u1100-\u11ff]")),
    ("cyrillic", re.compile("[\u0400-\u04ff]")),
    ("latin", re.compile("[A-Za-z\u00c0-\u024f]")),
)


def _script(token: str) -> str | None:
    """Return the script a token is written in, by its first letter; None for digits and the like."""
    for name, pattern in _SCRIPTS:
        if pattern.match(token):
            return name
    return None


def unseen(sentence: str, standing: Standing) -> bool:
    """Whether a sentence is written in a script that nothing that is there is written in.

    Words are compared as they are written, so a request in Korean shares
    no word with cards in English whatever it is about: that nothing
    matches says nothing then, and the small model is asked.
    """
    mine = [_script(token) for token in content_tokens(sentence)]
    mine = [script for script in mine if script is not None]
    if not mine:
        return False
    lines: list[str] = []
    for project in standing.projects:
        lines.extend(_lines_of_project(project, standing.members(project.name)))
    for agent in standing.strays():
        lines.extend(_lines_of_agent(agent))
    theirs = {_script(token) for token in token_set("\n".join(lines))} - {None}
    if not theirs:
        return False
    return sum(1 for script in mine if script in theirs) * 2 < len(mine)


def _idle(key: str, standing: Standing) -> float:
    """How long ago a project or a loose task agent last worked."""
    if key.startswith(PROJECT_KEY):
        return min((member.idle_seconds for member in standing.members(key[len(PROJECT_KEY) :])), default=float("inf"))
    agent = standing.agent(key[len(AGENT_KEY) :])
    return agent.idle_seconds if agent is not None else float("inf")


def clear_match(matches: Sequence[Match], bars: Bars) -> tuple[bool, Match | None]:
    """Say whether retrieval settles it: (settled, the match it settles on or None for nothing).

    Settled on the best match when it is clear and stands ahead of the
    next; settled on nothing when even the best is too weak. Otherwise
    not settled: what lies in between is for the small model.
    """
    if not matches or max(match.hits for match in matches) < bars.min_hits:
        return True, None
    best = matches[0]
    runner_up = matches[1].score if len(matches) > 1 else 0.0
    if best.score >= bars.attach and best.hits >= bars.attach_hits and best.score - runner_up >= bars.margin:
        return True, best
    return False, None


# ---------------------------------------------------------------------------
# The small model
# ---------------------------------------------------------------------------


def candidate_line(key: str, standing: Standing, max_tokens: int) -> str:
    """Return one project or loose task agent as the line the small model reads."""
    from utils.tokenize import truncate_to_tokens

    if key.startswith(PROJECT_KEY):
        project = standing.project(key[len(PROJECT_KEY) :])
        if project is None:
            return ""
        parts = [f"[project] {project.name}: {project.description}".rstrip(": ")]
        duties = [member.duty for member in standing.members(project.name) if member.duty]
        if duties:
            parts.append("its agents do: " + "; ".join(duties))
        late = [item.title for member in standing.members(project.name) for item in member.done[:1]]
        if late:
            parts.append("lately: " + "; ".join(late))
    else:
        agent = standing.agent(key[len(AGENT_KEY) :])
        if agent is None:
            return ""
        head = agent.title or agent.duty
        parts = [f"[agent] {head}" + (f": {agent.duty}" if agent.title and agent.duty else "")]
        did = [f"{item.title} -> {item.result}" if item.result else item.title for item in agent.done[:2]]
        if did:
            parts.append("did: " + "; ".join(did))
    return truncate_to_tokens(" | ".join(parts), max_tokens)


def question(
    dispatch_text: str, user_text: str, keys: Sequence[str], standing: Standing, *, line_max_tokens: int
) -> list[dict[str, Any]]:
    """Return the request that asks the small model which of ``keys`` a sentence belongs with."""
    from config.prompts.prompts_nekopower_brain import ORCHESTRATOR_PROMPT, build_orchestrator_request

    lines = [candidate_line(key, standing, line_max_tokens) for key in keys]
    return [
        {"role": "system", "content": ORCHESTRATOR_PROMPT},
        {"role": "user", "content": build_orchestrator_request(task=dispatch_text, user_said=user_text, candidates=lines)},
    ]


def read_answer(raw: str, keys: Sequence[str]) -> tuple[str | None, str] | None:
    """Read the small model's answer: (the key it chose or None for none of them, the relation). None when unreadable."""
    from nekopower.host.decision import parse_json_object

    answer = parse_json_object(raw or "")
    if answer is None:
        return None
    return read_choice(answer, keys)


def read_choice(answer: dict[str, Any], keys: Sequence[str]) -> tuple[str | None, str] | None:
    """Read ``goes_to`` and ``relation`` out of an answer that was parsed already."""
    number = answer.get("goes_to")
    if isinstance(number, str) and number.strip().lstrip("-").isdigit():
        number = int(number.strip())
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    if number <= 0:
        return None, NEW
    if number > len(keys):
        return None
    relation = str(answer.get("relation") or "").strip().lower()
    if relation not in (FOLLOW_UP, RECURRENCE):
        relation = FOLLOW_UP
    return keys[number - 1], relation


# ---------------------------------------------------------------------------
# From what was found to where it goes
# ---------------------------------------------------------------------------


def _takes_work(agent: Agent, bars: Bars) -> bool:
    return agent.idle_seconds < bars.retire_seconds


def _occurrences(agents: Sequence[Agent]) -> int:
    """How often the matter of these task agents came up so far: what was filed as new or as the same job once more."""
    return sum(1 for agent in agents for item in agent.done if item.filed in (NEW, RECURRENCE))


def settle(
    key: str | None,
    relation: str,
    standing: Standing,
    bars: Bars,
    *,
    matches: Sequence[Match] = (),
    layer: str = LAYER_RULES,
    model_calls: int = 0,
    reason: str = "",
) -> Verdict:
    """Turn the project or task agent a sentence belongs with into where the commission goes."""
    shown = list(matches)

    def verdict(action: str, **more: Any) -> Verdict:
        return Verdict(action=action, layer=layer, candidates=shown, model_calls=model_calls, reason=reason, **more)

    target = standing.agent(key[len(AGENT_KEY) :]) if key and key.startswith(AGENT_KEY) else None
    project = standing.project(key[len(PROJECT_KEY) :]) if key and key.startswith(PROJECT_KEY) else None
    if target is not None and target.project is not None:
        # A member that was named: the sentence is its project's.
        project = standing.project(target.project)
    if project is not None:
        if project.leader is not None or project.in_git:
            # Its leader says who does it. A project in a git repository gets one now.
            return verdict(TO_LEADER, project=project.name, relation=relation)
        working = [member for member in standing.members(project.name) if _takes_work(member, bars)]
        if target is not None and target in working:
            return verdict(CONTINUE, agent=target.id, project=project.name, relation=relation)
        at_work = [member for member in working if member.preset == WORKING_PRESET]
        if at_work:
            latest = min(at_work, key=lambda member: member.idle_seconds)
            return verdict(CONTINUE, agent=latest.id, project=project.name, relation=relation)
        return verdict(SUMMON_IN_PROJECT, project=project.name, preset=WORKING_PRESET, relation=relation)
    if target is None:
        return verdict(SUMMON_STRAY, preset=WORKING_PRESET, relation=NEW)

    # A loose task agent. Those that did the same thing go with it.
    found = {match.key: match for match in matches}
    own = found.get(AGENT_KEY + target.id)
    peers = [target]
    for stray in standing.strays():
        other = found.get(AGENT_KEY + stray.id)
        if stray.id == target.id or own is None or other is None or own.score <= 0:
            continue
        if other.hits >= bars.peer_hits and other.score >= bars.peer * own.score:
            peers.append(stray)
    if relation == RECURRENCE and _occurrences(peers) + 1 >= bars.family_min:
        working = [peer for peer in peers if _takes_work(peer, bars)]
        taker = min(working, key=lambda peer: peer.idle_seconds).id if working else None
        return verdict(
            FORM_FAMILY,
            agent=taker,
            preset=None if taker else WORKING_PRESET,
            members=tuple(sorted(peer.id for peer in peers)),
            relation=relation,
        )
    if _takes_work(target, bars):
        return verdict(CONTINUE, agent=target.id, relation=relation)
    # It takes no more work: a new one, which is told where the old files are.
    return verdict(SUMMON_STRAY, preset=WORKING_PRESET, relation=relation)


def by_rules(dispatch_text: str, user_text: str, standing: Standing, bars: Bars) -> Verdict | None:
    """Return where a sentence goes when its words say so outright, or None."""
    sentence = sentence_of(dispatch_text, user_text)
    key = named(sentence, standing, bars)
    if key is not None and key.startswith(PROJECT_KEY):
        # A project that is named has it, whatever else the sentence says.
        return settle(key, FOLLOW_UP, standing, bars, reason="named")
    if _has("fresh_start", sentence):
        return settle(None, NEW, standing, bars, reason="asked for a new one")
    if key is not None:
        return settle(key, relation_by_words(sentence, names_agent=True), standing, bars, reason="named")
    return None


def by_retrieval(
    dispatch_text: str, user_text: str, standing: Standing, bars: Bars
) -> tuple[Verdict | None, list[Match]]:
    """Return where a sentence goes when retrieval settles it (else None), and the matches.

    Not settled, besides what ``clear_match`` leaves open: a clear match
    that would make a project of loose task agents.
    """
    sentence = sentence_of(dispatch_text, user_text)
    matches = rank(sentence, standing)
    if matches and unseen(sentence, standing):
        return None, matches
    settled, best = clear_match(matches, bars)
    if not settled:
        return None, matches
    if best is None:
        return settle(None, NEW, standing, bars, matches=matches, layer=LAYER_RETRIEVAL, reason="nothing matches"), matches
    relation = relation_by_words(sentence) if best.key.startswith(AGENT_KEY) else FOLLOW_UP
    verdict = settle(best.key, relation, standing, bars, matches=matches, layer=LAYER_RETRIEVAL, reason="clear match")
    if verdict.action == FORM_FAMILY:
        # No project is made on words alone: whether this is the same job
        # once more, or one more change to what was made, the words do not
        # tell (``relation_by_words`` guesses). The small model says.
        return None, matches
    return verdict, matches


def shown_keys(matches: Sequence[Match], bars: Bars, sentence: str = "", standing: Standing | None = None) -> list[str]:
    """Return the matches the small model is shown: the few best that share anything with the sentence.

    For a sentence in a script nothing there is written in (``unseen``),
    which shares nothing with any of them: those that worked last.
    """
    if standing is not None and unseen(sentence, standing):
        return sorted((match.key for match in matches), key=lambda key: _idle(key, standing))[: bars.shown]
    return [match.key for match in matches[: bars.shown] if match.hits > 0]


def as_record(verdict: Verdict) -> dict[str, Any]:
    """Return a verdict as it is kept on record: no sentence, only keys and numbers."""
    return {
        "action": verdict.action,
        "agent": verdict.agent,
        "project": verdict.project,
        "preset": verdict.preset,
        "members": list(verdict.members),
        "relation": verdict.relation,
        "layer": verdict.layer,
        "candidates": [[match.key, round(match.score, 3), match.hits] for match in verdict.candidates[:RECORDED_CANDIDATES]],
        "model_calls": verdict.model_calls,
        "reason": verdict.reason,
        "seconds": verdict.seconds,
    }


def to_json(verdict: Verdict) -> str:
    """Return ``as_record`` as a text."""
    return json.dumps(as_record(verdict), ensure_ascii=False)
