"""Shared SDK v2 building blocks.

`shared` contains reusable lower-level primitives. Some subpackages already have
real implementations, while others are still contract-only.
"""

from . import bus, compat, constants, core, logging, models, runtime, storage, transport

IMPLEMENTATION_STATUS = {
    "core": "mixed",
    "bus": "contract-only",
    "storage": "facade",
    "runtime": "facade",
    "transport": "facade",
    "models": "implemented",
    "compat": "implemented",
    "constants": "implemented",
    "logging": "implemented",
}

__all__ = [
    "core",
    "bus",
    "storage",
    "runtime",
    "transport",
    "models",
    "compat",
    "constants",
    "logging",
    "IMPLEMENTATION_STATUS",
]
