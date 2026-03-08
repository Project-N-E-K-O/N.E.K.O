"""SDK v2 adapter surface (contract-only)."""

from __future__ import annotations

from . import base as _base
from . import decorators as _decorators
from . import gateway_contracts as _gateway_contracts
from . import gateway_core as _gateway_core
from . import gateway_defaults as _gateway_defaults
from . import gateway_models as _gateway_models
from . import neko_adapter as _neko_adapter
from . import types as _types
from .base import *
from .decorators import *
from .gateway_contracts import *
from .gateway_core import *
from .gateway_defaults import *
from .gateway_models import *
from .neko_adapter import *
from .types import *

__all__ = list(
    dict.fromkeys(
        [
            *_base.__all__,
            *_types.__all__,
            *_decorators.__all__,
            *_gateway_models.__all__,
            *_gateway_contracts.__all__,
            *_gateway_core.__all__,
            *_gateway_defaults.__all__,
            *_neko_adapter.__all__,
        ]
    )
)
