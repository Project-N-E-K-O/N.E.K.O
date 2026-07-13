"""Compatibility alias for :mod:`utils.storage.layout`."""

from __future__ import annotations

import sys

from utils.storage import layout as _implementation

sys.modules[__name__] = _implementation
