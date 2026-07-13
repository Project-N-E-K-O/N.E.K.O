"""Compatibility alias for :mod:`utils.storage.location_bootstrap`."""

from __future__ import annotations

import sys

from utils.storage import location_bootstrap as _implementation

sys.modules[__name__] = _implementation
