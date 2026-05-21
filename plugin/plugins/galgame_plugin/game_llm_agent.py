from __future__ import annotations

import logging
import sys
import types as _types

from .agent_shared import *  # noqa: F401,F403
from .agent_core import GameLLMAgent
from .agent_message_router import AgentMessageRouter
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_scene_tracker import AgentSceneTracker

# Preserve the historical public module path for inspect/pickle-style consumers.
AgentMessageRouter.__module__ = __name__
AgentSceneTracker.__module__ = __name__
GameLLMAgent.__module__ = __name__

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
            qualified_name = f"{package}.{module_name}"
            module = sys.modules.get(qualified_name)
            if module is not None and hasattr(module, name):
                try:
                    setattr(module, name, value)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "galgame game_llm_agent shim propagation failed: module=%s attr=%s",
                        qualified_name,
                        name,
                        exc_info=True,
                    )
                    raise


sys.modules[__name__].__class__ = _ShimModule
