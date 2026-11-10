"""Search assessment keeps resolved arguments separate from the raw user turn."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from brain.task_executor import DirectTaskExecutor
from config.prompts.prompts_agent import USER_PLUGIN_SYSTEM_PROMPT
from config.prompts.prompts_plugin_assessment import PLUGIN_CONTEXT_GUIDANCE


QUERY = "Mira Vale Paper Lanterns themes"
PLUGINS = [{"id": "web_search", "entries": [
    {"id": "search", "input_schema": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Standalone keywords for the requested subject"}}, "required": ["query"]}},
    {"id": "fetch"},
]}]


@pytest.mark.parametrize("lang", list(USER_PLUGIN_SYSTEM_PROMPT))
@pytest.mark.parametrize("followup", ["你帮我查一下这本书的主题吧", "还没搜到吗", "你用网络搜索呢"])
async def test_resolved_query_survives_assessment_and_dispatch(monkeypatch, lang, followup):
    requests = []
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 100000
    conversation = (
        "User: 我在读 Mira Vale 的 Paper Lanterns\n"
        "User: 你帮我查一下这本书的主题吧\nAssistant: 我试着查资料。\n"
        f"User: {followup}"
    )

    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content=json.dumps({
            "has_task": True, "can_execute": True, "plugin_id": "web_search",
            "entry_id": "search", "plugin_args": {"query": QUERY},
        }))

    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    decision = await executor._assess_user_plugin(conversation, PLUGINS, lang=lang)
    assert PLUGIN_CONTEXT_GUIDANCE[lang] in requests[0][0]["content"]
    assert "Standalone keywords for the requested subject" in requests[0][0]["content"]
    assert conversation in requests[0][1]["content"]
    assert "command/message/query/objective" not in requests[0][0]["content"]
    assert decision.can_execute
    executor.plugin_list = PLUGINS
    executor._await_run_completion = AsyncMock(return_value={"success": True, "status": "completed"})
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "search-test", "run_token": "test"})

    original = httpx.AsyncClient
    monkeypatch.setattr("brain.task_executor.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handle)))
    result = await executor._execute_user_plugin(
        "test-task", plugin_id=decision.plugin_id, entry_id=decision.entry_id,
        plugin_args=decision.plugin_args, latest_user_request=followup,
    )
    assert result.success
    assert sent[0]["args"]["query"] == QUERY
    assert sent[0]["args"]["_ctx"]["latest_user_request"] == followup
    assert decision.plugin_args == {"query": QUERY}


def test_schema_descriptions_preserve_field_semantics_with_bounded_text():
    from config import AGENT_PLUGIN_FIELD_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import truncate_to_tokens

    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    description = "Keep the user's original message verbatim. " * 100
    catalog = [{"id": "custom_tool", "entries": [{"id": "run", "input_schema": {
        "properties": {
            "message": {"type": "string", "description": description},
            "kind": {"type": "string", "enum": ["raw", "summary"]},
            "other": {"type": "string", "description": None},
        }, "required": ["message"],
    }}]}]
    rendered = "\n".join(executor._build_plugin_desc_lines(catalog))
    assert json.dumps(truncate_to_tokens(description.strip(), AGENT_PLUGIN_FIELD_DESCRIPTION_MAX_TOKENS)) in rendered
    assert description not in rendered
    assert "enum=[raw|summary]" in rendered
    assert "required=['message']" in rendered
    assert "other:string" in rendered


async def test_raw_message_parameter_is_not_rewritten_on_dispatch(monkeypatch):
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor.plugin_list = [{"id": "custom_tool", "entries": [{"id": "run"}]}]
    executor._await_run_completion = AsyncMock(return_value={"success": True, "status": "completed"})
    text = "  Keep My Words\n包括换行，不要翻译。  "
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "raw-test", "run_token": "test"})

    original = httpx.AsyncClient
    monkeypatch.setattr("brain.task_executor.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handle)))
    result = await executor._execute_user_plugin(
        "test-task", plugin_id="custom_tool", entry_id="run",
        plugin_args={"message": text}, latest_user_request=text,
    )
    assert result.success
    assert sent[0]["args"]["message"] == text
    assert sent[0]["args"]["_ctx"]["latest_user_request"] == text


@pytest.mark.parametrize("coarse_result", [TimeoutError("offline"), "invalid JSON", "[]", '["web_search"]'])
async def test_semantic_screen_gets_dialogue_and_failure_keeps_catalog(monkeypatch, coarse_result):
    requests = []
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    # No lexical match. Only the model may resolve what this follow-up means.
    monkeypatch.setattr("brain.task_executor.stage1_filter", lambda *a, **kw: ([], []))
    conversation = executor._format_messages([
        {"role": "user", "content": "请查询 Mira Vale 的 Paper Lanterns 主题"},
        {"role": "assistant", "content": "目前还没有相关结果。"},
        {"role": "user", "content": "有眉目了吗？"},
    ])

    def llm(**kwargs):
        async def answer(messages):
            requests.append((kwargs.get("tier"), messages))
            if kwargs.get("tier") == "emotion":
                if isinstance(coarse_result, Exception):
                    raise coarse_result
                return SimpleNamespace(content=coarse_result)
            return SimpleNamespace(content='{"has_task": false, "can_execute": false}')
        return SimpleNamespace(ainvoke=answer)

    executor._get_llm = llm
    await executor._assess_user_plugin(conversation, PLUGINS)
    assert requests[0][0] == "emotion"
    assert conversation in requests[0][1][1]["content"]
    assert conversation in requests[1][1][1]["content"]
    stage2 = requests[1][1][0]["content"]
    # Valid empty selection remains distinct from timeout/malformed output.
    assert ("- web_search:" in stage2) == (coarse_result != "[]")


async def test_both_semantic_stages_share_bounded_dialogue(monkeypatch):
    from config.agent_settings import AGENT_PLUGIN_CONTEXT_MAX_TOKENS
    from utils.tokenize import count_tokens

    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    screened = []

    async def coarse(conversation, plugins, lang="en"):
        screened.append(conversation)
        return ["web_search"]

    async def answer(messages):
        assert screened[0] in messages[1]["content"]
        return SimpleNamespace(content='{"has_task": false, "can_execute": false}')

    executor._stage1_llm_coarse_screen = coarse
    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    monkeypatch.setattr("brain.task_executor.stage1_filter", lambda *a, **kw: ([], []))
    await executor._assess_user_plugin(
        "LATEST_USER_REQUEST: check new subject\nuser: old subject\n" + "context " * 20000
        + "\nuser: check new subject", PLUGINS,
    )
    assert count_tokens(screened[0]) <= AGENT_PLUGIN_CONTEXT_MAX_TOKENS
    assert "LATEST_USER_REQUEST: check new subject" in screened[0]
    assert screened[0].endswith("user: check new subject")


async def test_failed_screen_does_not_restore_passive_or_hidden_tools(monkeypatch):
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    requests = []

    async def answer(messages):
        requests.append(messages)
        return SimpleNamespace(content='{"has_task": false, "can_execute": false}')

    async def failed(*args, **kwargs):
        raise TimeoutError("screen unavailable")

    executor._stage1_llm_coarse_screen = failed
    executor._get_llm = lambda **kwargs: SimpleNamespace(ainvoke=answer)
    monkeypatch.setattr("brain.task_executor.stage1_filter", lambda *a, **kw: ([], []))
    catalog = PLUGINS + [
        {"id": "passive_tool", "passive": True, "entries": [{"id": "run"}]},
        {"id": "hidden_tool", "entries": [{"id": "run", "metadata": {"agent_hidden": True}}]},
    ]
    await executor._assess_user_plugin("User: search for a book", catalog)
    prompt = requests[0][0]["content"]
    assert "- web_search:" in prompt
    assert "passive_tool" not in prompt
    assert "hidden_tool" not in prompt


async def test_cancelled_screen_does_not_start_final_assessment(monkeypatch):
    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    tiers = []

    def llm(**kwargs):
        tiers.append(kwargs.get("tier"))

        async def cancelled(messages):
            raise asyncio.CancelledError()

        return SimpleNamespace(ainvoke=cancelled)

    executor._get_llm = llm
    monkeypatch.setattr("brain.task_executor.stage1_filter", lambda *a, **kw: ([], []))
    with pytest.raises(asyncio.CancelledError):
        await executor._assess_user_plugin("User: search for a book", PLUGINS)
    assert tiers == ["emotion"]
