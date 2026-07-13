"""Compatibility alias for :mod:`utils.tts.native_voice_registry`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.native_voice_registry")
sys.modules[__name__] = _implementation
