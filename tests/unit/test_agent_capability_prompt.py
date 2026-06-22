from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from main_logic.core import LLMSessionManager


@pytest.mark.asyncio
async def test_initial_prompt_exposes_enabled_agent_capabilities():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.user_language = "en"
    mgr.lanlan_name = "Matsuko"
    mgr.lanlan_prompt = "\nPersona prompt."
    mgr.agent_flags = {
        "agent_enabled": True,
        "computer_use_enabled": True,
        "browser_use_enabled": True,
        "user_plugin_enabled": False,
        "openclaw_enabled": False,
        "openfang_enabled": False,
    }

    class ConfigManager:
        def is_agent_api_ready(self):
            return True, []

    mgr._config_manager = ConfigManager()
    mgr._fetch_active_agent_tasks_prompt = AsyncMock(return_value="")

    prompt = await mgr._build_initial_prompt()

    assert "Runtime agent capability state" in prompt
    assert "computer_use (mouse/keyboard)" in prompt
    assert "browser_use" in prompt
    assert "do not say you lack permission" in prompt
    assert "user_plugin" not in prompt
