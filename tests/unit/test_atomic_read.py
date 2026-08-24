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
"""Guards for the test-side reader that tolerates an in-flight atomic replace.

The race these helpers exist for is Windows-only and millisecond-wide, so no
test can trigger it on demand. Without these guards, "simplifying" either
helper back to a plain read would look green everywhere and quietly restore the
flake on Windows CI.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.atomic_read import (
    read_json_when_readable,
    read_text_tolerating_replace,
    read_text_when_readable,
)

pytestmark = pytest.mark.unit


def _fail_then_succeed(monkeypatch, failures: int, exc_factory):
    """Make the first ``failures`` reads raise; count every attempt."""
    real_read_text = Path.read_text
    attempts: list[int] = []

    def flaky(self, *args, **kwargs):
        attempts.append(1)
        if len(attempts) <= failures:
            raise exc_factory()
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    return attempts


def test_sync_read_retries_a_busy_target_and_returns_the_content(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text('{"v": 1}', encoding="utf-8")
    attempts = _fail_then_succeed(monkeypatch, 2, lambda: PermissionError(13, "denied"))

    assert read_text_tolerating_replace(target, backoff=0) == '{"v": 1}'
    assert len(attempts) == 3


def test_sync_read_surfaces_a_target_that_stays_locked(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")
    attempts = _fail_then_succeed(monkeypatch, 99, lambda: PermissionError(13, "denied"))

    with pytest.raises(PermissionError):
        read_text_tolerating_replace(target, attempts=4, backoff=0)
    assert len(attempts) == 4, "重试次数必须有硬上界，且最后一次原样抛出"


def test_sync_read_does_not_swallow_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_text_tolerating_replace(tmp_path / "never-written.json", backoff=0)


@pytest.mark.asyncio
async def test_async_read_waits_through_both_absence_and_a_busy_target(
    tmp_path, monkeypatch
):
    # 这两种失败要一视同仁：文件还没被 replace 上来（FileNotFoundError），和文件
    # 在盘上但此刻打不开（PermissionError）—— 后者正是 `exists()` 门守不住的那个。
    target = tmp_path / "state.json"
    target.write_text('{"v": 7}', encoding="utf-8")
    errors = iter([FileNotFoundError(2, "nope"), PermissionError(13, "denied")])
    attempts = _fail_then_succeed(monkeypatch, 2, lambda: next(errors))

    assert await read_json_when_readable(target, poll=0) == {"v": 7}
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_async_read_fails_loudly_when_the_artifact_never_shows_up(tmp_path):
    with pytest.raises(AssertionError, match="never became readable"):
        await read_text_when_readable(tmp_path / "never.json", timeout=0.05, poll=0.01)
