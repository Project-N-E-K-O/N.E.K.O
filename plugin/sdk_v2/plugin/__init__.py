"""Plugin-side SDK v2 surface.

Primary import target for standard plugin development.
"""

from __future__ import annotations
from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime

for _name in _base.__all__:
    globals()[_name] = getattr(_base, _name)

for _name in _decorators.__all__:
    globals()[_name] = getattr(_decorators, _name)

for _name in _runtime.__all__:
    globals()[_name] = getattr(_runtime, _name)

__all__ = list(dict.fromkeys([*_base.__all__, *_decorators.__all__, *_runtime.__all__]))

del _name
