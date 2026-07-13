"""Compatibility alias for :mod:`utils.http.ssl_diagnostics`."""

from __future__ import annotations

import sys

from utils.http import ssl_diagnostics as _implementation

sys.modules[__name__] = _implementation
