"""Compatibility wrapper for adapter gateway core.

Developer-facing adapter imports should usually come from `sdk_v2.adapter` or
`sdk_v2.adapter.runtime`. The concrete gateway core contract lives in the
internal `public/adapter` layer.
"""

from plugin.sdk_v2.public.adapter.gateway_core import *
from plugin.sdk_v2.public.adapter.gateway_core import __all__
