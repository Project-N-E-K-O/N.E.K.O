"""Compatibility alias for :mod:`utils.storage.migration`."""

from __future__ import annotations

import sys

from utils.storage import migration as _implementation

sys.modules[__name__] = _implementation
