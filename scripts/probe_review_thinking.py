#!/usr/bin/env python3
"""Compare correction-model thinking depth on one synthetic history review.

Three calls, same dialogue, same output cap as the real review:
  baseline       — review prompt without its reasoning-limit paragraph,
                   no extra_body, native thinking
  prompt_shallow — the shipped review prompt, reasoning limit included
  budget_256     — baseline prompt with DashScope thinking_budget=256

Prints timing and token counts only. Does not print the API key or the reply.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from openai import OpenAI  # noqa: E402

from config import MEMORY_REVIEW_OUTPUT_MAX_TOKENS  # noqa: E402
from config.prompts.prompts_memory import get_history_review_prompt  # noqa: E402
from utils.config_manager import get_config_manager  # noqa: E402

HISTORY = """棍: 今天耳朵有点痒。
悠怡: 痒的话别抓，我看看。
棍: 别老问我养不养猫。
悠怡: 那我不问了。耳朵还痒吗？
棍: 别老问我养不养猫。
悠怡: 用户在强调边界，我应该道歉并停止追问。策略：1. 道歉 2. 换话题。
"""

# The shipped template opens with the reasoning-limit paragraph; the
# baseline arm drops it so the comparison has exactly one variable.
REASONING_LIMIT_PREFIX = "推理限制："


def _build_prompt(shallow: bool) -> str:
    template = get_history_review_prompt("zh")
    if not template.startswith(REASONING_LIMIT_PREFIX):
        raise SystemExit("review template no longer starts with the reasoning limit")
    if not shallow:
        template = template.split("\n\n", 1)[1]
    return (
        template
        % ("棍", "悠怡", HISTORY, "棍", "悠怡")
    ).replace("{MASTER_NAME}", "棍")


def _usage_numbers(usage) -> tuple[int | None, int | None, int | None]:
    if usage is None:
        return None, None, None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details is not None else None
    if reasoning is None and isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        details = usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens")
    return prompt, completion, reasoning


def _run(client: OpenAI, model: str, name: str, *, shallow: bool, extra_body: dict | None) -> None:
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(shallow)}],
            max_tokens=MEMORY_REVIEW_OUTPUT_MAX_TOKENS,
            extra_body=extra_body,
            timeout=90,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        print(f"{name}: FAILED after {elapsed:.1f}s {type(exc).__name__}: {exc}")
        return
    elapsed = time.perf_counter() - started
    choice = response.choices[0]
    message = choice.message
    reasoning = getattr(message, "reasoning_content", None) or ""
    content = message.content or ""
    prompt_tokens, completion_tokens, reasoning_tokens = _usage_numbers(response.usage)
    print(
        f"{name}: {elapsed:.1f}s finish={choice.finish_reason} "
        f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
        f"reasoning_tokens={reasoning_tokens} "
        f"reasoning_chars={len(reasoning)} content_chars={len(content)}"
    )


def main() -> int:
    cfg = get_config_manager().get_model_api_config("correction")
    model = str(cfg.get("model") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    if not model or not api_key:
        print("correction model or api key missing")
        return 2
    print(f"model={model}")
    client = OpenAI(api_key=api_key, base_url=base_url or None)
    _run(client, model, "baseline", shallow=False, extra_body=None)
    _run(client, model, "prompt_shallow", shallow=True, extra_body=None)
    _run(
        client,
        model,
        "budget_256",
        shallow=False,
        extra_body={"enable_thinking": True, "thinking_budget": 256},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
