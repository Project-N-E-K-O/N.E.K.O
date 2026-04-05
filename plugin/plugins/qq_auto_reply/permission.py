"""
权限管理模块

根据 QQ 号管理用户权限等级
"""

from typing import Dict, List, Optional


class PermissionManager:
    """权限管理器"""

    VALID_LEVELS = {"admin", "trusted", "normal"}

    def __init__(self, trusted_users: List[Dict[str, str]] = None):
        """
        初始化权限管理器

        Args:
            trusted_users: 信任用户列表，格式: [{"qq": "123456", "level": "admin", "nickname": "小明"}, ...]
        """
        self._users: Dict[str, str] = {}  # {qq: level}
        self._nicknames: Dict[str, str] = {}  # {qq: nickname}

        if trusted_users:
            for user in trusted_users:
                qq = self._normalize_qq(user.get("qq", ""))
                level = self._normalize_level(user.get("level", "trusted"))
                nickname = user.get("nickname", "")
                if qq:
                    self._users[qq] = level
                    if nickname:
                        self._nicknames[qq] = nickname

    @staticmethod
    def _normalize_qq(qq_number: str) -> str:
        return str(qq_number or "").strip()

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        level_text = str(level or "trusted").strip().lower()
        return level_text if level_text in cls.VALID_LEVELS else "trusted"

    def add_user(self, qq_number: str, level: str = "trusted", nickname: str = ""):
        """
        添加用户

        Args:
            qq_number: QQ 号
            level: 权限等级 (admin, trusted, normal)
            nickname: 用户昵称（可选）
        """
        qq_str = self._normalize_qq(qq_number)
        if not qq_str:
            return
        self._users[qq_str] = self._normalize_level(level)
        if nickname:
            self._nicknames[qq_str] = nickname
        elif qq_str in self._nicknames:
            del self._nicknames[qq_str]

    def remove_user(self, qq_number: str):
        """移除用户"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str in self._users:
            del self._users[qq_str]
        if qq_str in self._nicknames:
            del self._nicknames[qq_str]

    def get_permission_level(self, qq_number: str) -> str:
        """
        获取用户权限等级

        Args:
            qq_number: QQ 号

        Returns:
            权限等级: admin, trusted, normal, none
        """
        qq_str = self._normalize_qq(qq_number)
        return self._users.get(qq_str, "none")

    def list_users(self) -> List[Dict[str, str]]:
        """列出所有用户"""
        result = []
        for qq, level in self._users.items():
            user_info = {"qq": qq, "level": level}
            if qq in self._nicknames:
                user_info["nickname"] = self._nicknames[qq]
            result.append(user_info)
        return result

    def get_nickname(self, qq_number: str) -> Optional[str]:
        """获取用户昵称"""
        return self._nicknames.get(self._normalize_qq(qq_number))

    def set_nickname(self, qq_number: str, nickname: str):
        """设置用户昵称"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str in self._users:
            if nickname:
                self._nicknames[qq_str] = nickname
            else:
                if qq_str in self._nicknames:
                    del self._nicknames[qq_str]
            return True
        return False

    def is_admin(self, qq_number: str) -> bool:
        """检查是否是管理员"""
        return self.get_permission_level(qq_number) == "admin"

    def is_trusted(self, qq_number: str) -> bool:
        """检查是否是信任用户（包括管理员）"""
        level = self.get_permission_level(qq_number)
        return level in ["admin", "trusted"]
