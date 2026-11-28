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
import asyncio
import json
import math
import re
import urllib.parse
import weakref
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


def _http_error_is_permanent(status_code: int, protocol: str = "") -> bool:
    """HTTP statuses that will not change until the route, model or API key changes.

    Covers unsupported/retired endpoints (400/404/405/422). For the Bailian
    ``systemone`` route, 401/403 also mean rejected credentials or a model not
    enabled for the workspace; the cooldown key includes the API key
    fingerprint, so a new key retries at once. Other gateways (e.g. OpenRouter)
    also answer 403 for a single moderated input, which must not pause every
    later decision, so there 401/403 stay non-permanent.
    """
    if status_code in (400, 404, 405, 422):
        return True
    return protocol == "systemone" and status_code in (401, 403)


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


# One pooled client per event loop: a new client per request paid a fresh TLS
# handshake every time (about +60 ms to Bailian and +1 s to OpenAI, measured
# 2026-11-25). A client cannot be shared across event loops, and the servers
# that call decisions each run their own loop.
_http_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)

# How long an idle pooled connection stays open. httpx closes idle connections
# after 5 s by default, while decisions usually come more than 5 s apart (one per
# turn), so every call paid the handshake again: 0.6-2 s cold against 0.1-0.55 s
# warm across six vendors (measured 2026-11-27). A connection the server closed
# meanwhile is noticed before reuse and replaced.
DECISION_KEEPALIVE_SECONDS = 120.0


def _decision_http_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _http_clients.get(loop)
    if client is None or getattr(client, "is_closed", False):
        # httpx's default pool sizes, with the longer idle time.
        client = httpx.AsyncClient(limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=DECISION_KEEPALIVE_SECONDS,
        ))
        _http_clients[loop] = client
    return client


# Request headers a vendor's decision endpoint needs besides the key, by host.
# Volcengine Ark serves System One only to requests that opt in to the beta;
# without the header it answers an empty 404 (observed 2026-11-26).
_HOST_DECISION_HEADERS: dict[str, dict[str, str]] = {
    "ark.cn-beijing.volces.com": {"ark-beta-systemone-api": "true"},
}


def _host_decision_headers(endpoint: str) -> dict[str, str]:
    host = (urllib.parse.urlsplit(str(endpoint or "")).hostname or "").lower()
    return dict(_HOST_DECISION_HEADERS.get(host, {}))


async def _post_decision_request(
    endpoint: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: float,
) -> httpx.Response:
    return await _decision_http_client().post(endpoint, json=payload, headers=dict(headers), timeout=timeout)


def _decode_json_object(resp: httpx.Response, protocol: str) -> Mapping[str, Any]:
    try:
        body = resp.json()
    except Exception as exc:
        raise DecisionProtocolError(
            f"{protocol} returned invalid JSON: {exc}",
            protocol=protocol,
        ) from exc
    if not isinstance(body, Mapping):
        raise DecisionProtocolError(
            f"{protocol} returned non-object JSON",
            protocol=protocol,
        )
    return body


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


def _score_probs_by_label(levels: tuple[str, ...], probs: Mapping[str, float]) -> dict[str, float]:
    """Map score probabilities keyed by 0-based level index strings ("0", "1", ...) to level labels.

    The key encoding is decided from the whole map, not per entry, so labels that
    look like digits (levels ``("1", "2")``) are not misread as indexes. The wire
    format documents index keys, which win when the map fits both encodings.
    """
    keys = set(probs)
    index_keys = {str(idx) for idx in range(len(levels))}
    if keys and not keys <= index_keys and keys <= set(levels):
        return {lbl: probs[lbl] for lbl in levels if lbl in probs}
    out: dict[str, float] = {}
    for idx, lbl in enumerate(levels):
        p = probs.get(str(idx))
        if p is not None:
            out[lbl] = p
    return out


def _bool_instructions_with_descriptions(q: BoolQuestion) -> str:
    """Fold a yes/no question's true/false descriptions into its instructions.

    For wire formats without a field for them (OpenAI ``predicate``; Gemini
    rejects every key in ``noul`` criteria).
    """
    if not (q.true_description or q.false_description):
        return q.instructions
    parts = [q.instructions]
    if q.true_description:
        parts.append(f"True: {q.true_description}")
    if q.false_description:
        parts.append(f"False: {q.false_description}")
    return "\n".join(parts)


def _usage_int(usage: Mapping[str, Any], *names: str) -> int:
    """First positive integer among *names* in *usage*, else 0."""
    for name in names:
        val = usage.get(name)
        if not val or isinstance(val, bool):
            continue
        try:
            return int(val)
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


class SystemOneAdapter(DecisionAdapter):
    """TypeSafe System One protocol (DashScope ``decision-model`` & OpenRouter Decisions).

    Wire format (Bailian decision model API reference): every question carries
    ``type`` / ``instructions`` / ``criteria``. ``score`` criteria is the
    low-to-high level description array, ``noul`` criteria is the optional
    ``{"true": ..., "false": ...}`` object and ``choice`` criteria maps option
    to description. One request accepts at most
    :attr:`MAX_QUESTIONS_PER_REQUEST` questions; larger batches are split into
    concurrent requests that share the same state.

    Close variants (Gemini) subclass this and override the ``_``-prefixed hooks.
    """

    MAX_QUESTIONS_PER_REQUEST = 16
    STATE_FIELD = "state"

    def __init__(self, protocol: str = "systemone") -> None:
        self.protocol = protocol

    def _bool_entry(self, q: BoolQuestion) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "noul", "instructions": q.instructions}
        noul_criteria: dict[str, str] = {}
        if q.true_description:
            noul_criteria["true"] = q.true_description
        if q.false_description:
            noul_criteria["false"] = q.false_description
        if noul_criteria:
            entry["criteria"] = noul_criteria
        return entry

    def _encode_state(self, state: Any) -> Any:
        return _truncate_state_for_decision(state)

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _parse_usage(self, data: Mapping[str, Any]) -> tuple[int, int, int]:
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        prompt_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
        cached_tokens = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, Mapping):
            cached_tokens = _usage_int(details, "cached_tokens")
        return prompt_tokens, completion_tokens, cached_tokens

    def _error_code(self, status_code: int, detail: str) -> str:
        # Bailian answers 403 "Workspace endpoint access denied." when the API key
        # belongs to another workspace than the one in the API Host.
        if self.protocol == "systemone" and status_code == 403 and "workspace endpoint" in detail.lower():
            return "workspace_mismatch"
        return ""

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
                q_map[qid] = self._bool_entry(q)
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
                    "criteria": list(q.levels),
                }
        return {
            "model": model,
            self.STATE_FIELD: self._encode_state(state),
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
                # ``noul`` is P(yes) in [0, 1]; the reference documents no
                # probabilities map for noul, so one is only a last resort.
                prob = _clamp_prob(item.get("noul"))
                if prob is None:
                    prob = _clamp_prob(item.get("probability"))
                if prob is None:
                    prob = probs.get("true", probs.get("yes"))
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
                # ``score`` is the probability-weighted 0-based level index (may be
                # fractional); ``probabilities`` is keyed by index strings "0".."N-1".
                max_idx = float(len(q.levels) - 1)
                label_probs = _score_probs_by_label(q.levels, probs)
                score_val = _extract_numeric_score(item.get("score"), 0.0, max_idx)
                if score_val is None:
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

        prompt_tokens, completion_tokens, cached_tokens = self._parse_usage(data)
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
        limit = self.MAX_QUESTIONS_PER_REQUEST
        if len(questions) <= limit:
            return await self._execute_one(
                endpoint=endpoint, state=state, questions=questions, model=model, api_key=api_key, timeout=timeout,
            )
        # Over the per-request cap: split into concurrent requests sharing the
        # state. Callers that can shrink the state per chunk (memory recall)
        # batch themselves; this keeps every other caller within the limit.
        items = list(questions.items())
        chunks = [dict(items[i:i + limit]) for i in range(0, len(items), limit)]
        parts = await asyncio.gather(*(
            self._execute_one(
                endpoint=endpoint, state=state, questions=chunk, model=model, api_key=api_key, timeout=timeout,
            )
            for chunk in chunks
        ))
        merged: dict[str, DecisionAnswer] = {}
        for part in parts:
            merged.update(part.answers)
        return DecisionResponse(
            answers=merged,
            model=parts[0].model,
            protocol=self.protocol,
            prompt_tokens=sum(p.prompt_tokens for p in parts),
            completion_tokens=sum(p.completion_tokens for p in parts),
            cached_tokens=sum(p.cached_tokens for p in parts),
        )

    async def _execute_one(
        self,
        *,
        endpoint: str,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str,
        api_key: str,
        timeout: float,
    ) -> DecisionResponse:
        payload = self.build_payload(state=state, questions=questions, model=model)
        headers = {
            "Content-Type": "application/json",
            **_host_decision_headers(endpoint),
            **self._auth_headers(api_key),
        }
        resp = await _post_decision_request(endpoint, payload, headers, timeout)
        if resp.status_code >= 400:
            detail = _extract_error_detail(resp)
            raise DecisionProtocolError(
                f"{self.protocol} HTTP {resp.status_code}: {detail}",
                status_code=resp.status_code,
                protocol=self.protocol,
                permanent_route_error=_http_error_is_permanent(resp.status_code, self.protocol),
                error_code=self._error_code(resp.status_code, detail),
            )
        return self.parse_response(_decode_json_object(resp, self.protocol), questions, model)


class GeminiDecisionsAdapter(SystemOneAdapter):
    """Google Gemini Decisions API (``POST /v1beta/decisions``, ``gemini-decision-1``).

    Same question and answer shapes as System One (0-based ``score`` over
    ``criteria`` levels; answers keyed by question ID), with these differences
    observed on 2026-11-25: the state goes in ``input`` and must be a string
    or a parts list, not an object; ``noul`` criteria rejects every key, so
    true/false descriptions are folded into the instructions; the key is sent
    as ``x-goog-api-key``; usage reports ``total_input_tokens``.
    """

    # 64 questions in one request were accepted on 2026-11-25; no limit is documented.
    MAX_QUESTIONS_PER_REQUEST = 64
    STATE_FIELD = "input"

    def __init__(self) -> None:
        super().__init__("gemini_decisions")

    def _bool_entry(self, q: BoolQuestion) -> dict[str, Any]:
        return {"type": "noul", "instructions": _bool_instructions_with_descriptions(q)}

    def _encode_state(self, state: Any) -> Any:
        if not isinstance(state, str):
            try:
                state = json.dumps(state, ensure_ascii=False)
            except (TypeError, ValueError):
                state = str(state)
        return _truncate_state_for_decision(state)

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key} if api_key else {}

    def _parse_usage(self, data: Mapping[str, Any]) -> tuple[int, int, int]:
        usage: Any = None
        for name in ("usage", "usage_metadata", "usageMetadata"):
            usage = data.get(name)
            if isinstance(usage, Mapping):
                break
        if not isinstance(usage, Mapping):
            return 0, 0, 0
        prompt_tokens = _usage_int(
            usage, "total_input_tokens", "input_token_count", "prompt_token_count",
            "promptTokenCount", "inputTokenCount",
        )
        completion_tokens = _usage_int(
            usage, "total_output_tokens", "output_token_count", "candidatesTokenCount", "outputTokenCount",
        )
        cached_tokens = _usage_int(usage, "cached_content_token_count", "cachedContentTokenCount")
        return prompt_tokens, completion_tokens, cached_tokens


def _probabilities_from_list(raw: Any, key_for: Any) -> dict[str, float]:
    """Normalize an OpenAI ``probabilities`` list (``[{value, probability}, ...]``) or map.

    *key_for* maps one list entry to its result key (``None`` skips it).
    """
    if isinstance(raw, Mapping):
        return _normalize_probs_map(raw)
    out: dict[str, float] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        key = key_for(entry)
        p = _clamp_prob(entry.get("probability"))
        if key is not None and p is not None:
            out[str(key)] = p
    return out


def _choice_value_key(entry: Mapping[str, Any]) -> Any:
    return entry.get("value")


def _score_level_key(levels: tuple[str, ...]) -> Any:
    """Key OpenAI score probabilities by level label (entries carry ``label`` and 0-based ``value``)."""

    def _key(entry: Mapping[str, Any]) -> str | None:
        label = entry.get("label")
        if isinstance(label, str) and label in levels:
            return label
        idx = entry.get("value")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(levels):
            return levels[idx]
        return None

    return _key


class OpenAIDecisionsAdapter(DecisionAdapter):
    """OpenAI Decisions API protocol (``POST /v1/decisions``, ``gpt-6-luna``).

    Wire format observed on 2026-11-25: ``input`` plus a ``questions`` list of
    ``{name, type, instructions}`` where type is ``predicate`` / ``choice`` /
    ``score``. ``choices`` items are ``{value, description}`` objects and
    ``levels`` items are ``{label}`` objects; predicates have no field for
    true/false descriptions, so those are folded into the instructions.
    Answers come back as a list keyed by ``name``; ``probabilities`` is a list
    of ``{value, (label), probability}`` objects and ``score`` is 0-based.
    """

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
                q_list.append({
                    "name": qid,
                    "type": "predicate",
                    "instructions": _bool_instructions_with_descriptions(q),
                })
            elif isinstance(q, ChoiceQuestion):
                choices: list[dict[str, str]] = []
                for key, desc in q.criteria.items():
                    option: dict[str, str] = {"value": key}
                    if desc:
                        option["description"] = desc
                    choices.append(option)
                q_list.append({
                    "name": qid,
                    "type": "choice",
                    "choices": choices,
                    "instructions": q.instructions,
                })
            elif isinstance(q, ScoreQuestion):
                q_list.append({
                    "name": qid,
                    "type": "score",
                    "levels": [{"label": lbl} for lbl in q.levels],
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

            if isinstance(q, BoolQuestion):
                probs = _probabilities_from_list(item.get("probabilities"), _choice_value_key)
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
                probs = _probabilities_from_list(item.get("probabilities"), _choice_value_key)
                selected = item.get("choice") or item.get("selected") or item.get("value")
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
                probs = _probabilities_from_list(item.get("probabilities"), _score_level_key(q.levels))
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
        prompt_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
        cached_tokens = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, Mapping):
            cached_tokens = _usage_int(details, "cached_tokens")

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

        resp = await _post_decision_request(endpoint, payload, headers, timeout)
        if resp.status_code >= 400:
            raise DecisionProtocolError(
                f"openai_decisions HTTP {resp.status_code}: {_extract_error_detail(resp)}",
                status_code=resp.status_code,
                protocol=self.protocol,
                permanent_route_error=_http_error_is_permanent(resp.status_code),
            )
        return self.parse_response(_decode_json_object(resp, self.protocol), questions, model)


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
                        probability=probs[choice] if choice in probs else conf,
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
