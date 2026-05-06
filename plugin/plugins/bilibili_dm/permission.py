"""
B站私信权限管理模块

根据 B站 UID 管理用户权限等级，支持 admin / trusted / normal / none 四级。
"""

from typing import Dict, List, Optional


class PermissionManager:
    """B站私信权限管理器"""

    VALID_LEVELS = {"admin", "trusted", "normal"}

    def __init__(self, trusted_users: List[Dict[str, str]] = None):
        """
        初始化权限管理器

        Args:
            trusted_users: 信任用户列表，格式: [{"uid": "12345", "level": "admin", "nickname": "小明"}, ...]
        """
        self._users: Dict[str, str] = {}      # {uid: level}
        self._nicknames: Dict[str, str] = {}   # {uid: nickname}

        if trusted_users:
            for user in trusted_users:
                uid = self._normalize_uid(user.get("uid", ""))
                level = self._normalize_level(user.get("level", "trusted"))
                nickname = user.get("nickname", "")
                if uid:
                    self._users[uid] = level
                    if nickname:
                        self._nicknames[uid] = nickname

    @staticmethod
    def _normalize_uid(uid: str) -> str:
        return str(uid or "").strip()

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        level_text = str(level or "trusted").strip().lower()
        return level_text if level_text in cls.VALID_LEVELS else "trusted"

    def add_user(self, uid: str, level: str = "trusted", nickname: str = ""):
        """添加用户"""
        uid_str = self._normalize_uid(uid)
        if not uid_str:
            return
        self._users[uid_str] = self._normalize_level(level)
        if nickname:
            self._nicknames[uid_str] = nickname
        elif uid_str in self._nicknames:
            del self._nicknames[uid_str]

    def remove_user(self, uid: str):
        """移除用户"""
        uid_str = self._normalize_uid(uid)
        if uid_str in self._users:
            del self._users[uid_str]
        if uid_str in self._nicknames:
            del self._nicknames[uid_str]

    def get_permission_level(self, uid: str) -> str:
        """
        获取用户权限等级

        Returns:
            权限等级: admin, trusted, normal, none
        """
        uid_str = self._normalize_uid(uid)
        return self._users.get(uid_str, "none")

    def list_users(self) -> List[Dict[str, str]]:
        """列出所有用户"""
        result = []
        for uid, level in self._users.items():
            user_info = {"uid": uid, "level": level}
            if uid in self._nicknames:
                user_info["nickname"] = self._nicknames[uid]
            result.append(user_info)
        return result

    def get_nickname(self, uid: str) -> Optional[str]:
        """获取用户昵称"""
        return self._nicknames.get(self._normalize_uid(uid))

    def set_nickname(self, uid: str, nickname: str) -> bool:
        """设置用户昵称"""
        uid_str = self._normalize_uid(uid)
        if uid_str in self._users:
            if nickname:
                self._nicknames[uid_str] = nickname
            else:
                if uid_str in self._nicknames:
                    del self._nicknames[uid_str]
            return True
        return False

    def is_admin(self, uid: str) -> bool:
        """检查是否是管理员"""
        return self.get_permission_level(uid) == "admin"

    def is_trusted(self, uid: str) -> bool:
        """检查是否是信任用户（包括管理员）"""
        level = self.get_permission_level(uid)
        return level in ("admin", "trusted")

    def should_process(self, uid: str, permission_mode: str = "allow_list") -> bool:
        """
        根据权限模式判断是否应处理该用户的消息

        allow_list 模式：只处理白名单中的用户
        deny_list 模式：处理所有用户，除了黑名单中的
        open 模式：处理所有用户
        """
        level = self.get_permission_level(uid)
        if permission_mode == "allow_list":
            return level != "none"
        elif permission_mode == "deny_list":
            return level != "normal"
        return True
