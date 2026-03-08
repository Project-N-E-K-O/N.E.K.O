"""Compatibility wrapper for adapter gateway contracts.

Developer-facing adapter imports should usually come from `sdk_v2.adapter` or
`sdk_v2.adapter.runtime`. The concrete gateway contract definitions live in the
internal `public/adapter` layer.
"""

from plugin.sdk_v2.public.adapter.gateway_contracts import *
from plugin.sdk_v2.public.adapter.gateway_contracts import __all__
