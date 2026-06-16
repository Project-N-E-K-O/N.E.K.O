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

"""Memory subsystem.

⚠️ LLM call conventions (project-level hard rules)
================================
**Any call in memory/ and utils/ going through ``utils.llm_client.create_chat_llm`` /
``ChatOpenAI``:**

1. **Do not pass ``temperature=...``**. Both default to ``None`` (not written into the
   request body), letting the model endpoint respond with its own default behavior. The
   same rule applies to any wrapper helper (e.g. ``FactStore._allm_call_with_retries``
   historically accepted ``temperature=``; it has been removed).
   Rationale: (1) compatibility with models that reject the parameter, such as
   o1/o3/gpt-5-thinking/Claude extended-thinking; (2) per-task custom temperatures
   (0.1/0.2/0.3/0.5/1.0) introduce hard-to-reproduce regressions.
   Gatekeeper: ``scripts/check_no_temperature.py`` (CI: ``.github/workflows/analyze.yml``).

2. **Models come from tiers; no hardcoded fallbacks**. Every LLM call goes through
   ``self._config_manager.get_model_api_config('summary'|'correction'|'emotion'|'vision'|...)``
   to fetch the ``api_config['model'] / ['base_url'] / ['api_key']`` triple. Do **not**
   write fallbacks like ``api_config.get('model', SETTING_PROPOSER_MODEL)`` — those are
   retired hardcodes (``SETTING_PROPOSER_MODEL`` / ``SETTING_VERIFIER_MODEL`` were
   decommissioned in 2026-04). If the tier isn't configured, ``api_config['model']`` is
   ``''`` and the request is explicitly rejected by the API; that is a configuration
   error which should surface directly, not be silently masked by a qwen-max fallback.

3. **Tiers used by memory submodules**: all active LLM paths run on the ``summary`` or
   ``correction`` tier (fact extraction / signal detection / reflection synthesis /
   fact dedup / recall rerank → ``summary``; recent.review +
   persona.correction + promotion merge → ``correction``). Do not introduce new
   hardcoded model names.

If you have a very specific reason to bypass this, delete
``scripts/check_no_temperature.py`` first and explain it in the PR description for the
reviewer to judge.
"""
import os
import shutil
import logging

from .recent import CompressedRecentHistoryManager
from .settings import ImportantSettingsManager
from .timeindex import TimeIndexedMemory
from .facts import FactStore
from .persona import PersonaManager
from .reflection import ReflectionEngine

_logger = logging.getLogger(__name__)


def ensure_character_dir(memory_dir: str, name: str) -> str:
    """Return the character-specific directory memory_dir/{name}/, creating it if missing."""
    char_dir = os.path.join(str(memory_dir), name)
    os.makedirs(char_dir, exist_ok=True)
    return char_dir


# 旧文件名 → 新文件名的映射（不含 name 后缀）
_MIGRATION_MAP = {
    'facts_{name}.json':                'facts.json',
    'persona_{name}.json':              'persona.json',
    'persona_corrections_{name}.json':  'persona_corrections.json',
    'reflections_{name}.json':          'reflections.json',
    'surfaced_{name}.json':             'surfaced.json',
    'settings_{name}.json':             'settings.json',
    'recent_{name}.json':               'recent.json',
    'time_indexed_{name}':              'time_indexed.db',
}


def migrate_to_character_dirs(memory_dir: str, names: list[str]) -> None:
    """One-time migration: move legacy memory_dir/{type}_{name}.ext into memory_dir/{name}/{type}.ext"""
    memory_dir = str(memory_dir)
    for name in names:
        char_dir = ensure_character_dir(memory_dir, name)
        for old_pattern, new_filename in _MIGRATION_MAP.items():
            old_filename = old_pattern.replace('{name}', name)
            old_path = os.path.join(memory_dir, old_filename)
            new_path = os.path.join(char_dir, new_filename)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    shutil.move(old_path, new_path)
                    _logger.info(f"[Memory] 迁移 {old_filename} → {name}/{new_filename}")
                except Exception as e:
                    _logger.warning(f"[Memory] 迁移失败 {old_filename}: {e}")
