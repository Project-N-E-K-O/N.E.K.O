"""Give a fixture teardown that hangs after a failure a time limit of its own.

pytest-timeout (``timeout`` in pytest.ini) cancels its timer as soon as a test
fails (its ``pytest_exception_interact`` hook; pytest's own faulthandler timer
is cancelled there as well), and no release that fixes this is left on PyPI
(2.5.0 was yanked). A fixture teardown that hangs after a failure would then
run on to the job's time limit with nothing in the log.

Such a teardown gets a watchdog as long as pytest-timeout's limit for the test:
the stack of every thread is written out, then the process ends. Under xdist
the test is reported as failed and a new worker goes on. A test that passed is
left to pytest-timeout, whose timer is still running through its teardown.

Registered from ``tests/conftest.py`` (``pytest_configure``).
"""

from __future__ import annotations

import faulthandler
import os
from typing import Any

import pytest


def timeout_seconds(item: pytest.Item) -> float:
    """Return pytest-timeout's limit for this test, 0 when there is none.

    The same precedence as pytest-timeout: the ``timeout`` marker, then
    ``--timeout``, then ``PYTEST_TIMEOUT``, then the ini setting. Its own
    resolution is used where it is there, so a shortened, lengthened or
    disabled timeout holds for the watchdog as well.
    """
    try:
        from pytest_timeout import _get_item_settings
    except ImportError:
        _get_item_settings = None
    if _get_item_settings is not None:
        try:
            return float(_get_item_settings(item).timeout or 0)
        except Exception:  # noqa: BLE001 - its internals moved; resolve it here instead
            pass
    marker = item.get_closest_marker("timeout")
    value: Any = None
    if marker is not None:
        value = marker.args[0] if marker.args else marker.kwargs.get("timeout")
    if value is None:
        try:
            value = item.config.getoption("timeout")
        except ValueError:  # pytest-timeout is not installed
            return 0.0
    if value is None:
        value = os.environ.get("PYTEST_TIMEOUT") or item.config.getini("timeout")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _stderr_fd(config: pytest.Config) -> Any:
    """Where pytest's own faulthandler writes: the real stderr, not the captured one."""
    try:
        from _pytest.faulthandler import fault_handler_stderr_fd_key

        return config.stash[fault_handler_stderr_fd_key]
    except (ImportError, KeyError):
        import sys

        return sys.__stderr__


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any):
    outcome = yield
    report = outcome.get_result()
    if report.when in ("setup", "call") and report.failed:
        item.stash[_FAILED_BEFORE_TEARDOWN] = True


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: Any):
    limit = timeout_seconds(item) if item.stash.get(_FAILED_BEFORE_TEARDOWN, False) else 0.0
    if limit > 0:
        faulthandler.dump_traceback_later(limit, file=_stderr_fd(item.config), exit=True)
    try:
        yield
    finally:
        if limit > 0:
            faulthandler.cancel_dump_traceback_later()


_FAILED_BEFORE_TEARDOWN = pytest.StashKey[bool]()
