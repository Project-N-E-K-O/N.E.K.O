# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Context-local LLM call-type state."""

from contextlib import contextmanager
from contextvars import ContextVar

_current_call_type: ContextVar[str] = ContextVar('_llm_call_type', default='unknown')

@contextmanager
def llm_call_context(call_type: str):
    """Context manager that tags the current LLM call type within the block."""
    token = _current_call_type.set(call_type)
    try:
        yield
    finally:
        _current_call_type.reset(token)

def set_call_type(call_type: str):
    """Simply set the current call type (for scenarios where wrapping is inconvenient)."""
    _current_call_type.set(call_type)
