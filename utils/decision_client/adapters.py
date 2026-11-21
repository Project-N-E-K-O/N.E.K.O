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

"""Symmetrical protocol adapters for native Decisions APIs and Chat Fallback."""

from __future__ import annotations

import abc
import json
import math
import re
from typing import Any, Mapping

import httpx

from utils.logger_config import get_module_logger

from .schema import (
    BoolQuestion,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionProtocolError,
    DecisionQuestion,
    DecisionResponse,
    ScoreQuestion,
    resolve_decision_endpoint,
)

logger = get_module_logger(__name__)


def _clamp_prob(val: Any, default: float | None = None) -> float | None:
    if val is None or isinstance(val, bool):
        return default
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return max(0.0, min(1.0, f))


def _extract_numeric_score(val: Any, lo: float, hi: float) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return max(lo, min(hi, f))


def _expected_score_from_probs(
    levels: tuple[str, ...],
    probs: Mapping[str, float],
    max_idx: float,
) -> float | None:
    """Compute normalized expected 0-based score from positive level weights, or None."""
    weight_sum = 0.0
    weighted_total = 0.0
    for idx, lbl in enumerate(levels):
        w = probs.get(lbl)
        if w is None or w <= 0.0:
            continue
        weight_sum += w
        weighted_total += idx * w
    if weight_sum <= 0.0:
        return None
    return _extract_numeric_score(weighted_total / weight_sum, 0.0, max_idx)


def _normalize_probs_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        p = _clamp_prob(v)
        if p is not None:
            out[str(k)] = p
    return out


def _truncate_state_for_decision(state: Any) -> Any:
    """Apply DECISION_STATE_MAX_TOKENS budget to string states."""
    if not isinstance(state, str):
        return state
    from config import DECISION_STATE_MAX_TOKENS
    from utils.tokenize import truncate_head_tail_tokens

    half = max(1, DECISION_STATE_MAX_TOKENS // 2)
    return truncate_head_tail_tokens(state, half, half)


def _http_error_is_permanent(status_code: int) -> bool:
    """HTTP statuses indicating the endpoint/model itself is unsupported or retired."""
    return status_code in (400, 404, 405, 422)


def _extract_error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or "")[:200]
            if err:
                return str(err)[:200]
            if data.get("message"):
                return str(data["message"])[:200]
    except Exception as exc:
        # Non-JSON error bodies are common on gateway/proxy failures; fall back to raw text.
        logger.debug("[DecisionAdapter] non-JSON error body: %s", exc)
    return (resp.text or "")[:200]


class DecisionAdapter(abc.ABC):
    """Base interface shared by all native and fallback decision adapters."""

    protocol: str = ""

    @abc.abstractmethod
    async def execute(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        base_url: str,
        api_key: str,
        decision_url: str = "",
        provider_type: str | None = None,
        timeout: float = 8.0,
    ) -> DecisionResponse:
        """Execute a decision batch and return a normalized :class:`DecisionResponse`."""


class SystemOneAdapter(DecisionAdapter):
    """TypeSafe System One protocol (DashScope ``decision-model-preview`` & OpenRouter Decisions)."""

    def __init__(self, protocol: str = "systemone") -> None:
        self.protocol = protocol

    def build_payload(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> dict[str, Any]:
        q_map: dict[str, dict[str, Any]] = {}
        for qid, q in questions.items():
            if isinstance(q, BoolQuestion):
                entry: dict[str, Any] = {
                    "type": "noul",
                    "instructions": q.instructions,
                }
                if q.true_description:
                    entry["true_description"] = q.true_description
                if q.false_description:
                    entry["false_description"] = q.false_description
                q_map[qid] = entry
            elif isinstance(q, ChoiceQuestion):
                q_map[qid] = {
                    "type": "choice",
                    "instructions": q.instructions,
                    "criteria": dict(q.criteria),
                }
            elif isinstance(q, ScoreQuestion):
                q_map[qid] = {
                    "type": "score",
                    "instructions": q.instructions,
                    "levels": list(q.levels),
                }
        return {
            "model": model,
            "state": _truncate_state_for_decision(state),
            "questions": q_map,
        }

    def parse_response(
        self,
        data: Mapping[str, Any],
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> DecisionResponse:
        raw_answers = data.get("results")
        if not isinstance(raw_answers, Mapping):
            raw_answers = data.get("answers")
        if not isinstance(raw_answers, Mapping):
            raise DecisionProtocolError(
                f"{self.protocol} response missing 'results'/'answers' object",
                protocol=self.protocol,
            )

        answers: dict[str, DecisionAnswer] = {}
        for qid, q in questions.items():
            item = raw_answers.get(qid)
            if not isinstance(item, Mapping):
                continue
            conf = _clamp_prob(item.get("confidence"))
            probs = _normalize_probs_map(item.get("probabilities"))

            if isinstance(q, BoolQuestion):
                prob = _clamp_prob(item.get("noul"))
                if prob is None:
                    prob = _clamp_prob(item.get("probability"))
                if prob is None and "yes" in probs:
                    prob = probs["yes"]
                if prob is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="bool",
                    value=prob >= 0.5,
                    probability=prob,
                    probabilities=probs or {"yes": prob, "no": round(1.0 - prob, 6)},
                    confidence=conf,
                )
            elif isinstance(q, ChoiceQuestion):
                choice = item.get("choice") or item.get("selected")
                if not isinstance(choice, str) or choice not in q.criteria:
                    if probs:
                        valid = {k: v for k, v in probs.items() if k in q.criteria}
                        choice = max(valid, key=valid.get) if valid else None
                if isinstance(choice, str) and choice in q.criteria:
                    answers[qid] = DecisionAnswer(
                        question_type="choice",
                        value=choice,
                        probability=probs.get(choice),
                        probabilities=probs,
                        confidence=conf,
                    )
            elif isinstance(q, ScoreQuestion):
                max_idx = float(len(q.levels) - 1)
                score_val = _extract_numeric_score(item.get("score"), 0.0, max_idx)
                if score_val is None:
                    score_val = _expected_score_from_probs(q.levels, probs, max_idx)
                if score_val is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="score",
                    value=score_val,
                    probabilities=probs,
                    confidence=conf,
                    score_norm=(score_val / max_idx) if max_idx > 0 else 0.0,
                )

        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cached_tokens = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, Mapping):
            cached_tokens = int(details.get("cached_tokens") or 0)

        return DecisionResponse(
            answers=answers,
            model=str(data.get("model") or model),
            protocol=self.protocol,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    async def execute(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        base_url: str,
        api_key: str,
        decision_url: str = "",
        provider_type: str | None = None,
        timeout: float = 8.0,
    ) -> DecisionResponse:
        endpoint = resolve_decision_endpoint(self.protocol, base_url, decision_url)
        if not endpoint:
            raise DecisionProtocolError(
                f"{self.protocol} endpoint URL is empty",
                protocol=self.protocol,
                permanent_route_error=True,
            )
        payload = self.build_payload(state=state, questions=questions, model=model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise DecisionProtocolError(
                f"{self.protocol} HTTP {resp.status_code}: {_extract_error_detail(resp)}",
                status_code=resp.status_code,
                protocol=self.protocol,
                permanent_route_error=_http_error_is_permanent(resp.status_code),
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise DecisionProtocolError(
                f"{self.protocol} returned invalid JSON: {exc}",
                protocol=self.protocol,
            ) from exc
        if not isinstance(body, Mapping):
            raise DecisionProtocolError(
                f"{self.protocol} returned non-object JSON",
                protocol=self.protocol,
            )
        return self.parse_response(body, questions, model)


class OpenAIDecisionsAdapter(DecisionAdapter):
    """OpenAI Decisions API protocol (``POST /v1/decisions``, ``gpt-6-luna``)."""

    protocol = "openai_decisions"

    def build_payload(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> dict[str, Any]:
        q_list: list[dict[str, Any]] = []
        for qid, q in questions.items():
            if isinstance(q, BoolQuestion):
                instr = q.instructions
                if q.true_description or q.false_description:
                    parts = [instr]
                    if q.true_description:
                        parts.append(f"True: {q.true_description}")
                    if q.false_description:
                        parts.append(f"False: {q.false_description}")
                    instr = "\n".join(parts)
                q_list.append({
                    "name": qid,
                    "type": "predicate",
                    "instructions": instr,
                })
            elif isinstance(q, ChoiceQuestion):
                criteria_desc = "; ".join(
                    f"{k}: {v}" for k, v in q.criteria.items() if v
                )
                instr = (
                    f"{q.instructions}\nOptions: {criteria_desc}"
                    if criteria_desc
                    else q.instructions
                )
                q_list.append({
                    "name": qid,
                    "type": "choice",
                    "choices": list(q.criteria.keys()),
                    "instructions": instr,
                })
            elif isinstance(q, ScoreQuestion):
                q_list.append({
                    "name": qid,
                    "type": "score",
                    "levels": list(q.levels),
                    "instructions": q.instructions,
                })
        return {
            "model": model,
            "input": _truncate_state_for_decision(state),
            "questions": q_list,
        }

    def parse_response(
        self,
        data: Mapping[str, Any],
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> DecisionResponse:
        raw_answers = data.get("answers")
        by_name: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_answers, list):
            for item in raw_answers:
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    by_name[item["name"]] = item
        elif isinstance(raw_answers, Mapping):
            by_name = {str(k): v for k, v in raw_answers.items() if isinstance(v, Mapping)}
        else:
            raise DecisionProtocolError(
                "openai_decisions response missing 'answers'",
                protocol=self.protocol,
            )

        answers: dict[str, DecisionAnswer] = {}
        for qid, q in questions.items():
            item = by_name.get(qid)
            if not item:
                continue
            conf = _clamp_prob(item.get("confidence"))
            probs = _normalize_probs_map(item.get("probabilities"))

            if isinstance(q, BoolQuestion):
                prob = _clamp_prob(item.get("probability"))
                raw_val = item.get("value")
                if prob is None and isinstance(raw_val, bool):
                    prob = 1.0 if raw_val else 0.0
                if prob is None:
                    continue
                val = raw_val if isinstance(raw_val, bool) else (prob >= 0.5)
                answers[qid] = DecisionAnswer(
                    question_type="bool",
                    value=val,
                    probability=prob,
                    probabilities=probs or {"true": prob, "false": round(1.0 - prob, 6)},
                    confidence=conf,
                )
            elif isinstance(q, ChoiceQuestion):
                selected = item.get("selected") or item.get("choice") or item.get("value")
                if not isinstance(selected, str) or selected not in q.criteria:
                    valid = {k: v for k, v in probs.items() if k in q.criteria}
                    selected = max(valid, key=valid.get) if valid else None
                if isinstance(selected, str) and selected in q.criteria:
                    answers[qid] = DecisionAnswer(
                        question_type="choice",
                        value=selected,
                        probability=probs.get(selected),
                        probabilities=probs,
                        confidence=conf,
                    )
            elif isinstance(q, ScoreQuestion):
                max_idx = float(len(q.levels) - 1)
                raw_score = item.get("score")
                if raw_score is None:
                    raw_score = item.get("score_index")
                score_val = _extract_numeric_score(raw_score, 0.0, max_idx)
                if score_val is None:
                    score_val = _expected_score_from_probs(q.levels, probs, max_idx)
                if score_val is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="score",
                    value=score_val,
                    probabilities=probs,
                    confidence=conf,
                    score_norm=(score_val / max_idx) if max_idx > 0 else 0.0,
                )

        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cached_tokens = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, Mapping):
            cached_tokens = int(details.get("cached_tokens") or 0)

        return DecisionResponse(
            answers=answers,
            model=str(data.get("model") or model),
            protocol=self.protocol,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    async def execute(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        base_url: str,
        api_key: str,
        decision_url: str = "",
        provider_type: str | None = None,
        timeout: float = 8.0,
    ) -> DecisionResponse:
        endpoint = resolve_decision_endpoint(self.protocol, base_url, decision_url)
        if not endpoint:
            raise DecisionProtocolError(
                "openai_decisions endpoint URL is empty",
                protocol=self.protocol,
                permanent_route_error=True,
            )
        payload = self.build_payload(state=state, questions=questions, model=model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise DecisionProtocolError(
                f"openai_decisions HTTP {resp.status_code}: {_extract_error_detail(resp)}",
                status_code=resp.status_code,
                protocol=self.protocol,
                permanent_route_error=_http_error_is_permanent(resp.status_code),
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise DecisionProtocolError(
                f"openai_decisions returned invalid JSON: {exc}",
                protocol=self.protocol,
            ) from exc
        if not isinstance(body, Mapping):
            raise DecisionProtocolError(
                "openai_decisions returned non-object JSON",
                protocol=self.protocol,
            )
        return self.parse_response(body, questions, model)


class GeminiDecisionsAdapter(DecisionAdapter):
    """Google Gemini Decisions API protocol (``POST /v1beta/decisions``, ``gemini-decision-1``)."""

    protocol = "gemini_decisions"

    def build_payload(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> dict[str, Any]:
        q_map: dict[str, dict[str, Any]] = {}
        for qid, q in questions.items():
            if isinstance(q, BoolQuestion):
                entry: dict[str, Any] = {
                    "type": "noul",
                    "instructions": q.instructions,
                }
                if q.true_description:
                    entry["true_description"] = q.true_description
                if q.false_description:
                    entry["false_description"] = q.false_description
                q_map[qid] = entry
            elif isinstance(q, ChoiceQuestion):
                q_map[qid] = {
                    "type": "choice",
                    "instructions": q.instructions,
                    "criteria": dict(q.criteria),
                }
            elif isinstance(q, ScoreQuestion):
                # Gemini score legend uses 1-based string keys ("1", "2", ..., "N")
                legend = {str(i + 1): lbl for i, lbl in enumerate(q.levels)}
                q_map[qid] = {
                    "type": "score",
                    "instructions": q.instructions,
                    "legend": legend,
                }
        return {
            "model": model,
            "input": _truncate_state_for_decision(state),
            "questions": q_map,
        }

    def parse_response(
        self,
        data: Mapping[str, Any],
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> DecisionResponse:
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, Mapping):
            raise DecisionProtocolError(
                "gemini_decisions response missing 'answers' object",
                protocol=self.protocol,
            )

        answers: dict[str, DecisionAnswer] = {}
        for qid, q in questions.items():
            item = raw_answers.get(qid)
            if not isinstance(item, Mapping):
                continue
            conf = _clamp_prob(item.get("confidence"))
            probs = _normalize_probs_map(item.get("probabilities"))

            if isinstance(q, BoolQuestion):
                prob = _clamp_prob(item.get("noul"))
                if prob is None:
                    prob = _clamp_prob(item.get("probability"))
                if prob is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="bool",
                    value=prob >= 0.5,
                    probability=prob,
                    probabilities=probs or {"yes": prob, "no": round(1.0 - prob, 6)},
                    confidence=conf,
                )
            elif isinstance(q, ChoiceQuestion):
                choice = item.get("choice")
                if not isinstance(choice, str) or choice not in q.criteria:
                    valid = {k: v for k, v in probs.items() if k in q.criteria}
                    choice = max(valid, key=valid.get) if valid else None
                if isinstance(choice, str) and choice in q.criteria:
                    answers[qid] = DecisionAnswer(
                        question_type="choice",
                        value=choice,
                        probability=probs.get(choice),
                        probabilities=probs,
                        confidence=conf,
                    )
            elif isinstance(q, ScoreQuestion):
                max_idx = float(len(q.levels) - 1)
                # Convert Gemini's 1..N score and "1".."N" prob keys back to 0..(N-1)
                label_probs: dict[str, float] = {}
                for i, lbl in enumerate(q.levels):
                    p = probs.get(str(i + 1))
                    if p is None:
                        p = probs.get(lbl)
                    if p is not None:
                        label_probs[lbl] = p
                raw_one_based = _extract_numeric_score(
                    item.get("score"), 1.0, float(len(q.levels))
                )
                if raw_one_based is not None:
                    score_val: float | None = max(0.0, min(max_idx, raw_one_based - 1.0))
                else:
                    score_val = _expected_score_from_probs(q.levels, label_probs, max_idx)
                if score_val is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="score",
                    value=score_val,
                    probabilities=label_probs or probs,
                    confidence=conf,
                    score_norm=(score_val / max_idx) if max_idx > 0 else 0.0,
                )

        usage = data.get("usage_metadata") if isinstance(data.get("usage_metadata"), Mapping) else {}
        prompt_tokens = int(usage.get("input_token_count") or usage.get("prompt_token_count") or 0)
        completion_tokens = int(usage.get("output_token_count") or 0)
        cached_tokens = int(usage.get("cached_content_token_count") or 0)

        return DecisionResponse(
            answers=answers,
            model=model,
            protocol=self.protocol,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    async def execute(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        base_url: str,
        api_key: str,
        decision_url: str = "",
        provider_type: str | None = None,
        timeout: float = 8.0,
    ) -> DecisionResponse:
        endpoint = resolve_decision_endpoint(self.protocol, base_url, decision_url)
        if not endpoint:
            raise DecisionProtocolError(
                "gemini_decisions endpoint URL is empty",
                protocol=self.protocol,
                permanent_route_error=True,
            )
        payload = self.build_payload(state=state, questions=questions, model=model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise DecisionProtocolError(
                f"gemini_decisions HTTP {resp.status_code}: {_extract_error_detail(resp)}",
                status_code=resp.status_code,
                protocol=self.protocol,
                permanent_route_error=_http_error_is_permanent(resp.status_code),
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise DecisionProtocolError(
                f"gemini_decisions returned invalid JSON: {exc}",
                protocol=self.protocol,
            ) from exc
        if not isinstance(body, Mapping):
            raise DecisionProtocolError(
                "gemini_decisions returned non-object JSON",
                protocol=self.protocol,
            )
        return self.parse_response(body, questions, model)


class ChatFallbackDecisionAdapter(DecisionAdapter):
    """Generative-chat fallback adapter for providers without a native Decisions API."""

    protocol = "chat_fallback"

    @staticmethod
    def _build_messages(
        state: Any,
        questions: Mapping[str, DecisionQuestion],
    ) -> list[dict[str, str]]:
        from config import DECISION_STATE_MAX_TOKENS
        from utils.tokenize import truncate_head_tail_tokens

        if isinstance(state, str):
            state_text = state
        else:
            try:
                state_text = json.dumps(state, ensure_ascii=False)
            except (TypeError, ValueError):
                state_text = str(state)

        half = max(1, DECISION_STATE_MAX_TOKENS // 2)
        bounded_state = truncate_head_tail_tokens(state_text, half, half)

        q_specs: dict[str, dict[str, Any]] = {}
        for qid, q in questions.items():
            if isinstance(q, BoolQuestion):
                spec: dict[str, Any] = {
                    "type": "bool",
                    "instructions": q.instructions,
                    "output_format": '{"value": true|false, "probability": 0.0_to_1.0}',
                }
                if q.true_description:
                    spec["true_description"] = q.true_description
                if q.false_description:
                    spec["false_description"] = q.false_description
                q_specs[qid] = spec
            elif isinstance(q, ChoiceQuestion):
                q_specs[qid] = {
                    "type": "choice",
                    "instructions": q.instructions,
                    "choices": dict(q.criteria),
                    "output_format": '{"choice": "<exact_choice_key>", "confidence": 0.0_to_1.0}',
                }
            elif isinstance(q, ScoreQuestion):
                q_specs[qid] = {
                    "type": "score",
                    "instructions": q.instructions,
                    "levels_0_based": {str(i): lbl for i, lbl in enumerate(q.levels)},
                    "output_format": f'{{"score": 0_to_{len(q.levels) - 1}, "confidence": 0.0_to_1.0}}',
                }

        system_prompt = (
            "You are a strict structured decision engine. Evaluate the input state against every "
            "question independently and return ONLY a single valid JSON object mapping each "
            "question ID to its typed result. No markdown fences, no prose, no extra keys."
        )
        user_prompt = (
            f"STATE:\n{bounded_state}\n\n"
            f"QUESTIONS:\n{json.dumps(q_specs, ensure_ascii=False)}\n\n"
            "Return strict JSON mapping each question ID to its result object:"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _parse_chat_json(
        raw_content: str,
        questions: Mapping[str, DecisionQuestion],
        model: str,
    ) -> DecisionResponse:
        from utils.file_utils import robust_json_loads
        from utils.llm_client.thinking import strip_thinking_segments

        text = strip_thinking_segments(raw_content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\s*\n?", "", text, count=1)
            text = re.sub(r"\n?\s*```\s*$", "", text, count=1).strip()

        parsed = robust_json_loads(text)
        if not isinstance(parsed, Mapping):
            raise DecisionProtocolError(
                "chat_fallback returned non-object JSON",
                protocol="chat_fallback",
            )

        answers: dict[str, DecisionAnswer] = {}
        for qid, q in questions.items():
            item = parsed.get(qid)
            if item is None:
                continue

            if isinstance(q, BoolQuestion):
                if isinstance(item, bool):
                    prob = 1.0 if item else 0.0
                    val = item
                    conf = None
                elif isinstance(item, (int, float)):
                    prob = _clamp_prob(item, 0.5)
                    val = bool(prob >= 0.5)
                    conf = None
                elif isinstance(item, Mapping):
                    raw_val = item.get("value")
                    prob = _clamp_prob(item.get("probability"))
                    if prob is None:
                        prob = _clamp_prob(item.get("prob"))
                    if prob is None and isinstance(raw_val, bool):
                        prob = 1.0 if raw_val else 0.0
                    if prob is None:
                        continue
                    val = raw_val if isinstance(raw_val, bool) else (prob >= 0.5)
                    conf = _clamp_prob(item.get("confidence"))
                else:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="bool",
                    value=val,
                    probability=prob,
                    probabilities={"yes": prob, "no": round(1.0 - prob, 6)},
                    confidence=conf,
                )
            elif isinstance(q, ChoiceQuestion):
                choice: Any = None
                conf = None
                probs: dict[str, float] = {}
                if isinstance(item, str):
                    choice = item
                elif isinstance(item, Mapping):
                    choice = item.get("choice") or item.get("selected") or item.get("value")
                    conf = _clamp_prob(item.get("confidence"))
                    probs = _normalize_probs_map(item.get("probabilities"))
                if isinstance(choice, str) and choice in q.criteria:
                    answers[qid] = DecisionAnswer(
                        question_type="choice",
                        value=choice,
                        probability=probs.get(choice) or conf,
                        probabilities=probs,
                        confidence=conf,
                    )
            elif isinstance(q, ScoreQuestion):
                max_idx = float(len(q.levels) - 1)
                conf = None
                probs = {}
                raw_score: Any = None
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    raw_score = item
                elif isinstance(item, Mapping):
                    raw_score = item.get("score")
                    if raw_score is None:
                        raw_score = item.get("value")
                    conf = _clamp_prob(item.get("confidence"))
                    probs = _normalize_probs_map(item.get("probabilities"))
                score_val = _extract_numeric_score(raw_score, 0.0, max_idx)
                if score_val is None:
                    continue
                answers[qid] = DecisionAnswer(
                    question_type="score",
                    value=score_val,
                    probabilities=probs,
                    confidence=conf,
                    score_norm=(score_val / max_idx) if max_idx > 0 else 0.0,
                )

        return DecisionResponse(
            answers=answers,
            model=model,
            protocol="chat_fallback",
        )

    async def execute(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        base_url: str,
        api_key: str,
        decision_url: str = "",
        provider_type: str | None = None,
        timeout: float = 8.0,
    ) -> DecisionResponse:
        from config import DECISION_CHAT_FALLBACK_MAX_TOKENS
        from utils.llm_client import create_chat_llm_async

        messages = self._build_messages(state, questions)
        llm = await create_chat_llm_async(
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_completion_tokens=DECISION_CHAT_FALLBACK_MAX_TOKENS,
            timeout=timeout,
            max_retries=0,
            provider_type=provider_type,
        )
        try:
            resp = await llm.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET  # state is truncated to DECISION_STATE_MAX_TOKENS inside _build_messages.
        finally:
            await llm.aclose()
        return self._parse_chat_json(getattr(resp, "content", "") or "", questions, model)


_ADAPTERS: dict[str, DecisionAdapter] = {
    "systemone": SystemOneAdapter("systemone"),
    "openrouter_decisions": SystemOneAdapter("openrouter_decisions"),
    "openai_decisions": OpenAIDecisionsAdapter(),
    "gemini_decisions": GeminiDecisionsAdapter(),
    "chat_fallback": ChatFallbackDecisionAdapter(),
}


def get_decision_adapter(protocol: str | None) -> DecisionAdapter:
    """Return the registered adapter for *protocol*, defaulting to ``chat_fallback``."""
    key = str(protocol or "chat_fallback").strip().lower()
    return _ADAPTERS.get(key, _ADAPTERS["chat_fallback"])
