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

"""Lightweight time formatting helpers shared by memory_server / main_logic and friends."""

from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    ELAPSED_TIME_DH, ELAPSED_TIME_D,
    ELAPSED_TIME_HM, ELAPSED_TIME_H, ELAPSED_TIME_M,
)


def format_elapsed(lang: str, gap_seconds: float) -> str:
    """Pick a time format template (days/hours/minutes, omitting zero-value units) based on the interval in seconds."""
    days = int(gap_seconds // 86400)
    hours = int((gap_seconds % 86400) // 3600)
    minutes = int((gap_seconds % 3600) // 60)
    if days > 0:
        if hours > 0:
            return _loc(ELAPSED_TIME_DH, lang).format(d=days, h=hours)
        else:
            return _loc(ELAPSED_TIME_D, lang).format(d=days)
    elif hours > 0 and hours < 3 and minutes > 0:
        return _loc(ELAPSED_TIME_HM, lang).format(h=hours, m=minutes)
    elif hours > 0:
        return _loc(ELAPSED_TIME_H, lang).format(h=hours)
    else:
        return _loc(ELAPSED_TIME_M, lang).format(m=minutes)
