"""Compatibility alias for :mod:`utils.storage.policy`."""

from __future__ import annotations

import sys

from utils.storage import policy as _implementation

sys.modules[__name__] = _implementation
