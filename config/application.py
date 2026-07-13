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

# 应用程序名称与版本配置
APP_NAME = "N.E.K.O"
APP_VERSION = "0.8.3"
logger = logging.getLogger(f"{APP_NAME}.{__package__}")

# GPT-SoVITS voice_id 前缀(角色管理中使用 "gsv:<voice_id>" 格式标识 GPT-SoVITS 声音)
GSV_VOICE_PREFIX = "gsv:"

# GeoIP 区域判定的调试开关（ConfigManager._check_non_mainland 读取）：
#   None  → 正常走真实检测（HTTP IP geo + Steam geo 双判），生产默认值
#   True  → 强制判定为非中国大陆（走 lanlan.app 免费路径）
#   False → 强制判定为中国大陆
# 调试时改这里即可，不用动 config_manager 的检测逻辑；上线保持 None。
GEOIP_FORCE_NON_MAINLAND = None
