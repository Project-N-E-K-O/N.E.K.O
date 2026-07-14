"""Compatibility alias for :mod:`utils.storage.path_rewrite`."""

from __future__ import annotations

import sys

from utils.storage import path_rewrite as _implementation

sys.modules[__name__] = _implementation
