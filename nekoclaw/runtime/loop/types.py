# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/types.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Shared low-level types for the portable agent layer."""

from __future__ import annotations

from typing import Union

from typing_extensions import TypeAliasType

# Upstream declares these with PEP 695 ``type`` statements (Python 3.12+).
# This repository pins Python 3.11, so the aliases are plain assignments and
# the recursive one is a named ``TypeAliasType`` that pydantic can resolve.
JSONPrimitive = Union[str, int, float, bool, None]
JSONValue = TypeAliasType(
    "JSONValue",
    "Union[str, int, float, bool, None, list[JSONValue], dict[str, JSONValue]]",
)
JSONObject = dict[str, JSONValue]
