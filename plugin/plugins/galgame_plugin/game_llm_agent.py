from __future__ import annotations

import sys
import types as _types

from .agent_shared import *  # noqa: F401,F403
from .agent_core import GameLLMAgent
from .agent_message_router import AgentMessageRouter
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_scene_tracker import AgentSceneTracker

__all__ = [
    "AgentMessageRouter",
    "AgentSceneTracker",
    "GameLLMAgent",
    "_bounded_choice_instruction_text",
    "_context_line_count",
]

_PROXY_MODULE_NAMES = (
    "agent_actuation",
    "agent_choice_planning",
    "agent_consult",
    "agent_context",
    "agent_core",
    "agent_diagnostics",
    "agent_lifecycle",
    "agent_message_router",
    "agent_observation",
    "agent_ocr_actuation",
    "agent_prompt",
    "agent_scene_context",
    "agent_scene_state",
    "agent_scene_tracker",
    "agent_shared",
    "agent_status",
    "agent_strategy",
    "agent_summary",
    "agent_sync",
    "agent_thinking",
)


class _ShimModule(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        package = __name__.rsplit(".", 1)[0]
        for module_name in _PROXY_MODULE_NAMES:
            module = sys.modules.get(f"{package}.{module_name}")
            if module is not None and hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _ShimModule
