"""Music guidance belongs to the selected plugin channel, never browser/CU."""
from types import SimpleNamespace

import pytest

from brain.task_executor import DirectTaskExecutor
from config.prompts.prompts_music_assessment import MUSIC_QUERY_GUIDANCE


@pytest.mark.parametrize("plugin_id", ["netease_music", "weather"])
async def test_music_guidance_only_reaches_the_music_plugin_request(plugin_id):
    requests = []

    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content='{"has_task": false, "can_execute": false}')

    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 100000
    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    executor._build_plugin_desc_lines = lambda plugins: ["Available plugin: " + p["id"] for p in plugins]
    plugins = [{"id": plugin_id, "entries": [{"id": "play_netease_music", "description": "play"}]}]
    await executor._assess_user_plugin("User: play two songs", plugins)
    assert requests
    assert (MUSIC_QUERY_GUIDANCE in requests[0][0]["content"]) == (plugin_id == "netease_music")


async def test_music_guidance_is_absent_from_browser_assessment():
    requests = []

    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content='{"browser_use": {"can_execute": false}}')

    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    executor._retrieve_relevant_corrections = lambda *args, **kwargs: []
    executor._build_correction_lessons_block = lambda *args: ""
    await executor._assess_unified_channels("User: search the web", browser_available=True)
    assert requests
    assert MUSIC_QUERY_GUIDANCE not in requests[0][0]["content"]
