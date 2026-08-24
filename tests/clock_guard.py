# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Fail any test that replaces a stdlib clock function process-wide.

Patching ``time.monotonic`` (or ``time``/``perf_counter``/``sleep``) on the
stdlib module hands every thread in the process a fake clock for the duration
of the test. With a stateful fake — an iterator, a counter — it is also a race:
whichever thread calls first consumes a value, and this repo leaves background
threads running across a suite. That is how
``test_sender_token_bucket_preserves_average_rate_under_jitter`` started failing
in full runs while passing in isolation.

Enforced at runtime rather than by scanning source. A source scanner has to
enumerate every way to install a fake — ``monkeypatch.setattr`` positional,
keyword and dotted-string forms, module aliases (``time``, ``_time``,
``real_time``, ``time_module``…), direct assignment — and each missed spelling
reads exactly like a passing guard. Comparing the actual function objects is
indifferent to how the patch was written.

What it does and does not cover
-------------------------------
The check runs after the test body and before teardown, so it catches any
patch still in effect at that point: ``monkeypatch`` (which restores during
teardown — the repo's standard, and the one that caused the bug above) and
plain assignment that is never restored. It does **not** catch a patch that is
already undone inside the body, such as a ``with mock.patch(...)`` block that
closes before the test returns; that form is still racy while open, but it is
self-limiting and the repo currently has no clock patch written that way.
Catching it too would need an audit hook or an immutable ``time`` proxy, which
is more machinery than the risk warrants.

Use ``tests.fake_clock.patch_module_clock`` to fake a clock for one module.
"""

import time

import pytest

_GUARDED = ("time", "monotonic", "perf_counter", "sleep", "monotonic_ns", "time_ns")
_REAL = {name: getattr(time, name) for name in _GUARDED}


def _drifted() -> list[str]:
    return [name for name, real in _REAL.items() if getattr(time, name, None) is not real]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Check while the test is still running.

    ``monkeypatch`` undoes its patches in the teardown phase, so a check that
    ran afterwards would always see the clock restored. This wrapper resumes
    after the call phase and before teardown, which is exactly the window where
    the patch is live.
    """
    outcome = yield
    drifted = _drifted()
    # 先无条件还原：直接赋值（不走 monkeypatch）的假时钟没人替它撤销，用例失败
    # 时若跟着早退，它会活到后面每一条用例里——正是这个模块声称要挡的那种泄漏。
    for name in drifted:
        setattr(time, name, _REAL[name])
    if drifted and outcome.excinfo is None:  # 用例本身已失败时不盖掉原因
        pytest.fail(
            f"{item.nodeid} 把 stdlib time 的 {drifted} 换掉了——这是全进程生效的，"
            "任何后台线程都会读到假时钟；若假时钟有状态（迭代器/计数器），别人调一次"
            "就把它打乱。改用 tests.fake_clock.patch_module_clock(monkeypatch, "
            "<真正读时钟的模块>, monotonic=...)。",
            pytrace=False,
        )
