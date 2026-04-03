"""
群聊权限管理模块

管理信任的 QQ 群聊
"""

from typing import Dict, List


class GroupPermissionManager:
    """群聊权限管理器"""

    def __init__(self, trusted_groups: List[Dict[str, str]] = None):
        """
        初始化群聊权限管理器

        Args:
            trusted_groups: 信任群聊列表，格式: [{"group_id": "123456", "level": "trusted"}, ...]
        """
        self._groups: Dict[str, str] = {}  # {group_id: level}

        if trusted_groups:
            for group in trusted_groups:
                group_id = group.get("group_id", "")
                level = group.get("level", "normal")
                if group_id:
                    self._groups[group_id] = level

    def add_group(self, group_id: str, level: str = "normal"):
        """
        添加群聊

        Args:
            group_id: 群号
            level: 权限等级 (trusted, normal)
        """
        if level not in ["trusted", "normal"]:
            level = "normal"
        self._groups[group_id] = level

    def remove_group(self, group_id: str):
        """移除群聊"""
        if group_id in self._groups:
            del self._groups[group_id]

    def get_group_level(self, group_id: str) -> str:
        """
        获取群聊权限等级

        Args:
            group_id: 群号

        Returns:
            权限等级: trusted, normal, none
        """
        group_str = str(group_id)
        return self._groups.get(group_str, "none")

    def is_trusted_group(self, group_id: str) -> bool:
        """检查是否是信任群聊"""
        return self.get_group_level(str(group_id)) == "trusted"

    def is_allowed_group(self, group_id: str) -> bool:
        """检查群聊是否被允许（信任或普通）"""
        level = self.get_group_level(str(group_id))
        return level in ["trusted", "normal"]

    def list_groups(self) -> List[Dict[str, str]]:
        """列出所有群聊"""
        return [{"group_id": group_id, "level": level} for group_id, level in self._groups.items()]
