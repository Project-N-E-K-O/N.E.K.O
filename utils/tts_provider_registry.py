"""Compatibility alias for :mod:`utils.tts.provider_registry`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.provider_registry")
sys.modules[__name__] = _implementation
