# Ported from claudian/src/core/providers/providerEnvironment.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider environment configuration.
"""

from __future__ import annotations

import os
from typing import Dict, Optional


def get_provider_env(provider_id: str) -> Dict[str, str]:
    """Get environment variables for a provider."""
    prefix = f"NEKO_{provider_id.upper()}_"
    return {
        key: value
        for key, value in os.environ.items()
        if key.startswith(prefix)
    }


def get_api_key(provider_id: str) -> Optional[str]:
    """Get API key for a provider."""
    key_name = f"ANTHROPIC_API_KEY"
    return os.environ.get(key_name)
