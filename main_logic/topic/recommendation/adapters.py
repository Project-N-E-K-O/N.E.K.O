"""Thin chat, read-only memory and proactive prompt/choice adapters."""
from __future__ import annotations

import json
import asyncio
import re
from urllib.parse import quote

from config.prompts.prompts_topic_recommendation import delivery_prompt, restriction_prompt
from main_logic.topic.recommendation.analysis import redact_credentials
from main_logic.topic.recommendation.contracts import RecommendationError, RecommendationSnapshot
from utils.tokenize import acount_tokens, atruncate_to_tokens


class RecommendationBudgetedClient:
    """Guard actual Phase 2 text requests, including fixes and regenerations.

    Images retain the existing source's vision contract. Their base64 transport
    is not text tokens; every text part and message wrapper is counted intact.
    Never truncate a refusal or a candidate to squeeze it into the request.
    """
    def __init__(self, client, max_text_tokens: int):
        self._owner = client
        self._client = None
        self._max_text_tokens = max_text_tokens

    async def __aenter__(self):
        self._client = await self._owner.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._owner.__aexit__(*args)

    async def _guard_input(self, messages) -> None:
        projected = []
        for message in messages:
            content = message.content
            if isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        raise RecommendationError("invalid_model_input")
                    if part.get("type") == "text" and isinstance(part.get("text"), str):
                        parts.append(part)
                    elif part.get("type") == "image_url":
                        parts.append({"type": "image_url"})
                    else:
                        raise RecommendationError("invalid_model_input")
                content = parts
            elif not isinstance(content, str):
                raise RecommendationError("invalid_model_input")
            projected.append({"role": message.role, "content": content})
        wire = json.dumps(projected, ensure_ascii=False, allow_nan=False)
        async with asyncio.timeout(5):
            if await acount_tokens(wire) > self._max_text_tokens:
                raise RecommendationError("input_budget_exceeded")

    async def astream(self, messages, **kwargs):
        await self._guard_input(messages)
        async for chunk in self._client.astream(messages, **kwargs):  # noqa: LLM_INPUT_BUDGET # _guard_input rejects oversized actual text requests
            yield chunk

    async def ainvoke(self, messages, **kwargs):
        await self._guard_input(messages)
        return await self._client.ainvoke(messages, **kwargs)  # noqa: LLM_INPUT_BUDGET # _guard_input rejects oversized actual text requests


class RecommendationTurnSink:
    def __init__(self, service, character_id: str, session_id: str, binding_generation: str) -> None:
        self.service = service
        self.character_id = character_id
        self.session_id = session_id
        self.binding_generation = binding_generation

    def note_turn(self, event) -> None:
        self.service.note_turn(self.character_id, self.session_id, self.binding_generation, event)


class ReadOnlyMemoryAdapter:
    def __init__(self, client_provider=None) -> None:
        self._client_provider = client_provider

    async def read(self, *, display_name: str, subjects: tuple[dict, ...] | None, query: str, language: str,
                   allow_private: bool = False) -> tuple[dict, ...]:
        # Explicit empty scope cannot become omitted/private fallback.
        if subjects is None and not allow_private:
            raise RecommendationError("invalid_memory_scope")
        if subjects is not None:
            if not subjects or len(subjects) > 8:
                raise RecommendationError("invalid_memory_scope")
            for subject in subjects:
                if not isinstance(subject, dict) or set(subject) - {"subject_kind", "subject_id", "scope"} or subject.get("subject_kind") not in {"group_chat", "participant", "group_participant"} or not isinstance(subject.get("subject_id"), str) or not subject["subject_id"]:
                    raise RecommendationError("invalid_memory_scope")
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client
        client = self._client_provider() if self._client_provider else get_internal_http_client()
        bounded = await atruncate_to_tokens(redact_credentials(query), 200)
        body = {"query": bounded, "language": language}
        if subjects is not None:
            body["subjects"] = list(subjects)
        response = await client.post(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/query_memory/{quote(display_name, safe='')}",
                                     json=body, timeout=10.0)
        if response.status_code != 200:
            raise RecommendationError("memory_read_failed")
        value = response.json()
        if not isinstance(value, dict) or value.get("error_code") or not isinstance(value.get("results"), list):
            raise RecommendationError("memory_read_failed")
        output = []
        for index, item in enumerate(value["results"][:6]):
            if not isinstance(item, dict):
                raise RecommendationError("memory_read_failed")
            text = item.get("text") or item.get("content") or item.get("summary")
            if not isinstance(text, str):
                continue
            output.append({"ref": str(item.get("id") or item.get("fact_id") or item.get("reflection_id") or f"unidentified:{index}")[:128],
                           "text": await atruncate_to_tokens(redact_credentials(text), 200),
                           "event_time": item.get("event_time"), "source": "memory_context", "reliable_time": False})
        return tuple(output)


def build_candidate_prompt(snapshot: RecommendationSnapshot | None, language: str = "en", *, restrictions=()) -> str:
    candidates = snapshot.candidates if snapshot else ()
    limits = snapshot.restrictions if snapshot else restrictions
    if not candidates and not limits:
        return ""
    data = {"candidates": [{"choice": f"R{index + 1}", "subject_id": candidate["subject_id"],
                             "summary": candidate["summary"], "angle": candidate["angle"],
                             "basis": candidate["basis"], "evidence_refs": candidate["evidence_refs"]}
                            for index, candidate in enumerate(candidates)],
            "restrictions": [{k: restriction.get(k) for k in ("subject_id", "scope", "summary", "angle")}
                             for restriction in limits]}
    instruction = delivery_prompt(language)
    if snapshot is None:
        instruction = restriction_prompt(language)
    else:
        instruction += "\nPlace the single choice marker immediately after the existing source tag, e.g. [CHAT][REC:R1]. Other sources use their existing source tag followed by [REC:NONE]."
    return "\n" + instruction + "\n" + json.dumps(data, ensure_ascii=False)


_CHOICE = re.compile(r"\[REC:([^\]\r\n]+)\]", re.IGNORECASE)


def parse_recommendation_choice(text: str, snapshot: RecommendationSnapshot | None) -> tuple[str, str | None, bool]:
    marks = _CHOICE.findall(text)
    clean = _CHOICE.sub("", text).strip()
    if len(marks) != 1:
        return clean, None, False
    marker = _CHOICE.search(text)
    prefix = text[:marker.start()].strip()
    if prefix and not re.fullmatch(r"\[[A-Z_]+\]", prefix):
        return clean, None, False
    choice = marks[0].upper()
    if choice == "NONE":
        return clean, None, True
    if snapshot is None or not re.fullmatch(r"R[1-3]", choice):
        return clean, None, False
    index = int(choice[1:]) - 1
    if index >= len(snapshot.candidates):
        return clean, None, False
    return clean, snapshot.candidates[index]["subject_id"], True
