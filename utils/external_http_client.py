"""Compatibility alias for :mod:`utils.http.external_client`."""

from __future__ import annotations

import sys

from utils.http import external_client as _implementation

sys.modules[__name__] = _implementation
