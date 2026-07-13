"""Compatibility alias for :mod:`utils.tts.providers.mimo`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.providers.mimo")
sys.modules[__name__] = _implementation
