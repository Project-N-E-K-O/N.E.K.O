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

"""Can this model be shown a picture?

Providers do not expose a capability flag, so this is a name-matching
heuristic and it will occasionally be wrong. That is acceptable only
because every caller has a working fallback: a False answer costs one
extra vision-model round trip, it never disables a feature. Do not build
anything on this that breaks when it guesses low.

Originally lived inside ``study_companion``; moved here once the tool
image channel needed the same judgement, so the two cannot drift apart.
"""
from __future__ import annotations

import re

# GLM names its multimodal line by appending a ``v`` to the version
# (``glm-4v``, ``glm-4.5v``), which no substring match can catch without
# also matching plain ``glm-4``.
_GLM_VISION_SUFFIX = re.compile(r"(?:^|[-_.])\d+(?:\.\d+)?v(?:[-_.]|$)")

_VISION_NAME_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-5",
    "vision",
    "vl",
    "qwen2.5-vl",
    "qwen-vl",
    "gemini",
    "claude-3",
    "claude-4",
)


def model_supports_vision(model: object) -> bool:
    """Best-effort guess at whether ``model`` accepts image input."""
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("glm-") and _GLM_VISION_SUFFIX.search(normalized):
        return True
    return any(marker in normalized for marker in _VISION_NAME_MARKERS)


__all__ = ["model_supports_vision"]
