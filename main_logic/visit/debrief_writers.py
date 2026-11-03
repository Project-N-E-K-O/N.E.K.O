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

"""The "keep as diary" writers of the home-coming debrief (OD-16 v4, design §3.7.4, §5 PR-14).

Two separate steps, so the user reads exactly what will be stored before it is:

* :func:`generate_diary_preview` -- one LLM call over the visit's spool
  (whole lines from the newest backwards within
  ``VISIT_DEBRIEF_INPUT_MAX_TOKENS``, the peer's lines inside
  ``VISIT_PEER_LINES_BLOCK``) producing a first-person diary entry and at most
  ``VISIT_DIARY_FACTS_MAX`` facts. Cleaning, truncation and the peer n-gram
  check all happen here; nothing is written anywhere. The returned
  :class:`DiaryPreview` is byte for byte what a later commit stores.
* :func:`commit_diary` -- the two-step write of a persisted preview: first
  ``POST /internal/memory/{name}/visit_facts`` (skipped when there are no
  facts), then ``POST /cache/{name}`` with
  ``idempotency_key='visit-debrief:{visit_id}'``. Steps already done in
  ``debrief_writes`` are skipped; every request reports its progress through
  ``on_transition`` *before* it is sent (``inflight``) and after it answered
  (``confirmed`` / ``rejected`` / ``indeterminate``), so the caller can
  persist it and a crash never loses the evidence of a write that may have
  happened. The text is never cleaned or cut again.

Failures are classified by :func:`classify_write_failure` (owner decision of
2026-10-02): 5xx, connection errors, timeouts, a 200 whose body does not
confirm the write, 408, 409 and 429 are transient (retried with backoff,
without a bound); any other status is permanent (retrying stops until the
user chooses). The state machine around both lives in
``main_routers/visit_router/debrief.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import httpx

from config.prompts.prompts_visit import (
    build_visit_diary_prompt,
    escape_visit_block_text,
    get_family_neutral_term,
    get_visit_debrief_fallback,
)
from config.visit_settings import (
    VISIT_DEBRIEF_INPUT_MAX_TOKENS,
    VISIT_DIARY_FACT_MAX_CHARS,
    VISIT_DIARY_FACTS_MAX,
    VISIT_DIARY_MAX_TOKENS,
    VISIT_LLM_TIMEOUT_S,
    VISIT_PEER_NGRAM_N,
)
from main_logic.visit.memory_commit import record_block_within_budget
from main_logic.visit.sanitize import PeerNgramHit, assert_no_peer_ngram, redact_outbound, strip_emotion_tags
from main_logic.visit.spool import VisitSpool
from utils.logger_config import get_module_logger
from utils.tokenize import truncate_to_tokens

logger = get_module_logger(__name__, "Main")

STEP_FACTS = "facts"
STEP_CACHE = "cache"

TRANSIENT_STATUSES = frozenset({408, 409, 429})
"""Non-5xx statuses retried automatically (request timeout, limited mode / release fence, rate limit)."""

_WRITE_TIMEOUT_S = 15.0
"""Bound of one write request (the keyed /cache write stores to two places)."""

_PEER_SPEAKERS = ("peer_cat", "peer_human")

DiaryLLM = Callable[[str], Awaitable[str]]
"""Prompt in, reply text out; raises on failure."""

OnTransition = Callable[[str, str, Mapping[str, Any]], Awaitable[None]]
"""``on_transition(step, event, detail)``; ``event`` is ``inflight`` / ``confirmed`` / ``rejected`` / ``indeterminate``."""


class NothingToRecord(RuntimeError):
    """The visit has no transcript line to write a diary about (spool missing or empty)."""


class DiaryGenerationFailed(RuntimeError):
    """The diary LLM call failed, timed out or returned something unusable (retry may succeed)."""


@dataclass(frozen=True)
class DiaryPreview:
    """What "keep as diary" will store: the diary entry and its facts, already final."""

    diary: str
    facts: tuple[str, ...]

    def to_pending(self) -> dict:
        """The ``state.json.debrief_pending`` document (and the 200 ``preview`` body)."""
        return {"diary": self.diary, "facts": list(self.facts)}


@dataclass(frozen=True)
class WriteOutcome:
    """Classification of one write request: ``ok``, ``transient`` or ``permanent``.

    ``indeterminate`` marks a request that may have been applied although it
    was not confirmed (read timeout, broken connection, 5xx, a 200 whose body
    does not confirm); ``written`` is the ``visit_facts`` count when known.
    """

    kind: str
    status: int
    indeterminate: bool = False
    written: int = 0


@dataclass(frozen=True)
class CommitResult:
    """Outcome of :func:`commit_diary`: ``done``, or the step that failed ``transient`` / ``permanent``."""

    kind: str
    step: Optional[str] = None
    status: int = 0


# ── generation ────────────────────────────────────────────────────────────


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _clean(text: str, *, family_names: Iterable[str], neutral_term: str) -> str:
    text = strip_emotion_tags(str(text or ""))
    names = tuple(family_names)
    if names:
        text = redact_outbound(text, family_names=names, replacement=neutral_term)
    # Memory text later enters prompts: forbid delimiters that could impersonate the end of a data block.
    return escape_visit_block_text(text).strip()


def _first_json_object(text: str) -> Any:
    # Try parsing a complete object from each "{" so surrounding model prose containing braces cannot swallow the response.
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            data, _end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, dict) and "diary" in data:
            return data
        start = text.find("{", start + 1)
    raise DiaryGenerationFailed("reply has no JSON object with a diary")


def _parse_reply(raw: str) -> tuple[str, list[str]]:
    data = _first_json_object(str(raw or ""))
    diary = data.get("diary")
    facts = data.get("facts")
    if not isinstance(diary, str):
        raise DiaryGenerationFailed("reply has no diary text")
    return diary, [f for f in facts if isinstance(f, str)] if isinstance(facts, list) else []


def _copies_peer(text: str, peer_lines: list[str]) -> bool:
    try:
        assert_no_peer_ngram(text, peer_lines, n=VISIT_PEER_NGRAM_N)
    except PeerNgramHit:
        return True
    return False


def finish_preview(
    raw: str, *, peer_lines: list[str], family_names: Iterable[str], lang: Optional[str],
) -> DiaryPreview:
    """Turn the LLM reply into the final preview (sync; run it in a worker thread).

    The diary is cleaned, cut to ``VISIT_DIARY_MAX_TOKENS`` and replaced by
    the fixed debrief line when it copies the peer or ends up empty; each fact
    is cleaned to one line of at most ``VISIT_DIARY_FACT_MAX_CHARS``
    characters and dropped when it copies the peer, is empty or repeats one
    kept before; at most ``VISIT_DIARY_FACTS_MAX`` are kept.
    """
    diary_raw, facts_raw = _parse_reply(raw)
    neutral = get_family_neutral_term(lang)
    names = tuple(family_names)
    diary = truncate_to_tokens(_clean(diary_raw, family_names=names, neutral_term=neutral),
                               VISIT_DIARY_MAX_TOKENS).strip()
    if not diary or _copies_peer(diary, peer_lines):
        logger.info("visit diary: entry empty or copied the peer, using the fixed line")
        diary = get_visit_debrief_fallback(lang)
    facts: list[str] = []
    for item in facts_raw:
        if len(facts) >= VISIT_DIARY_FACTS_MAX:
            break
        fact = _single_line(_clean(item, family_names=names, neutral_term=neutral))[:VISIT_DIARY_FACT_MAX_CHARS].strip()
        if not fact or fact in facts or _copies_peer(fact, peer_lines):
            continue
        facts.append(fact)
    return DiaryPreview(diary=diary, facts=tuple(facts))


async def generate_diary_preview(
    spool: VisitSpool,
    *,
    llm: DiaryLLM,
    own_char: str,
    family_names: Iterable[str] = (),
) -> DiaryPreview:
    """Generate the diary entry and facts of one finished visit; writes nothing.

    Reads the spool (the debrief chips are only offered when it has lines).
    Raises :class:`NothingToRecord` without a header or a line, and
    :class:`DiaryGenerationFailed` when the call fails or the reply is unusable.
    """
    contents = await spool.read_back()
    lines = [line for line in contents.lines if str(line.get("text") or "").strip()]
    if contents.header is None or not lines:
        raise NothingToRecord(spool.visit_id)
    lang = contents.header.get("lang")
    block = await asyncio.to_thread(record_block_within_budget, lines, lang, VISIT_DEBRIEF_INPUT_MAX_TOKENS)
    prompt = build_visit_diary_prompt(own_char, block, lang)
    try:
        raw = await asyncio.wait_for(llm(prompt), VISIT_LLM_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - Any failure means this generation failed; the user can retry.
        logger.warning("visit %s: diary generation failed: %s", spool.visit_id[:6], type(exc).__name__)
        raise DiaryGenerationFailed(type(exc).__name__) from None
    peer_lines = [str(line.get("text") or "") for line in lines if line.get("from") in _PEER_SPEAKERS]
    return await asyncio.to_thread(
        finish_preview, raw, peer_lines=peer_lines, family_names=tuple(family_names), lang=lang,
    )


# ── commit ────────────────────────────────────────────────────────────────


def _confirms(step: str, body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if step == STEP_CACHE:
        return body.get("status") == "cached"
    return body.get("ok") is True


def classify_write_failure(resp_or_exc: Any, step: str) -> WriteOutcome:
    """Classify one write attempt (an ``httpx.Response`` or the exception it raised).

    ``ok``: HTTP 200 whose body confirms (``/cache`` ``status=='cached'``,
    ``visit_facts`` ``ok is True``). Transient: 5xx, connection refused /
    broken, timeouts, a 200 that does not confirm or cannot be parsed, 408,
    409, 429. Permanent: every other status (400 / 404 / 422 ...): better
    handed to the user than retried forever. ``indeterminate`` is set when the
    server may have applied the write: everything transient except a refused
    connection, a connect / pool timeout and 408 / 409 / 429.
    """
    if isinstance(resp_or_exc, BaseException):
        exc = resp_or_exc
        never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
        return WriteOutcome("transient", 0, indeterminate=not never_sent)
    resp = resp_or_exc
    status = int(getattr(resp, "status_code", 0) or 0)
    if status == 200:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 - Unparseable: result unknown.
            body = None
        if _confirms(step, body):
            written = body.get("written") if step == STEP_FACTS else 0
            return WriteOutcome("ok", status, written=int(written) if isinstance(written, int) else 0)
        return WriteOutcome("transient", status, indeterminate=True)
    if status >= 500:
        return WriteOutcome("transient", status, indeterminate=True)
    if status in TRANSIENT_STATUSES:
        return WriteOutcome("transient", status)
    return WriteOutcome("permanent", status)


def _memory_base_url() -> str:
    from config import MEMORY_SERVER_PORT

    return f"http://127.0.0.1:{MEMORY_SERVER_PORT}"


def cache_request(visit_id: str, diary: str) -> dict:
    """The ``/cache`` body of a diary entry: one assistant message, keyed by the visit."""
    return {
        "input_history": json.dumps([{"role": "assistant", "content": diary}], ensure_ascii=False),
        "idempotency_key": f"visit-debrief:{visit_id}",
    }


def facts_request(visit_id: str, facts: Iterable[str]) -> dict:
    """The ``visit_facts`` body (the server stamps origin / importance / absorbed itself)."""
    return {"visit_id": visit_id, "facts": [{"text": fact} for fact in facts]}


async def _post(http: httpx.AsyncClient, url: str, body: dict, step: str) -> WriteOutcome:
    try:
        resp = await http.post(url, json=body, timeout=_WRITE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - Classification determines whether to retry.
        return classify_write_failure(exc, step)
    return classify_write_failure(resp, step)


async def commit_diary(
    lanlan_name: str,
    visit_id: str,
    pending: Mapping[str, Any],
    writes: Mapping[str, Any],
    on_transition: OnTransition,
    *,
    http: Optional[httpx.AsyncClient] = None,
    base_url: Optional[str] = None,
) -> CommitResult:
    """Write ``pending`` (``{diary, facts}``, verbatim) in two steps; skip the steps ``writes`` marks done.

    Only called by the debrief state machine (``run_commit``), which persists
    every ``on_transition`` before this function continues. Returns at the
    first failed step.
    """
    if http is None:
        from utils.http.internal_client import get_internal_http_client

        http = get_internal_http_client()
    base = (base_url or _memory_base_url()).rstrip("/")
    name = quote(lanlan_name, safe="")
    steps: list[tuple[str, str, dict]] = []
    if not writes.get("facts"):
        facts = list(pending.get("facts") or [])
        if facts:
            steps.append((STEP_FACTS, f"{base}/internal/memory/{name}/visit_facts", facts_request(visit_id, facts)))
        else:
            # With no facts, skip sending this step and mark it complete; write the diary normally.
            await on_transition(STEP_FACTS, "confirmed", {"facts_written": 0})
    if not writes.get("cache"):
        steps.append((STEP_CACHE, f"{base}/cache/{name}", cache_request(visit_id, str(pending.get("diary") or ""))))
    for step, url, body in steps:
        # Record before sending: a crash after sending but before recording results still leaves evidence of a possible write.
        await on_transition(step, "inflight", {})
        outcome = await _post(http, url, body, step)
        if outcome.kind == "ok":
            await on_transition(step, "confirmed", {"facts_written": outcome.written} if step == STEP_FACTS else {})
            continue
        await on_transition(step, "indeterminate" if outcome.indeterminate else "rejected", {"status": outcome.status})
        logger.warning("visit %s: diary write step %s failed (%s, status %s)",
                       visit_id[:6], step, outcome.kind, outcome.status)
        return CommitResult(outcome.kind, step, outcome.status)
    return CommitResult("done")


async def write_forget(spool: VisitSpool, *, final_choice: str = "forget") -> bool:
    """Record "don't keep" (or ``abandoned``): nothing goes to private memory; see ``VisitSpool.mark_forget``."""
    return await spool.mark_forget(final_choice=final_choice)
