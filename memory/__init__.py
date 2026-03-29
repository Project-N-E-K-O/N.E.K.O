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
    """返回角色专属目录 memory_dir/{name}/，不存在则创建。"""
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
    """一次性迁移：将旧的 memory_dir/{type}_{name}.ext 移入 memory_dir/{name}/{type}.ext"""
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
