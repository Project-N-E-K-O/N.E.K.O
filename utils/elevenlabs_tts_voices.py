"""Compatibility alias for :mod:`utils.tts.providers.elevenlabs`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.providers.elevenlabs")
sys.modules[__name__] = _implementation
