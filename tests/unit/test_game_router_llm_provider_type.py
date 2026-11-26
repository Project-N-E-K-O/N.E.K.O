"""Minigame LLM clients must be built with the slot's provider type.

The factory picks the wire protocol from provider_type: without it a ChatGPT
subscription slot (OAuth, Responses API) is called as an OpenAI-compatible
endpoint with no key, and every minigame line falls back to canned text.
"""

import ast
from pathlib import Path

FACTORIES = {"create_chat_llm", "create_chat_llm_async"}
GAME_ROUTER = Path(__file__).resolve().parents[2] / "main_routers" / "game_router"


def _factory_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in FACTORIES:
                yield node


def test_every_minigame_llm_factory_call_passes_provider_type():
    missing = []
    calls = 0
    for path in sorted(GAME_ROUTER.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _factory_calls(tree):
            calls += 1
            keywords = {kw.arg for kw in call.keywords}
            # **kwargs may carry it; an explicit keyword is required otherwise.
            if "provider_type" not in keywords and None not in keywords:
                missing.append(f"{path.relative_to(GAME_ROUTER.parents[1])}:{call.lineno}")
    assert calls, "no LLM factory calls found; the scan no longer matches the code"
    assert not missing, "LLM factory calls without provider_type: " + ", ".join(missing)
