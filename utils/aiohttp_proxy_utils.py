"""Compatibility alias for :mod:`utils.http.aiohttp_proxy`."""

from __future__ import annotations

import sys

from utils.http import aiohttp_proxy as _implementation

sys.modules[__name__] = _implementation
