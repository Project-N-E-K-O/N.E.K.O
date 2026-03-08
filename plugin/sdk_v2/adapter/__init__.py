"""SDK v2 adapter surface (contract-only)."""

from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime
from . import types as _types
from .base import *
from .decorators import *
from .runtime import *
from .types import *
from .neko_adapter import NekoAdapterPlugin

__all__ = list(dict.fromkeys([*_base.__all__, *_types.__all__, *_decorators.__all__, *_runtime.__all__, "NekoAdapterPlugin"]))
