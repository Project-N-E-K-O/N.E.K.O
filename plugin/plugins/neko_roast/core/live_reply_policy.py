"""Compatibility facade for NEKO Live output policy.

The policy lives in ``main_logic.neko_live_reply_policy`` because
``main_logic`` cannot depend on the plugin layer. Keep this import path for
plugin-side callers and tests.
"""

from __future__ import annotations

from main_logic.neko_live_reply_policy import *  # noqa: F401,F403

