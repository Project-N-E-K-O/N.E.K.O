"""
群聊权限管理模块

管理允许自动回复的 QQ 群聊
"""

from typing import Any, Dict, List, Optional


class GroupPermissionManager:
    """群聊权限管理器"""

    VALID_LEVELS = {"trusted", "open", "normal"}
    LEGACY_LEVEL_ALIASES = {"truth": "open"}

    def __init__(self, trusted_groups: List[Dict[str, Any]] = None):
        """
        初始化群聊权限管理器

        Args:
            trusted_groups: 群聊列表，格式: [{"group_id": "123456", "level": "trusted"}, ...]
        """
        self._groups: Dict[str, Dict[str, Any]] = {}

        if trusted_groups:
            for group in trusted_groups:
                group_id = str(group.get("group_id", "") or "").strip()
                level = group.get("level", "normal")
                if group_id:
                    self._groups[group_id] = {
                        "level": self._normalize_level(level),
                        "normal_relay_probability": self._normalize_probability(group.get("normal_relay_probability")),
                        "open_reply_probability": self._normalize_probability(group.get("open_reply_probability")),
                    }

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        normalized = str(level or "normal").strip().lower()
        normalized = cls.LEGACY_LEVEL_ALIASES.get(normalized, normalized)
        return normalized if normalized in cls.VALID_LEVELS else "normal"

    @staticmethod
    def _normalize_probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            normalized = float(value)
        except Exception:
            return None
        if normalized < 0.0 or normalized > 1.0:
            return None
        return normalized

    def add_group(self, group_id: str, level: str = "normal", normal_relay_probability: Any = None, open_reply_probability: Any = None):
        """
        添加群聊

        Args:
            group_id: 群号
            level: 权限等级 (trusted, open, normal)
            normal_relay_probability: normal 群命中后转发给主人时使用的概率
            open_reply_probability: open 群主动回复时使用的概率
"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        self._groups[normalized_group_id] = {
            "level": self._normalize_level(level),
            "normal_relay_probability": self._normalize_probability(normal_relay_probability),
            "open_reply_probability": self._normalize_probability(open_reply_probability),
        }

    def remove_group(self, group_id: str):
        """移除群聊"""
        normalized_group_id = str(group_id or "").strip()
        if normalized_group_id in self._groups:
            del self._groups[normalized_group_id]

    def get_group_level(self, group_id: str) -> str:
        """
        获取群聊权限等级

        Args:
            group_id: 群号

        Returns:
            权限等级: trusted, open, normal, none
        """
        group_str = str(group_id or "").strip()
        group = self._groups.get(group_str) or {}
        return str(group.get("level") or "none")

    def get_normal_relay_probability(self, group_id: str) -> Optional[float]:
        group = self._groups.get(str(group_id or "").strip()) or {}
        return self._normalize_probability(group.get("normal_relay_probability"))

    def get_open_reply_probability(self, group_id: str) -> Optional[float]:
        group = self._groups.get(str(group_id or "").strip()) or {}
        return self._normalize_probability(group.get("open_reply_probability"))

    def is_trusted_group(self, group_id: str) -> bool:
        """检查是否是 @ 后回复的信任群聊"""
        return self.get_group_level(str(group_id)) == "trusted"

    def is_allowed_group(self, group_id: str) -> bool:
        """检查群聊是否被允许（信任、开放或普通）"""
        level = self.get_group_level(str(group_id))
        return level in self.VALID_LEVELS

    def list_groups(self) -> List[Dict[str, Any]]:
        """列出所有群聊"""
        result: List[Dict[str, Any]] = []
        for group_id, group in self._groups.items():
            item: Dict[str, Any] = {"group_id": group_id, "level": group.get("level", "normal")}
            normal_relay_probability = self._normalize_probability(group.get("normal_relay_probability"))
            open_reply_probability = self._normalize_probability(group.get("open_reply_probability"))
            if normal_relay_probability is not None:
                item["normal_relay_probability"] = normal_relay_probability
            if open_reply_probability is not None:
                item["open_reply_probability"] = open_reply_probability
            result.append(item)
        return result
