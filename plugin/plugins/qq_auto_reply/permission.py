"""
权限管理模块

根据 QQ 号管理用户权限等级
"""

from typing import Any, Dict, List, Optional


class PermissionManager:
    """权限管理器"""

    VALID_LEVELS = {"admin", "trusted", "normal"}
    NICKNAME_MAX_CHARS = 64
    _NICKNAME_FORBIDDEN_CHARS = frozenset("[]|")
    _NICKNAME_ALLOWED_FORMAT_CHARS = frozenset({"\u200d"})

    def __init__(self, trusted_users: List[Dict[str, Any]] = None):
        """
        初始化权限管理器

        Args:
            trusted_users: 信任用户列表，格式: [{"qq": "123456", "level": "admin", "nickname": "小明"}, ...]
        """
        self._users: Dict[str, Dict[str, Any]] = {}  # {qq: {level, nickname?, normal_relay_probability?}}

        if trusted_users:
            for user in trusted_users:
                qq = self._normalize_qq(user.get("qq", ""))
                level = self._normalize_level(user.get("level", "trusted"))
                nickname = str(user.get("nickname", "") or "").strip()
                normal_relay_probability = self._normalize_probability(user.get("normal_relay_probability"))
                if qq:
                    self._users[qq] = {
                        "level": level,
                        "nickname": nickname,
                        "normal_relay_probability": normal_relay_probability,
                    }

    @staticmethod
    def _normalize_qq(qq_number: str) -> str:
        return str(qq_number or "").strip()

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        level_text = str(level or "trusted").strip().lower()
        return level_text if level_text in cls.VALID_LEVELS else "trusted"

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

    @classmethod
    def _normalize_nickname_for_write(cls, nickname: str) -> Optional[str]:
        raw = str(nickname or "")
        normalized = raw.strip()
        if len(normalized) > cls.NICKNAME_MAX_CHARS:
            return None
        if any(
            char in cls._NICKNAME_FORBIDDEN_CHARS
            or (
                not char.isprintable()
                and char not in cls._NICKNAME_ALLOWED_FORMAT_CHARS
            )
            for char in raw
        ):
            return None
        return normalized

    def add_user(self, qq_number: str, level: str = "trusted", nickname: str = "", normal_relay_probability: Any = None):
        """
        添加用户

        Args:
            qq_number: QQ 号
            level: 权限等级 (admin, trusted, normal)
            nickname: 用户昵称（可选）
        """
        qq_str = self._normalize_qq(qq_number)
        if not qq_str:
            return False
        normalized_level = self._normalize_level(level)
        normalized_nickname = (
            "" if normalized_level == "admin"
            else self._normalize_nickname_for_write(nickname)
        )
        if normalized_nickname is None:
            return False
        self._users[qq_str] = {
            "level": normalized_level,
            "nickname": normalized_nickname,
            "normal_relay_probability": self._normalize_probability(normal_relay_probability),
        }
        return True

    def remove_user(self, qq_number: str):
        """移除用户"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str in self._users:
            del self._users[qq_str]

    def get_permission_level(self, qq_number: str) -> str:
        """
        获取用户权限等级

        Args:
            qq_number: QQ 号

        Returns:
            权限等级: admin, trusted, normal, none
        """
        qq_str = self._normalize_qq(qq_number)
        user = self._users.get(qq_str) or {}
        return str(user.get("level") or "none")

    def list_users(self) -> List[Dict[str, Any]]:
        """列出所有用户"""
        result = []
        for qq, user in self._users.items():
            user_info: Dict[str, Any] = {"qq": qq, "level": user.get("level", "trusted")}
            nickname = str(user.get("nickname") or "").strip()
            if nickname:
                user_info["nickname"] = nickname
            probability = self._normalize_probability(user.get("normal_relay_probability"))
            if probability is not None:
                user_info["normal_relay_probability"] = probability
            result.append(user_info)
        return result

    def get_nickname(self, qq_number: str) -> Optional[str]:
        """获取用户昵称"""
        user = self._users.get(self._normalize_qq(qq_number)) or {}
        nickname = str(user.get("nickname") or "").strip()
        return nickname or None

    def get_normal_relay_probability(self, qq_number: str) -> Optional[float]:
        user = self._users.get(self._normalize_qq(qq_number)) or {}
        return self._normalize_probability(user.get("normal_relay_probability"))

    def set_nickname(self, qq_number: str, nickname: str):
        """设置用户昵称"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str not in self._users:
            return False
        normalized = self._normalize_nickname_for_write(nickname)
        if normalized is None:
            return False
        self._users[qq_str]["nickname"] = normalized
        return True

    def find_users_by_nickname(self, nickname: str) -> List[Dict[str, Any]]:
        """按配置昵称查找用户（精确匹配）"""
        target = str(nickname or "").strip()
        if not target:
            return []
        result = []
        for qq, user in self._users.items():
            saved_nickname = str(user.get("nickname") or "").strip()
            if saved_nickname == target:
                user_info: Dict[str, Any] = {"qq": qq, "level": user.get("level", "trusted"), "nickname": saved_nickname}
                probability = self._normalize_probability(user.get("normal_relay_probability"))
                if probability is not None:
                    user_info["normal_relay_probability"] = probability
                result.append(user_info)
        return result

    def is_admin(self, qq_number: str) -> bool:
        """检查是否是管理员"""
        return self.get_permission_level(qq_number) == "admin"

    def is_trusted(self, qq_number: str) -> bool:
        """检查是否是信任用户（包括管理员）"""
        level = self.get_permission_level(qq_number)
        return level in ["admin", "trusted"]
