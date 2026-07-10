import pytest


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def ainvoke(self, messages):
        # Copy the outer list: the assessor mutates `messages` in place on the
        # correction retry, which would otherwise rewrite this captured snapshot.
        self.calls.append(list(messages))
        return _FakeResponse(self.content)


class _SeqLLM:
    """Returns a different canned response per call (last one sticks)."""

    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

    async def ainvoke(self, messages):
        # Copy the outer list: the assessor mutates `messages` in place on the
        # correction retry, which would otherwise rewrite this captured snapshot.
        self.calls.append(list(messages))
        idx = min(len(self.calls) - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


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


@pytest.mark.asyncio
async def test_correction_retry_telemetry_records_fail(monkeypatch):
    """A retry that still yields an invalid id is counted as a failed retry."""
    from brain import task_executor as task_executor_module
    from brain.task_executor import DirectTaskExecutor
    from utils import instrument

    monkeypatch.setattr(task_executor_module, "stage1_filter", lambda *args, **kwargs: ([], []))
    instrument.snapshot()  # clear any data from earlier tests in this process

    executor = object.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    executor._stage1_llm_coarse_screen = _no_coarse_ids

    # Stage 1 filters everything out, so any plugin_id is invalid -> retry, and the
    # retry returns the same invalid decision -> still fails final validation.
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
    # initial call + one correction retry
    assert len(fake_llm.calls) == 2

    counters = instrument.snapshot()["counters"]
    # One Stage-2 call total; it was actionable (has_task & can_execute), the only
    # state a correction retry can fire from. The retry's own ainvoke is not a new
    # Stage-2 assessment, so the denominators stay at 1.
    assert counters.get("plugin_assess_stage2") == 1
    assert counters.get("plugin_assess_stage2_actionable") == 1
    assert counters.get("plugin_assess_correction_retry|result=fail") == 1
    assert "plugin_assess_correction_retry|result=success" not in counters


@pytest.mark.asyncio
async def test_correction_retry_telemetry_records_success(monkeypatch):
    """A retry that fixes the id is counted as a successful retry."""
    from brain.task_executor import DirectTaskExecutor
    from utils import instrument

    instrument.snapshot()  # clear any data from earlier tests in this process

    executor = object.__new__(DirectTaskExecutor)
    # High threshold -> skip Stage 1, so the full plugin list is the candidate set.
    executor._STAGE1_TRIGGER_TOKENS = 1_000_000
    executor._stage1_llm_coarse_screen = _no_coarse_ids

    # First response has a bogus entry_id (triggers correction); retry fixes it.
    seq_llm = _SeqLLM([
        '{"has_task": true, "can_execute": true, "task_description": "run alpha", '
        + '"plugin_id": "alpha", "entry_id": "bogus", "plugin_args": {}, "reason": "bad entry"}',
        '{"has_task": true, "can_execute": true, "task_description": "run alpha", '
        + '"plugin_id": "alpha", "entry_id": "run", "plugin_args": {}, "reason": "fixed"}',
    ])
    executor._get_llm = lambda **_kwargs: seq_llm

    result = await executor._assess_user_plugin(
        "LATEST_USER_REQUEST: run alpha",
        _make_plugins(),
        lang="en",
    )

    assert result.can_execute is True
    assert result.plugin_id == "alpha"
    assert result.entry_id == "run"
    assert len(seq_llm.calls) == 2

    counters = instrument.snapshot()["counters"]
    assert counters.get("plugin_assess_stage2") == 1
    assert counters.get("plugin_assess_stage2_actionable") == 1
    assert counters.get("plugin_assess_correction_retry|result=success") == 1
    assert "plugin_assess_correction_retry|result=fail" not in counters


@pytest.mark.asyncio
async def test_stage2_denominator_includes_unparsed_response(monkeypatch):
    """An unparseable Stage-2 response counts toward the total denominator but is
    neither actionable nor a retry — so the cost rate isn't overstated."""
    from brain import task_executor as task_executor_module
    from brain.task_executor import DirectTaskExecutor
    from utils import instrument

    monkeypatch.setattr(task_executor_module, "stage1_filter", lambda *args, **kwargs: ([], []))
    instrument.snapshot()  # clear any data from earlier tests in this process

    executor = object.__new__(DirectTaskExecutor)
    executor._STAGE1_TRIGGER_TOKENS = 1
    executor._stage1_llm_coarse_screen = _no_coarse_ids

    # No JSON object at all -> early return on the parse-failure path, before any
    # validation / retry can happen.
    fake_llm = _FakeLLM("sorry, I cannot help with that")
    executor._get_llm = lambda **_kwargs: fake_llm

    result = await executor._assess_user_plugin(
        "LATEST_USER_REQUEST: unrelated request",
        _make_plugins(),
        lang="en",
    )

    assert result.can_execute is False
    assert len(fake_llm.calls) == 1  # no correction retry fired

    counters = instrument.snapshot()["counters"]
    assert counters.get("plugin_assess_stage2") == 1
    assert "plugin_assess_stage2_actionable" not in counters
    assert not any(k.startswith("plugin_assess_correction_retry") for k in counters)
