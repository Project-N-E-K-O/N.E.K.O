"""Music guidance belongs to the selected plugin channel, never browser/CU."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from brain.task_executor import DirectTaskExecutor
from config.prompts.prompts_music_assessment import music_query_guidance

MUSIC_QUERY_GUIDANCE = music_query_guidance("en")


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


@pytest.mark.parametrize("followup,query", [
    ("你用网络搜索呢", "Mira Vale Paper Lanterns lyrics meaning"),
    ("先不查这首了，查京都十一月天气", "京都 十一月 天气"),
    ("取消搜索，也不要播放", None),
])
async def test_music_catalog_preserves_other_tools_and_dialogue(followup, query):
    """Check the planning contract; semantic choices are supplied by the model."""
    requests = []
    plugins = [
        {"id": "netease_music", "entries": [
            {"id": "play_netease_music", "input_schema": {
                "properties": {"query": {"type": "string"}}, "required": ["query"],
            }},
            {"id": "cloud_music", "input_schema": {
                "properties": {"action": {"type": "string", "enum": ["play", "add"]}},
            }},
        ]},
        {"id": "web_search", "entries": [{"id": "search", "input_schema": {
            "properties": {"query": {"type": "string"}}, "required": ["query"],
        }}]},
    ]
    original = deepcopy(plugins)
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 100000
    conversation = executor._format_messages([
        {"role": "user", "content": "我在听 Mira Vale 的 Paper Lanterns"},
        {"role": "user", "content": "请查歌词含义"},
        {"role": "assistant", "content": "目前还没查到。"},
        {"role": "user", "content": followup},
    ])

    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content=json.dumps({
            "has_task": query is not None, "can_execute": query is not None,
            "plugin_id": "web_search" if query else None,
            "entry_id": "search" if query else None,
            "plugin_args": {"query": query} if query else None,
        }))

    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    decision = await executor._assess_user_plugin(conversation, plugins)
    assert plugins == original
    assert conversation in requests[0][1]["content"]
    assert "- web_search:" in requests[0][0]["content"]
    assert "play_netease_music" in requests[0][0]["content"]
    assert "cloud_music args(" not in requests[0][0]["content"]
    assert MUSIC_QUERY_GUIDANCE in requests[0][0]["content"]
    assert decision.can_execute is (query is not None)
    if query:
        assert decision.plugin_id == "web_search"
        assert decision.plugin_args == {"query": query}


@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
async def test_localized_music_rules_are_loaded_only_for_selected_plugin(lang, monkeypatch):
    from unittest.mock import AsyncMock
    import brain.task_executor as task_executor
    from config.prompts.prompts_agent import USER_PLUGIN_SYSTEM_PROMPT
    from config.prompts.prompts_sys import SESSION_INIT_PROMPT_AGENT, SESSION_INIT_PROMPT_AGENT_DYNAMIC
    from config.prompts.prompts_music_assessment import music_query_guidance
    requests = []
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 0
    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content='{"has_task": false, "can_execute": false}')
    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    executor._stage1_llm_coarse_screen = AsyncMock(return_value=["weather"])
    monkeypatch.setattr(task_executor, "stage1_filter", lambda query, plugins, **kwargs: ([plugins[1]], {}))
    plugins = [{"id": "netease_music", "entries": [{"id": "play_netease_music"}]},
               {"id": "weather", "entries": [{"id": "forecast"}]}]
    await executor._assess_user_plugin("user: weather tomorrow", plugins, lang=lang)
    assert music_query_guidance(lang) not in requests[0][0]["content"]
    assert "netease_music" not in requests[0][0]["content"]
    executor._STAGE1_TRIGGER_TOKENS = 100000
    await executor._assess_user_plugin("user: play music", plugins, lang=lang)
    assert music_query_guidance(lang) in requests[-1][0]["content"]
    assert "playlist.save_current" not in USER_PLUGIN_SYSTEM_PROMPT[lang]
    assert "歌单" not in SESSION_INIT_PROMPT_AGENT[lang]
    assert "歌单" not in SESSION_INIT_PROMPT_AGENT_DYNAMIC[lang]
