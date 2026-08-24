from __future__ import annotations

from typing import Optional


class QQAutoReplyValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class QQAutoReplyTargetsMixin:
    @staticmethod
    def _normalize_target_id(target_id: str) -> str:
        return str(target_id or "").strip()

    @classmethod
    def _validate_group_id(cls, group_id: str) -> str:
        normalized = cls._normalize_target_id(group_id)
        if not normalized:
            raise QQAutoReplyValidationError("INVALID_GROUP_ID", "group_id 不能为空")
        if not normalized.isdigit():
            raise QQAutoReplyValidationError("INVALID_GROUP_ID", "group_id 必须是纯数字")
        return normalized

    @staticmethod
    def _validate_outbound_message(message: str) -> str:
        normalized = str(message or "").strip()
        if not normalized:
            raise QQAutoReplyValidationError("INVALID_MESSAGE", "message 不能为空")
        return normalized

    def _ensure_qq_client_connected(self):
        if not self.qq_client:
            raise RuntimeError("QQ 客户端未初始化")
        if not self.qq_client.is_connected():
            raise RuntimeError("OneBot 未连接，请先启动自动回复")

    def _resolve_private_message_target(self, target: str) -> tuple[str, Optional[str]]:
        normalized = self._normalize_target_id(target)
        if not normalized:
            raise QQAutoReplyValidationError("INVALID_TARGET", "target 不能为空")
        if normalized.isdigit():
            return normalized, None
        if not self.permission_mgr:
            raise RuntimeError("权限管理器未初始化")

        matches = self.permission_mgr.find_users_by_nickname(normalized)
        if not matches:
            raise QQAutoReplyValidationError("NICKNAME_NOT_FOUND", f"昵称 {normalized} 不在信任用户列表中")
        if len(matches) > 1:
            qq_list = ", ".join(user["qq"] for user in matches)
            raise QQAutoReplyValidationError("NICKNAME_AMBIGUOUS", f"昵称 {normalized} 匹配到多个用户: {qq_list}")
        return matches[0]["qq"], normalized
