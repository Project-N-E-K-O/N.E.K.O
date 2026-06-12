# Ported from claudian/src/utils/env.ts
# Original author: Claudian contributors
# License: MIT

"""
Environment utilities.
"""

from __future__ import annotations

import os
from typing import Dict, Optional


def parse_environment_variables(env_str: str) -> Dict[str, str]:
    """Parse environment variables from string."""
    result = {}
    if not env_str:
        return result

    for line in env_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()

    return result


def get_env(key: str, default: str = "") -> str:
    """Get an environment variable."""
    return os.environ.get(key, default)


def set_env(key: str, value: str) -> None:
    """Set an environment variable."""
    os.environ[key] = value
