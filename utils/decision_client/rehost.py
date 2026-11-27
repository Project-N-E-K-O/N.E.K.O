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

"""Ask a decision question on a chat model that returns token probabilities.

For vendors without a native decision model whose chat API returns the top
candidates of each token (see :mod:`utils.decision_client.routing`), a
:class:`~utils.decision_client.schema.ChoiceQuestion` goes to the chat model
unchanged: its instructions and options, labelled with letters, in the system
message; the state as the user message; one output token. The candidates of
that token are kept to the option letters and renormalised over them, which
gives the same kind of answer a native decision returns, for the site's own
reading.

Letters, not numbers: Doubao and DeepSeek split a two-digit number into two
tokens. No assistant prefill: the question already asks for the letter only,
and without one the first token was a letter on every vendor tested
(DeepSeek, Kimi, StepFun, Doubao; 2026-11-27).

Every miss (an error, a timeout, no probabilities, no letter among them)
returns ``None``; the caller then asks its own chat prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from typing import Any, Iterable, Mapping

from utils.logger_config import get_module_logger

from .schema import ChoiceQuestion, DecisionAnswer

logger = get_module_logger(__name__)

REHOST_LETTERS: tuple[str, ...] = tuple(chr(code) for code in range(ord("A"), ord("Z") + 1))
REHOST_TOP_LOGPROBS = 20
REHOST_REPLY_RULE = "Reply with the option letter only."


def _state_text(state: Any) -> str:
    if not isinstance(state, str):
        try:
            state = json.dumps(state, ensure_ascii=False)
        except (TypeError, ValueError):
            state = str(state)
    from config import DECISION_STATE_MAX_TOKENS
    from utils.tokenize import truncate_head_tail_tokens

    half = max(1, DECISION_STATE_MAX_TOKENS // 2)
    return truncate_head_tail_tokens(state, half, half)


def build_rehost_messages(state: Any, question: ChoiceQuestion) -> tuple[list[dict[str, str]], list[str]]:
    """Chat messages asking *question* about *state*, and the option keys in letter order."""
    keys = list(question.criteria)
    if len(keys) > len(REHOST_LETTERS):
        raise ValueError(f"a rehosted question takes at most {len(REHOST_LETTERS)} options, got {len(keys)}")
    options = "\n".join(f"{REHOST_LETTERS[i]}. {question.criteria[key]}" for i, key in enumerate(keys))
    system = f"{question.instructions}\n\nOptions:\n{options}\n\n{REHOST_REPLY_RULE}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _state_text(state)},
    ], keys


def letter_probabilities(candidates: Iterable[tuple[str, float]], keys: list[str]) -> dict[str, float]:
    """Option probabilities from the first token's candidates, over the letters only.

    A candidate counts when, without spaces and a trailing full stop, it is one
    of the first ``len(keys)`` letters; anything else (words, other letters)
    is dropped before renormalising. Empty when no candidate is a letter.
    """
    probs: dict[str, float] = {}
    for token, logprob in candidates:
        letter = str(token or "").strip().rstrip(".").strip()
        if len(letter) != 1 or letter not in REHOST_LETTERS:
            continue
        idx = REHOST_LETTERS.index(letter)
        if idx >= len(keys):
            continue
        try:
            p = math.exp(float(logprob))
        except (TypeError, ValueError, OverflowError):
            continue
        probs[keys[idx]] = probs.get(keys[idx], 0.0) + p
    total = sum(probs.values())
    if total <= 0.0:
        return {}
    return {key: p / total for key, p in probs.items()}


async def rehost_choice(
    *,
    state: Any,
    question: ChoiceQuestion,
    chat_config: Mapping[str, Any],
    timeout: float = 8.0,
    call_type: str | None = None,
) -> DecisionAnswer | None:
    """Ask *question* on the chat model of *chat_config*; ``None`` on any miss.

    *chat_config* is a resolved chat slot (``model``, ``base_url``, ``api_key``,
    ``provider_type``). The answer's ``probabilities`` cover the options seen
    among the first token's candidates and sum to 1.
    """
    from utils.llm_client import first_token_top_logprobs
    from utils.llm_client.warm_pool import get_warm_chat_llm

    try:
        messages, keys = build_rehost_messages(state, question)
        llm = await get_warm_chat_llm(
            str(chat_config.get("model") or ""),
            str(chat_config.get("base_url") or ""),
            str(chat_config.get("api_key") or ""),
            provider_type=chat_config.get("provider_type"),
            max_retries=0,
        )
        call_ctx: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
        if call_type:
            from utils.token_tracker import llm_call_context

            call_ctx = llm_call_context(call_type)
        with call_ctx:
            raw = await asyncio.wait_for(
                llm.ainvoke_raw(  # noqa: LLM_INPUT_BUDGET  # the state is truncated to DECISION_STATE_MAX_TOKENS in build_rehost_messages.
                    messages,
                    max_completion_tokens=1,
                    logprobs=True,
                    top_logprobs=REHOST_TOP_LOGPROBS,
                    timeout=timeout,
                ),
                timeout=timeout,
            )
    except Exception as exc:
        logger.info("[rehost] %s failed on %s (%s); using the site's chat prompt",
                    call_type or "question", chat_config.get("model"), type(exc).__name__)
        return None
    probs = letter_probabilities(first_token_top_logprobs(raw), keys)
    if not probs:
        logger.info("[rehost] %s: no option letter among the first token's candidates on %s; "
                    "using the site's chat prompt", call_type or "question", chat_config.get("model"))
        return None
    best = max(probs, key=probs.get)
    return DecisionAnswer(
        question_type="choice",
        value=best,
        probability=probs[best],
        probabilities=probs,
    )


async def rehost_choices(
    *,
    state: Any,
    questions: Mapping[str, ChoiceQuestion],
    chat_config: Mapping[str, Any],
    timeout: float = 8.0,
    call_type: str | None = None,
) -> dict[str, DecisionAnswer] | None:
    """Ask several questions about one state, one chat call each, all at once.

    ``None`` when any of them misses, so the caller falls back as a whole.
    """
    ids = list(questions)
    answers = await asyncio.gather(*(
        rehost_choice(
            state=state,
            question=questions[qid],
            chat_config=chat_config,
            timeout=timeout,
            call_type=call_type,
        )
        for qid in ids
    ))
    if any(answer is None for answer in answers):
        return None
    return dict(zip(ids, answers))
