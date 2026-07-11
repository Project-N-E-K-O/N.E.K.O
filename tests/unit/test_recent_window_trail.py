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

"""Unit tests for the recent-window trail fed into the activity_guess LLM.

``ActivityStateMachine.recent_window_trail`` turns the window-change history into
``(canonical, dwell)`` pairs; ``UserActivityTracker._format_window_trail`` renders
the compact prompt line. Both are exercised hermetically — windows are hand-built
``WindowObservation``s fed at controlled timestamps, no polling / no prefs I/O.
"""

from __future__ import annotations

from types import SimpleNamespace

from main_logic.activity.snapshot import WindowObservation
from main_logic.activity.state_machine import ActivityStateMachine
from main_logic.activity.tracker import UserActivityTracker
from utils.activity_config import ActivityPreferences


def _win(canonical: str, category: str, sub: str) -> WindowObservation:
    """A WindowObservation with a distinct canonical and a *sensitive* title, so
    tests can assert the title never leaks into the trail."""
    return WindowObservation(
        process_name=f'{canonical}.exe',
        title=f'secret-{canonical}-document',
        category=category,
        subcategory=sub,
        canonical=canonical,
        is_browser=(category == 'entertainment'),
    )


def _sm() -> ActivityStateMachine:
    return ActivityStateMachine(prefs=ActivityPreferences())


def test_recent_window_trail_canonical_dwell_and_no_titles():
    """Oldest-first, current-last; dwell = time until the next change (or `now`
    for the still-active current window); window titles never leak."""
    sm = _sm()
    t0 = 1000.0
    sm.update_window(_win('VS Code', 'work', 'ide'), now=t0)
    sm.update_window(_win('Chrome', 'entertainment', 'browser'), now=t0 + 360)
    sm.update_window(_win('Slack', 'communication', 'im'), now=t0 + 480)

    trail = sm.recent_window_trail(now=t0 + 520, limit=3)
    assert [name for name, _ in trail] == ['VS Code', 'Chrome', 'Slack']
    assert [dwell for _, dwell in trail] == [360.0, 120.0, 40.0]
    assert all('secret' not in name for name, _ in trail)


def test_recent_window_trail_limit_and_max_age():
    sm = _sm()
    t0 = 1000.0
    sm.update_window(_win('A', 'work', 'ide'), now=t0)
    sm.update_window(_win('B', 'entertainment', 'browser'), now=t0 + 100)
    sm.update_window(_win('C', 'communication', 'im'), now=t0 + 200)
    sm.update_window(_win('D', 'work', 'ide'), now=t0 + 300)
    now = t0 + 350

    # limit keeps only the newest N.
    assert [n for n, _ in sm.recent_window_trail(now=now, limit=2)] == ['C', 'D']
    # max_age drops windows whose switch is older than the cutoff: only D's switch
    # (t0+300) is within 120s of now (t0+350).
    assert [n for n, _ in sm.recent_window_trail(now=now, limit=5, max_age_seconds=120)] == ['D']


def test_recent_window_trail_single_window_kept_as_one_entry():
    sm = _sm()
    sm.update_window(_win('VS Code', 'work', 'ide'), now=1000.0)
    assert sm.recent_window_trail(now=1100.0, limit=3) == [('VS Code', 100.0)]


def test_format_window_trail_omits_single_and_renders_flow():
    """The tracker renders a trail line only when there's an actual flow (>= 2
    windows); a lone current window adds nothing over ``active_window``."""
    sm = _sm()
    fake = SimpleNamespace(_sm=sm)  # _format_window_trail only touches self._sm
    fmt = UserActivityTracker._format_window_trail

    t0 = 1000.0
    sm.update_window(_win('VS Code', 'work', 'ide'), now=t0)
    assert fmt(fake, now=t0 + 60) is None  # single window → no flow → omitted

    sm.update_window(_win('Chrome', 'entertainment', 'browser'), now=t0 + 360)
    line = fmt(fake, now=t0 + 480)
    assert line == 'VS Code 6min -> Chrome 2min'
    assert 'secret' not in line  # app names only, never titles


def test_recent_window_trail_excludes_private_entries():
    """A 'private' window recorded in history (the loop's private bail is
    downstream of update_window) must NOT resurface in the trail after the user
    leaves private mode — even though the observation carries a canonical."""
    sm = _sm()
    t0 = 1000.0
    sm.update_window(_win('VS Code', 'work', 'ide'), now=t0)
    sm.update_window(
        WindowObservation(
            process_name=None, title='secret-bank', category='private',
            subcategory=None, canonical='[private]', is_browser=False,
        ),
        now=t0 + 60,
    )
    sm.update_window(_win('Chrome', 'entertainment', 'browser'), now=t0 + 120)
    assert [n for n, _ in sm.recent_window_trail(now=t0 + 180, limit=5)] == ['VS Code', 'Chrome']


def test_recent_window_trail_dwell_not_inflated_by_noncanonical_gap():
    """A canonical-less window sandwiched between two named ones drops its own
    time rather than folding it into the previous app's dwell."""
    sm = _sm()
    t0 = 1000.0
    sm.update_window(_win('A', 'work', 'ide'), now=t0)
    sm.update_window(
        WindowObservation(
            process_name='unknown.exe', title='secret-unknown', category='unknown',
            subcategory=None, canonical=None, is_browser=False,
        ),
        now=t0 + 50,
    )
    sm.update_window(_win('B', 'communication', 'im'), now=t0 + 100)
    trail = sm.recent_window_trail(now=t0 + 130, limit=5)
    assert trail[0] == ('A', 50.0)   # not 100.0 — the gap's 50s is dropped, not added
    assert trail[1] == ('B', 30.0)
