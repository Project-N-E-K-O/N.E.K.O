"""Compatibility alias for :mod:`utils.http.internal_client`."""

from __future__ import annotations

import sys

from utils.http import internal_client as _implementation

sys.modules[__name__] = _implementation
