# Ported from claudian/src/core/prompt/ (main agent prompt)
# Original author: Claudian contributors
# License: MIT

"""
Main agent system prompt.
"""

from __future__ import annotations

# Default system prompt for the main agent
MAIN_AGENT_SYSTEM_PROMPT = """You are Claude, an AI assistant made by Anthropic. You help users with software engineering tasks, coding, debugging, and general questions.

Key guidelines:
- Be helpful, harmless, and honest
- Write clean, well-documented code
- Explain your reasoning
- Ask for clarification when needed
- Respect user preferences and constraints"""


def get_main_agent_system_prompt(custom_prompt: str = "") -> str:
    """Get the main agent system prompt.

    Args:
        custom_prompt: Optional custom prompt to append

    Returns:
        The system prompt
    """
    if custom_prompt:
        return f"{MAIN_AGENT_SYSTEM_PROMPT}\n\n{custom_prompt}"
    return MAIN_AGENT_SYSTEM_PROMPT
