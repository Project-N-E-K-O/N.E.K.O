"""Give a fixture teardown that hangs after a failure a time limit of its own.

pytest-timeout (``timeout`` in pytest.ini) cancels its timer as soon as a test
fails (its ``pytest_exception_interact`` hook; pytest's own faulthandler timer
is cancelled there as well), and no release that fixes this is left on PyPI
(2.5.0 was yanked). A fixture teardown that hangs after a failure would then
run on to the job's time limit with nothing in the log.

Such a teardown gets a watchdog for what is left of pytest-timeout's limit
for the test, counted from its setup, and at least 60 seconds:
the stack of every thread is written out, then the process ends. Under xdist
the test is reported as failed and a new worker goes on. A test that passed is
left to pytest-timeout, whose timer is still running through its teardown.
Not for a test timed with ``func_only`` (its fixtures have no limit), nor
while a debugger is in use (pytest-timeout stands down then as well).

Registered from ``tests/conftest.py`` (``pytest_configure``).
"""

from __future__ import annotations

import faulthandler
import os
import time
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


def _item_settings(item: pytest.Item) -> Any:
    """pytest-timeout's own settings for this test, None when they cannot be had."""
    try:
        from pytest_timeout import _get_item_settings

        return _get_item_settings(item)
    except Exception:  # noqa: BLE001 - not installed, or its internals moved
        return None


def times_fixtures(item: pytest.Item) -> bool:
    """Whether pytest-timeout's limit covers this test's fixtures, not only its function.

    With ``func_only`` (marker or ``timeout_func_only``) it times the test
    function alone, and a long setup is allowed: there is no budget for the
    whole test that a teardown could be held to.
    """
    settings = _item_settings(item)
    if settings is not None:
        return not settings.func_only
    marker = item.get_closest_marker("timeout")
    if marker is not None and marker.kwargs.get("func_only") is not None:
        return not marker.kwargs["func_only"]
    try:
        return not item.config.getini("timeout_func_only")
    except ValueError:  # pytest-timeout is not installed
        return True


def debugging(item: pytest.Item) -> bool:
    """Whether someone is at a debugger, as pytest-timeout tells it (or ``--pdb``).

    pytest-timeout stops its timers once a debugger is entered; a watchdog
    that ends the process must not come back after ``continue``.
    """
    if item.config.getvalue("usepdb"):
        return True
    settings = _item_settings(item)
    if settings is not None and settings.disable_debugger_detection:
        return False
    try:
        from pytest_timeout import is_debugging

        return bool(is_debugging())
    except Exception:  # noqa: BLE001 - not installed, or its internals moved
        return False


def _stderr_fd(config: pytest.Config) -> Any:
    """Where pytest's own faulthandler writes: the real stderr, not the captured one."""
    try:
        from _pytest.faulthandler import fault_handler_stderr_fd_key

        return config.stash[fault_handler_stderr_fd_key]
    except (ImportError, KeyError):
        import sys

        return sys.__stderr__


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item):
    item.stash[_STARTED_AT] = time.monotonic()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any):
    outcome = yield
    report = outcome.get_result()
    # Only where an exception was raised: that is when pytest-timeout (and
    # pytest's faulthandler timer) are cancelled. An xfail(strict=True) that
    # passed is a failed report without one, and both timers are still running.
    if report.when in ("setup", "call") and report.failed and call.excinfo is not None:
        item.stash[_FAILED_BEFORE_TEARDOWN] = True


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: Any):
    limit = 0.0
    if item.stash.get(_FAILED_BEFORE_TEARDOWN, False) and times_fixtures(item) and not debugging(item):
        limit = timeout_seconds(item)
    if limit > 0:
        # What is left of the test's whole budget, counted from its setup: a
        # test that failed at second 299 does not get another 300 for its
        # teardown. But never less than a grace an ordinary teardown fits in
        # (closing a loop, stopping tasks, removing a temporary tree): where
        # pytest-timeout fails a test by a signal (Linux, macOS) the run goes
        # on afterwards, and ending the process here would lose the rest of it.
        started = item.stash.get(_STARTED_AT, time.monotonic())
        left = max(limit - (time.monotonic() - started), _TEARDOWN_GRACE_SECONDS)
        faulthandler.dump_traceback_later(left, file=_stderr_fd(item.config), exit=True)
    try:
        yield
    finally:
        if limit > 0:
            faulthandler.cancel_dump_traceback_later()


_FAILED_BEFORE_TEARDOWN = pytest.StashKey[bool]()
_STARTED_AT = pytest.StashKey[float]()
_TEARDOWN_GRACE_SECONDS = 60.0
