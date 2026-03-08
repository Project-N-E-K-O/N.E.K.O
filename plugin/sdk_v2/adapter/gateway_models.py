"""Compatibility wrapper for adapter gateway models.

Developer-facing adapter imports should usually come from `sdk_v2.adapter` or
`sdk_v2.adapter.runtime`. The concrete gateway model definitions live in the
internal `public/adapter` layer.
"""

from plugin.sdk_v2.public.adapter.gateway_models import *
from plugin.sdk_v2.public.adapter.gateway_models import __all__
