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

"""Shared infrastructure for the memory_server package.

Dependency root: this module must not import any sibling submodule, so
every submodule (and the package ``__init__``) can safely do
``from ._shared import logger`` without ordering concerns.
"""

import logging

from fastapi import HTTPException

from utils.character_name import validate_character_name
from utils.logger_config import setup_logging

logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)


def validate_lanlan_name(name: str) -> str:
    result = validate_character_name(name, allow_dots=True, max_length=50)
    if result.code in {"empty", "too_long_length"}:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name length")
    if result.code is not None:
        raise HTTPException(status_code=400, detail="Invalid characters in lanlan_name")
    return result.normalized
