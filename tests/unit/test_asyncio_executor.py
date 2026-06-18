import concurrent.futures
from unittest.mock import Mock

from utils.asyncio_executor import (
    DEFAULT_EXECUTOR_THREAD_PREFIX,
    configure_default_executor,
    resolve_default_executor_max_workers,
)


def test_default_executor_workers_have_low_end_floor():
    assert resolve_default_executor_max_workers(1) == 16
    assert resolve_default_executor_max_workers(4) == 16
    assert resolve_default_executor_max_workers(11) == 16
    assert resolve_default_executor_max_workers(12) == 16


def test_default_executor_workers_fall_back_when_cpu_count_is_unknown(monkeypatch):
    monkeypatch.setattr("utils.asyncio_executor.os.cpu_count", lambda: None)
    assert resolve_default_executor_max_workers(None) == 16


def test_default_executor_workers_follow_python_default_in_middle():
    assert resolve_default_executor_max_workers(20) == 24


def test_default_executor_workers_are_capped():
    assert resolve_default_executor_max_workers(27) == 31
    assert resolve_default_executor_max_workers(28) == 32
    assert resolve_default_executor_max_workers(128) == 32


def test_configure_default_executor_installs_named_thread_pool(monkeypatch):
    monkeypatch.setattr("utils.asyncio_executor.os.cpu_count", lambda: 4)
    loop = Mock()

    max_workers = configure_default_executor(loop)

    assert max_workers == 16
    loop.set_default_executor.assert_called_once()
    executor = loop.set_default_executor.call_args.args[0]
    try:
        assert isinstance(executor, concurrent.futures.ThreadPoolExecutor)
        assert executor._max_workers == 16
        assert executor._thread_name_prefix == DEFAULT_EXECUTOR_THREAD_PREFIX
    finally:
        executor.shutdown(wait=False)
