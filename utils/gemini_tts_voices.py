"""Compatibility alias for :mod:`utils.tts.providers.gemini`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.providers.gemini")
sys.modules[__name__] = _implementation
