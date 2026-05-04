import pytest


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return _FakeResponse(self.content)


def _make_plugins():
    return [
        {
            "id": "alpha",
            "description": "alpha plugin does calendar automation",
            "entries": [{"id": "run", "description": "run alpha"}],
        },
        {
            "id": "beta",
            "description": "beta plugin controls lights",
            "entries": [{"id": "run", "description": "run beta"}],
        },
    ]


async def _no_quota(_call_site):
    return None


async def _no_coarse_ids(_user_text, _plugins, lang="en"):
    return []


@pytest.mark.asyncio
async def test_stage1_empty_union_does_not_fallback_to_full_plugin_list(monkeypatch):
    from brain import task_executor as task_executor_module
    from brain.task_executor import DirectTaskExecutor

    monkeypatch.setattr(task_executor_module, "stage1_filter", lambda *args, **kwargs: ([], []))

    executor = object.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    executor._stage1_llm_coarse_screen = _no_coarse_ids
    executor._check_agent_quota = _no_quota

    fake_llm = _FakeLLM(
        '{"has_task": false, "can_execute": false, "task_description": "", '
        '"plugin_id": null, "entry_id": null, "plugin_args": null, "reason": "no candidates"}'
    )
    executor._get_llm = lambda **_kwargs: fake_llm

    result = await executor._assess_user_plugin(
        "LATEST_USER_REQUEST: unrelated request",
        _make_plugins(),
        lang="en",
    )

    assert result.can_execute is False
    assert fake_llm.calls
    stage2_system_prompt = fake_llm.calls[0][0]["content"]
    assert "No plugins available." in stage2_system_prompt
    assert "- alpha:" not in stage2_system_prompt
    assert "- beta:" not in stage2_system_prompt


@pytest.mark.asyncio
async def test_stage1_empty_union_rejects_hallucinated_existing_plugin(monkeypatch):
    from brain import task_executor as task_executor_module
    from brain.task_executor import DirectTaskExecutor

    monkeypatch.setattr(task_executor_module, "stage1_filter", lambda *args, **kwargs: ([], []))

    executor = object.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    executor._stage1_llm_coarse_screen = _no_coarse_ids
    executor._check_agent_quota = _no_quota

    fake_llm = _FakeLLM(
        '{"has_task": true, "can_execute": true, "task_description": "run alpha", '
        '"plugin_id": "alpha", "entry_id": "run", "plugin_args": {}, "reason": "hallucinated"}'
    )
    executor._get_llm = lambda **_kwargs: fake_llm

    result = await executor._assess_user_plugin(
        "LATEST_USER_REQUEST: unrelated request",
        _make_plugins(),
        lang="en",
    )

    assert result.can_execute is False
    assert result.plugin_id == "alpha"
    assert result.reason == "plugin_id 'alpha' not available in current candidates"
