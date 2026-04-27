"""Memory subsystem.

⚠️ LLM 调用约定（项目级硬性规则）
================================
**memory/ 与 utils/ 下走 ``utils.llm_client.create_chat_llm`` /
``ChatOpenAI`` 的任何调用：**

1. **不要传 ``temperature=...``**。两者默认 ``None``（不写进请求体），由模型端
   按其自家默认行为响应。这条规则同样适用于任何包装 helper（例如
   ``FactStore._allm_call_with_retries`` 历史上接受过 ``temperature=``，已删除）。
   理由：(1) 兼容 o1/o3/gpt-5-thinking/Claude extended-thinking 这类拒收该
   参数的模型；(2) 各 task 自定温度（0.1/0.2/0.3/0.5/1.0）会引入难复现的回归。
   守门：``scripts/check_no_temperature.py``（CI: ``.github/workflows/analyze.yml``）。

2. **模型从 tier 拿，不要 hardcoded fallback**。每个 LLM 调用都通过
   ``self._config_manager.get_model_api_config('summary'|'correction'|'emotion'|'vision'|...)``
   取 ``api_config['model'] / ['base_url'] / ['api_key']`` 三件套。**不**要再写
   ``api_config.get('model', SETTING_PROPOSER_MODEL)`` 这种 fallback——那是退役
   的老硬编码（``SETTING_PROPOSER_MODEL`` / ``SETTING_VERIFIER_MODEL`` 已于
   2026-04 退环境）。如果 tier 没配置好，``api_config['model']`` 是 ``''``，请求会
   被 API 显式拒绝；这是配置错误，应该直接暴露，不应该被静默回退到 qwen-max
   掩盖。

3. **memory 子模块走的 tier**：现役 LLM 路径全部跑在 ``summary`` 或 ``correction``
   tier 上（fact extraction / signal detection / reflection synthesis /
   fact dedup / recall rerank → ``summary``；recent.review +
   persona.correction + promotion merge → ``correction``）。不要再引入新的
   hardcoded 模型名字。

如果有非常具体的理由需要绕过，先删 ``scripts/check_no_temperature.py`` 并在
PR 描述里说明，由 reviewer 把关。
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
