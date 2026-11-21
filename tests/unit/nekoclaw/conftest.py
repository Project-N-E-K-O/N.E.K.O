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

"""Shared settings for the NekoClaw unit tests."""

from __future__ import annotations

from typing import Any

import pytest

# Long enough that the first round of a dispatch never runs out on a loaded CI
# runner. It only changes anything when the round is slow: a fast round is
# answered as soon as it finishes. Note it is longer than the main dialogue's
# TASK_TOOL_TIMEOUT_SECONDS (2.5 s): a future test that goes through that client
# to a slow first round would see the client time out, not the "received"
# receipt production gives; such a test must set its own budget.
GENEROUS_FIRST_ROUND_SECONDS = 30.0


@pytest.fixture(autouse=True)
def _generous_dispatch_first_round(monkeypatch: Any) -> None:
    """Keep the "received" fallback out of tests that do not exercise it.

    ``dispatch`` waits ``DISPATCH_FIRST_ROUND_SECONDS`` (1 s in production) for
    the first round and otherwise answers "received" with no commission id.
    Most tests read ``commission_id`` off that receipt, so a first round that
    takes over a second on a loaded Windows runner turned into a 15 s wait on
    commission ``None``. Tests about the slow-round fallback set their own,
    much shorter budget, which overrides this one.
    """
    monkeypatch.setattr("config.nekoclaw_settings.DISPATCH_FIRST_ROUND_SECONDS", GENEROUS_FIRST_ROUND_SECONDS)
