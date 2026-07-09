from __future__ import annotations

from typing import Any, Optional


class QQPromptBuilder:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def build_user_title(
        self,
        *,
        permission_level: str,
        sender_id: str,
        master_name: str,
        custom_nickname: str | None,
        user_nickname: str | None,
        is_group: bool,
    ) -> str:
        if is_group:
            if custom_nickname:
                return custom_nickname
            if user_nickname:
                return user_nickname
            return self.plugin.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=sender_id)
        if permission_level == "admin":
            return master_name if master_name else self.plugin.i18n.t("prompts.default_master", default="主人")
        if custom_nickname:
            return custom_nickname
        if user_nickname:
            return user_nickname
        return self.plugin.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=sender_id)

    def build_character_card_fields(self, current_character: dict[str, Any]) -> dict[str, Any]:
        character_card_fields: dict[str, Any] = {}
        for key, value in current_character.items():
            if key not in [
                "_reserved", "voice_id", "system_prompt", "model_type",
                "live2d", "vrm", "vrm_animation", "lighting", "vrm_rotation",
                "live2d_item_id", "item_id", "idleAnimation",
            ]:
                if isinstance(value, (str, int, float, bool)) and value:
                    character_card_fields[key] = value
        return character_card_fields

    def should_use_memory_context(self, *, is_group: bool, permission_level: str, requested: Optional[bool]) -> bool:
        if requested is None:
            return (not is_group and permission_level == "admin")
        return bool(requested)

    def should_persist_memory(self, *, should_use_memory_context: bool, requested: Optional[bool]) -> bool:
        if requested is None:
            return should_use_memory_context
        return bool(requested)

    def build_prompt_message(
        self,
        *,
        is_group: bool,
        group_facing: bool,
        group_scene_mode: str,
        user_title: str,
        sender_id: str,
        group_id: str | None,
        message: str,
        current_message_id: str = "",
    ) -> str:
        if is_group and not group_facing:
            return self.plugin._build_group_turn_message(
                group_scene_mode=group_scene_mode,
                user_title=user_title,
                sender_id=sender_id,
                group_id=group_id,
                message=message,
                current_message_id=current_message_id,
            )
        return message
