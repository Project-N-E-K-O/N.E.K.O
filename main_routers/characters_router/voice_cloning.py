"""Compatibility alias for the renamed voice registration route module."""

import sys

from . import voice_registration as _voice_registration


# Preserve monkeypatch/import behavior for integrations using the former module
# path while keeping the implementation under its actual registration domain.
sys.modules[__name__] = _voice_registration
