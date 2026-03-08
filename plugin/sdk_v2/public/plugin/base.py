"""Plugin flavor base exports."""

from plugin.sdk_v2.shared.core import base as _shared_base

for _export_name in _shared_base.__all__:
    globals()[_export_name] = getattr(_shared_base, _export_name)

__all__ = list(_shared_base.__all__)
