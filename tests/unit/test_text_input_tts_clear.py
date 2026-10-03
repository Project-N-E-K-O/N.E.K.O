"""The text-input path must fully clear TTS before rotating to a new user sid."""

from __future__ import annotations

import ast
from pathlib import Path


def _text_input_clear_calls() -> list[ast.Call]:
    source = (
        Path(__file__).resolve().parents[2] / "main_logic" / "core" / "streaming.py"
    ).read_text(encoding="utf-8")
    calls = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_clear_tts_pipeline"
        ):
            calls.append(node)
    return calls


def test_text_input_clear_does_not_keep_a_rotated_turns_chunks():
    """handle_interruption() is awaited between capturing the sid and clearing.

    A proactive delivery can rotate the sid in that window; the selective
    ``expected_speech_id`` form would then keep that turn's pending chunks and
    replay them over the user's reply once the worker reports ready.
    """
    calls = _text_input_clear_calls()
    assert calls, "streaming.py no longer clears the TTS pipeline"
    for call in calls:
        assert not any(kw.arg == "expected_speech_id" for kw in call.keywords), (
            f"_clear_tts_pipeline at line {call.lineno} keeps chunks of a rotated sid"
        )
