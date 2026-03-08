"""Compatibility layer: `plugin.sdk_v2.public.adapter`.

Deprecated import path. Prefer `plugin.sdk_v2.adapter`.
"""

from __future__ import annotations

from plugin.sdk_v2 import adapter as _adapter

for _name in _adapter.__all__:
    globals()[_name] = getattr(_adapter, _name)

__all__ = list(_adapter.__all__)

del _name
