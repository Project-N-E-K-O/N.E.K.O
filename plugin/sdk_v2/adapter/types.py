"""Compatibility wrapper for adapter transport and routing types.

Developer-facing adapter imports should usually come from `sdk_v2.adapter`.
Concrete transport/routing type definitions live in the internal
`public/adapter` layer.
"""

from plugin.sdk_v2.public.adapter.types import *
from plugin.sdk_v2.public.adapter.types import __all__
