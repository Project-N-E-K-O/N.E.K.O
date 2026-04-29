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

__all__ = [
    'UserActivityTracker',
    'ActivitySnapshot', 'ActivityState', 'Propensity',
    'UnfinishedThread', 'WindowObservation',
    'format_activity_state_section', 'state_to_propensity',
    'SystemSignalCollector', 'SystemSnapshot', 'get_system_signal_collector',
]
