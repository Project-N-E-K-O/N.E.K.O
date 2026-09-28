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

"""Legacy ``settings.json`` accessor (read-only).

History
--------
This module originally carried two responsibilities:

1. Reading ``memory/{name}/settings.json``. Still in use —
   ``app/memory_server/routes.py`` and the testbench/dump tools call ``get_settings`` /
   ``load_settings`` to merge the legacy on-disk fields into the prompt.
2. Using an LLM to extract new settings from conversations + run LLM
   contradiction resolution. Fully superseded by the evidence / reflection
   pipeline; ``extract_and_update_settings`` and
   ``detect_and_resolve_contradictions`` had no callers left.

To keep these two dead methods from dragging along retired hard-coded
constants like ``SETTING_PROPOSER_MODEL`` / ``SETTING_VERIFIER_MODEL`` (and to
avoid carving out dead-code exceptions in the project-wide "no temperature"
gate), this cleanup removes the LLM paths and the write-side disk IO outright
and keeps only the disk read. ``settings.json`` is now a frozen legacy import
source — cloudsave may sync it but the app no longer writes back.
"""
import json

from config import CHARACTER_RESERVED_FIELDS
from utils.config_manager import get_config_manager


class ImportantSettingsManager:
    def __init__(self):
        self.settings = {}
        self.settings_file = None
        self._config_manager = get_config_manager()
        self._excluded_profile_fields = set(CHARACTER_RESERVED_FIELDS)

    def load_settings(self):
        # It is important to update the settings with the latest character on-disk files
        _, _, master_basic_config, lanlan_basic_config, name_mapping, _, _, setting_store, _ = self._config_manager.get_character_data()
        self.settings_file = setting_store
        self.master_basic_config = master_basic_config
        self.lanlan_basic_config = lanlan_basic_config
        self.name_mapping = name_mapping

        for i in self.settings_file:
            try:
                # 角色档案保留字段不参与记忆提取
                for reserved_field in self._excluded_profile_fields:
                    self.lanlan_basic_config[i].pop(reserved_field, None)
                with open(self.settings_file[i], 'r', encoding='utf-8') as f:
                    self.settings[i] = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self.settings[i] = {i: {}, self.name_mapping['human']: {}}

    def get_settings(self, lanlan_name):
        self.load_settings()
        self.settings[lanlan_name][lanlan_name].update(self.lanlan_basic_config[lanlan_name])
        self.settings[lanlan_name][self.name_mapping['human']].update(self.master_basic_config)
        return self.settings[lanlan_name]
