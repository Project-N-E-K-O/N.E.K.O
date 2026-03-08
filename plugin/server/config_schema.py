"""Backward-compat shim — real implementation lives in plugin.config.schema."""
from plugin.config.schema import *  # noqa: F401,F403
from plugin.config.schema import validate_plugin_config, ConfigValidationError
