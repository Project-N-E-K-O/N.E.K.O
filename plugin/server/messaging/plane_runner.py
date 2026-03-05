"""Backward-compat shim — real implementation lives in plugin.message_plane.runner."""
from plugin.message_plane.runner import *  # noqa: F401,F403
from plugin.message_plane.runner import build_message_plane_runner
