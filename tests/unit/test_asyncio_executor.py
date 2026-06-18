from utils.asyncio_executor import resolve_default_executor_max_workers


def test_default_executor_workers_have_low_end_floor():
    assert resolve_default_executor_max_workers(1) == 16
    assert resolve_default_executor_max_workers(4) == 16


def test_default_executor_workers_fall_back_when_cpu_count_is_unknown(monkeypatch):
    monkeypatch.setattr("utils.asyncio_executor.os.cpu_count", lambda: None)
    assert resolve_default_executor_max_workers(None) == 16


def test_default_executor_workers_follow_python_default_in_middle():
    assert resolve_default_executor_max_workers(20) == 24


def test_default_executor_workers_are_capped():
    assert resolve_default_executor_max_workers(128) == 32
