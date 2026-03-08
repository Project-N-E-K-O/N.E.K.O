"""SDK v2 root namespace.

Plugin-facing surfaces:
- plugin
- extension
- adapter
- shared (advanced)
"""

from . import adapter, extension, plugin, shared

__all__ = ["plugin", "extension", "adapter", "shared"]
