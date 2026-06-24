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

"""Drawing Guess minigame prompt data.

The legacy generic prompts_game module was split into per-minigame modules by
upstream. Keep Drawing Guess imports feature-specific so callers do not depend
on the old generic name.
"""

from config.prompts.prompts_soccer import (
    DRAWING_GUESS_WORD_DATA,
    get_drawing_guess_direct_hint_template,
)

__all__ = [
    "DRAWING_GUESS_WORD_DATA",
    "get_drawing_guess_direct_hint_template",
]
