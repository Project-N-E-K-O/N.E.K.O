# Ported from claudian/src/core/tools/toolInput.ts
# Original author: Claudian contributors
# License: MIT

"""
Tool input parsing utilities.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def extract_resolved_answers(tool_use_result: Any) -> Optional[Dict[str, Union[str, List[str]]]]:
    """Extract resolved answers from AskUserQuestion tool result.

    Ported from toolInput.ts extractResolvedAnswers.
    """
    if not isinstance(tool_use_result, dict):
        return None

    answers = tool_use_result.get("answers")
    if isinstance(answers, dict):
        return answers

    return None


def extract_resolved_answers_from_result_text(
    result_text: str,
) -> Optional[Dict[str, Union[str, List[str]]]]:
    """Extract resolved answers from result text.

    Ported from toolInput.ts extractResolvedAnswersFromResultText.
    """
    if not result_text:
        return None

    # Try to parse as JSON
    try:
        import json
        data = json.loads(result_text)
        if isinstance(data, dict) and "answers" in data:
            return data["answers"]
    except (json.JSONDecodeError, TypeError):
        pass

    return None
