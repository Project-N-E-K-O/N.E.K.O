"""Module-level constants for the galgame plugin.

These constants were originally defined inline in ``__init__.py``. They were
moved here verbatim during the PR2 split so both the GalgamePlugin class body
and the plugin_entries mixin files can import them without circular references.
"""
from __future__ import annotations


_OCR_BACKEND_SELECTIONS = {"auto", "rapidocr"}
_OCR_CAPTURE_BACKEND_SELECTIONS = {"auto", "smart", "dxcam", "mss", "pyautogui", "printwindow"}
_BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS = 45.0
_BRIDGE_TICK_INTERVAL_SECONDS = 1.0
# Foreground refresh TTL: repeated calls within two seconds return early so
# bridge_tick, advance monitor, and status payload refreshes stay idempotent.
_OCR_FOREGROUND_REFRESH_TTL_SECONDS = 2.0
_LATENCY_SAMPLE_LIMIT = 120
_LATENCY_MIN_SAMPLES_FOR_P95 = 5
_OCR_POLL_P95_DEGRADE_THRESHOLD_SECONDS = 3.0
_OCR_FOREGROUND_ADVANCE_MONITOR_INTERVAL_SECONDS = 0.05
_OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS = 0.15
_OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS = 0.15
_OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS = 2.0
