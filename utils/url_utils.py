"""Compatibility alias for :mod:`utils.http.url`."""

from __future__ import annotations

import sys

from utils.http import url as _implementation

sys.modules[__name__] = _implementation
