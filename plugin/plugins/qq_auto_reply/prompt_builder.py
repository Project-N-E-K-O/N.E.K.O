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
            if is_group:
                # 群路径的 None 默认跟配置走：normal 群路径由 dispatcher 显式
                # 传值，但回溯审核（_reply_to_ignored_message）、rapid-fire
                # flush 等旁路构造请求时不带该字段。None→False 会让这些回复
                # 既不召回 scoped 记忆也不落成员轮，还会把共享群会话的
                # memory_enabled 翻回 False、阻断 idle flush 结算。让 None
                # 恒等于"配置的群记忆策略"，任何旁路自动对齐，不再逐点补。
                settings = getattr(self.plugin, "_qq_settings", {}) or {}
                return bool(settings.get("group_memory_enabled", False))
            if permission_level == "admin":
                return True
            # 非 admin 私聊：跟 participant 记忆开关走（对偶群路径的
            # None→配置策略）。开着时该轮以对方的 participant 域读写，
            # legacy 私聊主人语料仍然只属于 admin。
            settings = getattr(self.plugin, "_qq_settings", {}) or {}
            return bool(settings.get("private_participant_memory_enabled", False))
        return bool(requested)

    def should_persist_memory(self, *, should_use_memory_context: bool, requested: Optional[bool], is_group: bool = False) -> bool:
        if requested is None:
            if is_group:
                # 群会话的持久化跟配置策略走、与"本轮是否召回"解耦：
                # proactive 等路径显式关本轮召回（use=False）不代表要把
                # 共享会话的 memory_enabled 翻回 False——那会搁浅已缓冲
                # 的 opt-in 历史直到下一条普通消息。
                settings = getattr(self.plugin, "_qq_settings", {}) or {}
                return bool(settings.get("group_memory_enabled", False))
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
