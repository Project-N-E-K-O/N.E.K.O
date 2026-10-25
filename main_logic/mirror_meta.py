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

"""Re-export of :mod:`utils.mirror_meta` for existing ``main_logic`` importers.

The mirror-channel schema moved to ``utils.mirror_meta`` so that ``services``
can build mirror metadata without importing ``main_logic``. The names below
are the very same objects, not copies.

Patch ``utils.mirror_meta``, not this module: the helpers look each other up
in ``utils.mirror_meta``, so replacing a name here does not reach them.
"""
from __future__ import annotations

from utils.mirror_meta import (
    MIRROR_USER_INPUT_TYPES,
    MIRROR_USER_TEXT_INPUT_TYPE,
    MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE,
    build_mirror_meta,
    is_mirror_assistant_message,
    is_mirror_event_memory_disabled,
    is_mirror_turn_end_meta,
)

__all__ = [
    "MIRROR_USER_INPUT_TYPES",
    "MIRROR_USER_TEXT_INPUT_TYPE",
    "MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE",
    "build_mirror_meta",
    "is_mirror_assistant_message",
    "is_mirror_event_memory_disabled",
    "is_mirror_turn_end_meta",
]
