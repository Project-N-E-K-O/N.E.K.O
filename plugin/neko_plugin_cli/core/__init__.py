"""Core packaging library — platform-independent, no hardcoded paths."""

from .bundle_analysis import analyze_bundle_plugins
from .inspect import inspect_package
from .build import BuildResult, build_bundle, build_plugin
from .install import install_package

__all__ = [
    "BuildResult",
    "analyze_bundle_plugins",
    "inspect_package",
    "build_bundle",
    "build_plugin",
    "install_package",
]
