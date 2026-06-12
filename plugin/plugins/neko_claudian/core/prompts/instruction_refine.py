# Ported from claudian/src/core/prompt/ (instruction refine)
# Original author: Claudian contributors
# License: MIT

"""
Instruction refinement prompts.
"""

from __future__ import annotations

INSTRUCTION_REFINE_SYSTEM_PROMPT = """You are an instruction refinement assistant.
Your task is to help refine and clarify user instructions for an AI coding assistant.

Rules:
- If the instruction is clear and specific, return it as-is
- If the instruction is ambiguous, ask a clarifying question
- If the instruction is too broad, suggest breaking it down
- Be concise and helpful
- Preserve the user's intent"""


def get_instruction_refine_system_prompt() -> str:
    """Get the instruction refine system prompt."""
    return INSTRUCTION_REFINE_SYSTEM_PROMPT


def build_instruction_refine_prompt(
    instruction: str,
    existing_prompt: str = "",
) -> str:
    """Build the instruction refine prompt."""
    parts = []
    if existing_prompt:
        parts.append(f"Existing system prompt:\n{existing_prompt}")
    parts.append(f"User instruction to refine:\n{instruction}")
    return "\n\n".join(parts)
