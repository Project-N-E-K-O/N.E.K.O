# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Pure helpers for new-user icebreaker free-text interpretation."""
from __future__ import annotations

import json
import re
from typing import Any, Dict

from config import (
    ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS,
    ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS,
)
from config.prompts.prompts_icebreaker import ICEBREAKER_FREE_TEXT_WATERMARK
from utils.tokenize import truncate_to_tokens


ICEBREAKER_FREE_TEXT_ACTIONS = {
    "choose",
    "respond_and_keep_options",
    "release",
}
ICEBREAKER_FREE_TEXT_TOPIC_STATES = {
    "on_topic",
    "soft_derail",
    "hard_exit",
}
_JSON_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def trim_icebreaker_token_text(raw: Any, max_tokens: int) -> str:
    return truncate_to_tokens(str(raw or "").strip(), max_tokens).strip()


def strip_json_fence(raw: str) -> str:
    return _JSON_FENCE_PATTERN.sub("", str(raw or "").strip()).strip()


def extract_first_json_object(raw: str) -> str | None:
    text = strip_json_fence(raw)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def clean_icebreaker_interpreter_reply(raw: Any) -> str:
    reply = strip_json_fence(str(raw or ""))
    reply = reply.replace(ICEBREAKER_FREE_TEXT_WATERMARK, "")
    reply = re.sub(r"======以上为[^=\n]{0,80}======", "", reply)
    reply = re.sub(r"\s+", " ", reply).strip()
    return trim_icebreaker_token_text(reply, ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS)


def parse_icebreaker_free_text_decision(raw: Any) -> Dict[str, str]:
    text = strip_json_fence(str(raw or ""))
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(text)
    except Exception:
        extracted = extract_first_json_object(text)
        if extracted:
            try:
                payload = json.loads(extracted)
            except Exception:
                payload = {}
    if not isinstance(payload, dict):
        payload = {}

    action = str(payload.get("action") or "").strip()
    choice = str(payload.get("choice") or "").strip().upper()
    reply = clean_icebreaker_interpreter_reply(payload.get("reply"))
    topic_state = str(payload.get("topic_state") or "").strip()
    if action not in ICEBREAKER_FREE_TEXT_ACTIONS:
        action = "respond_and_keep_options"
    if action == "choose" and choice not in {"A", "B"}:
        action = "respond_and_keep_options"
        choice = ""
    if action == "choose":
        reply = ""
    else:
        choice = ""
    if topic_state not in ICEBREAKER_FREE_TEXT_TOPIC_STATES:
        if action == "choose":
            topic_state = "on_topic"
        elif action == "release":
            topic_state = "hard_exit"
        else:
            topic_state = "on_topic"
    return {"action": action, "choice": choice, "reply": reply, "topic_state": topic_state}


def normalize_icebreaker_free_text_options(raw: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return options
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        choice = str(item.get("choice") or item.get("id") or "").strip().upper()
        label = trim_icebreaker_token_text(item.get("label"), ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS)
        if choice in {"A", "B"} and label:
            options.append({"choice": choice, "label": label})
    return options


def trim_icebreaker_history_text(raw: Any) -> str:
    return trim_icebreaker_token_text(raw, ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS)


def normalize_icebreaker_free_text_derail_streak(raw: Any) -> int:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return 1 if value > 0 else 0


def normalize_icebreaker_recent_free_text_turns(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    turns: list[dict[str, str]] = []
    for item in raw[-ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS:]:
        if not isinstance(item, dict):
            continue
        user_text = trim_icebreaker_history_text(item.get("user_text") or item.get("userText"))
        if not user_text:
            continue
        action = str(item.get("action") or "").strip()
        if action not in ICEBREAKER_FREE_TEXT_ACTIONS:
            action = "respond_and_keep_options"
        turn: dict[str, str] = {
            "user_text": user_text,
            "action": action,
        }
        choice = str(item.get("choice") or "").strip().upper()
        if action == "choose" and choice in {"A", "B"}:
            turn["choice"] = choice
        topic_state = str(item.get("topic_state") or item.get("topicState") or "").strip()
        if topic_state in ICEBREAKER_FREE_TEXT_TOPIC_STATES:
            turn["topic_state"] = topic_state
        reply = trim_icebreaker_history_text(item.get("reply"))
        if reply:
            turn["reply"] = reply
        turns.append(turn)
    return turns
