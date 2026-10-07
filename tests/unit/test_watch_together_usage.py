from types import SimpleNamespace
from main_logic.watch_together.engine import record_usage


def test_cached_and_reasoning_are_subsets_not_added_twice():
    job = {}
    response = SimpleNamespace(usage={"prompt_tokens": 100, "completion_tokens": 30,
        "total_tokens": 130, "prompt_tokens_details": {"cached_tokens": 40},
        "completion_tokens_details": {"reasoning_tokens": 10}})
    record_usage(job, response, "vision", "window1")
    record_usage(job, response, "vision", "window1 retry")
    assert job["usage"]["total_tokens"] == 260
    assert job["usage"]["calls"][0]["cached_tokens"] == 40


def test_missing_usage_is_not_claimed_as_zero_consumption():
    job = {}
    record_usage(job, SimpleNamespace(usage=None), "vision", "window")
    assert job["usage"]["missing_usage_calls"] == 1
    assert job["usage"]["calls"][0]["total_tokens"] is None


def test_total_fallback_and_deepseek_cache():
    job = {}
    record_usage(job, SimpleNamespace(usage={"prompt_tokens": 12, "completion_tokens": 3,
        "prompt_cache_hit_tokens": 5}), "vision", "window")
    assert job["usage"]["total_tokens"] == 15
    assert job["usage"]["calls"][0]["cached_tokens"] == 5


def test_malformed_nested_usage_does_not_abort_accounting():
    for details in ['invalid', [1], True, 42]:
        job = {}
        record_usage(job, SimpleNamespace(usage={'prompt_tokens': 12, 'completion_tokens': 3,
            'prompt_tokens_details': details, 'completion_tokens_details': details}), 'vision', 'window')
        assert job['usage']['total_tokens'] == 15
        assert job['usage']['calls'][0]['cached_tokens'] is None
        assert job['usage']['calls'][0]['reasoning_tokens'] is None
