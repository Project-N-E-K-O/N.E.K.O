"""SDK v2 adapter surface (contract-only)."""

from __future__ import annotations
from __future__ import annotations

from . import base as _base
from . import types as _types
from . import decorators as _decorators
from . import runtime as _runtime

for _name in _base.__all__:
    globals()[_name] = getattr(_base, _name)

for _name in _types.__all__:
    globals()[_name] = getattr(_types, _name)

for _name in _decorators.__all__:
    globals()[_name] = getattr(_decorators, _name)

for _name in _runtime.__all__:
    globals()[_name] = getattr(_runtime, _name)

from .neko_adapter import NekoAdapterPlugin

__all__ = list(dict.fromkeys([*_base.__all__, *_types.__all__, *_decorators.__all__, *_runtime.__all__, "NekoAdapterPlugin"]))

del _name
