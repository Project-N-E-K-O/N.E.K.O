"""Shared budget loaded only by plugin metadata scan consumers."""

from plugin.server.application.plugins._env_budgets import env_seconds

METADATA_SCAN_TIMEOUT_SECONDS = env_seconds("NEKO_PLUGIN_METADATA_SCAN_TIMEOUT", 10.0)
