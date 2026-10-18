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

"""Application identity, logging, voice identifiers, and region overrides."""

import logging

# Application name and version configuration
APP_NAME = "N.E.K.O"
APP_VERSION = "0.9.0"
logger = logging.getLogger(f"{APP_NAME}.{__package__}")

# GPT-SoVITS voice_id prefix (character management identifies GPT-SoVITS voices using "gsv:<voice_id>")
GSV_VOICE_PREFIX = "gsv:"

# GeoIP region-detection debug switch (read by ConfigManager._check_non_mainland):
#   None  → Run real detection (HTTP IP geo + Steam geo checks); production default
#   True  → Force non-mainland China (use the lanlan.app free route)
#   False → Force mainland China
# Change this for debugging without modifying config_manager detection logic; retain None in production.
GEOIP_FORCE_NON_MAINLAND = None
