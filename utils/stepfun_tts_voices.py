"""Compatibility alias for :mod:`utils.tts.providers.stepfun`."""

from importlib import import_module
import sys

_implementation = import_module("utils.tts.providers.stepfun")
sys.modules[__name__] = _implementation
