"""Every theater and workshop model request carries the N.E.K.O prompt watermark.

The free text route refuses requests without a ``======以上为`` line, so a
theater stage that builds its own system message without the mark fails for
every user on that route.
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from config.prompts.prompts_theater import (
    THEATER_INVITE_SYSTEM_PROMPT_WATERMARK,
    THEATER_SYSTEM_PROMPT_WATERMARK,
    THEATER_WORKSHOP_SYSTEM_PROMPT_WATERMARK,
)
from services.theater.numeric_v2_prompt_mark import (
    mark_theater_system_prompt,
    theater_system_message,
    unmark_theater_system_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMISSION = "======以上为"


def test_watermarks_carry_the_admission_line():
    for mark in (THEATER_SYSTEM_PROMPT_WATERMARK, THEATER_WORKSHOP_SYSTEM_PROMPT_WATERMARK,
                 THEATER_INVITE_SYSTEM_PROMPT_WATERMARK):
        assert ADMISSION in mark


def test_mark_is_single_and_trailing_when_a_prompt_is_extended():
    first = theater_system_message("只核对正文。").content
    extended = mark_theater_system_prompt(first + "\n另填写 fact_discoveries。")
    assert extended.endswith(THEATER_SYSTEM_PROMPT_WATERMARK)
    assert extended.count(THEATER_SYSTEM_PROMPT_WATERMARK) == 1
    assert unmark_theater_system_prompt(extended) == "只核对正文。\n另填写 fact_discoveries。"


def test_theater_runtime_builds_system_messages_only_through_the_marking_helper():
    offenders = []
    for path in sorted((REPO_ROOT / "services" / "theater").glob("*.py")):
        if path.name == "numeric_v2_prompt_mark.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name == "SystemMessage":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "build theater system messages with theater_system_message()"


def test_workshop_host_marks_the_system_message_before_counting_it(monkeypatch):
    from services.theater_workshop.host import NekoWorkshopModel, _with_workshop_watermark
    from utils import llm_client
    from utils.tokenize import count_tokens

    messages = [{"role": "system", "content": "你是互动剧本策划。"}, {"role": "user", "content": "{}"}]
    original = deepcopy(messages)
    sent = _with_workshop_watermark(messages)
    calls = []

    class Client:
        def invoke(self, actual, **options):
            calls.append(actual)
            return SimpleNamespace(content="{}", response_metadata={})

        def close(self):
            pass

    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **options: Client())
    model = NekoWorkshopModel({"model": "selected-model", "base_url": "https://example.invalid/v1",
                               "max_input_tokens": count_tokens(json.dumps(sent, ensure_ascii=False))})
    model(messages, max_tokens=100, max_retries=1, response_format={"type": "json_object"},
          thinking=None, operation="numeric_v2_story_frame")

    assert calls[0][0]["content"] == "你是互动剧本策划。" + THEATER_WORKSHOP_SYSTEM_PROMPT_WATERMARK
    assert calls[0][1:] == original[1:]
    assert messages == original
    assert _with_workshop_watermark(sent) == sent
