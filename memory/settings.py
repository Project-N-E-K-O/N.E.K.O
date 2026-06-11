"""Legacy ``settings.json`` accessor.

历史背景
--------
原本这里同时承载两件事：

1. 维护 ``memory/{name}/settings.json`` 的读写。这部分仍然在用——
   ``memory_server.py`` 和 testbench/dump 工具都会调用 ``get_settings`` /
   ``load_settings`` / ``save_settings`` 来把磁盘上的旧字段合进 prompt。
2. 用 LLM 从对话里抽取新设定 + 用 LLM 跑矛盾消解。这套已经被 evidence /
   reflection 流水线全面取代——见 ``memory_server.py::process_history``
   里 "旧模块已禁用（性能不足）" 的注释；``extract_and_update_settings`` 与
   ``detect_and_resolve_contradictions`` 已经没有任何调用方。

为了避免这两个死方法继续把 ``SETTING_PROPOSER_MODEL`` /
``SETTING_VERIFIER_MODEL`` 这种已退役的硬编码常量留在身上（也避免
项目级 "no temperature" 守门时还得给死代码开口子），本次清理直接把
LLM 路径删掉，只保留磁盘读写。如果未来真的需要重启这套，请走
evidence/reflection 范式，不要复活旧代码。
"""
import json

from config import CHARACTER_RESERVED_FIELDS
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json


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

    def save_settings(self, lanlan_name):
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/settings.json",
        )
        atomic_write_json(
            self.settings_file[lanlan_name],
            self.settings[lanlan_name],
            indent=2,
            ensure_ascii=False,
        )

    def get_settings(self, lanlan_name):
        self.load_settings()
        self.settings[lanlan_name][lanlan_name].update(self.lanlan_basic_config[lanlan_name])
        self.settings[lanlan_name][self.name_mapping['human']].update(self.master_basic_config)
        return self.settings[lanlan_name]
