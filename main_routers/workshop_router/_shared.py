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

"""Shared APIRouter instance and logger for the workshop_router package.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from fastapi import APIRouter
from utils.logger_config import get_module_logger


router = APIRouter(prefix="/api/steam/workshop", tags=["workshop"])


logger = get_module_logger(__name__, "Main")
