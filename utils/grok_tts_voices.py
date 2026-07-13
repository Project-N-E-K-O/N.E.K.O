"""Compatibility alias for :mod:`utils.tts.providers.grok`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.providers.grok")
sys.modules[__name__] = _implementation
