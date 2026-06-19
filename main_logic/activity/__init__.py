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

"""User-activity tracker package.

Public surface:

  * ``UserActivityTracker`` — per-character orchestrator. Owned by
    ``LLMSessionManager``.
  * ``ActivitySnapshot`` — immutable result type returned by
    ``get_snapshot()``.
  * ``ActivityState`` / ``Propensity`` — string enums used both inside
    the snapshot and by the proactive-chat prompt builder.
  * ``get_system_signal_collector`` — process-wide singleton accessor;
    callers usually don't need this directly (tracker auto-uses it),
    but exposed so app shutdown code can ``stop()`` it.

Implementation modules (``state_machine``, ``system_signals``) are
internal — import from this top-level only.
"""

from main_logic.activity.snapshot import (
    ActivitySnapshot,
    ActivityState,
    Propensity,
    UnfinishedThread,
    WindowObservation,
    format_activity_state_section,
    state_to_propensity,
)
from main_logic.activity.system_signals import (
    SystemSignalCollector,
    SystemSnapshot,
    get_system_signal_collector,
)
from main_logic.activity.tracker import UserActivityTracker
from main_logic.activity.focus_scorer import FocusScore, FocusScorer

__all__ = [
    'UserActivityTracker',
    'ActivitySnapshot', 'ActivityState', 'Propensity',
    'UnfinishedThread', 'WindowObservation',
    'format_activity_state_section', 'state_to_propensity',
    'SystemSignalCollector', 'SystemSnapshot', 'get_system_signal_collector',
    'FocusScore', 'FocusScorer',
]
