# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reserved character fields and legacy reserved-schema migration metadata."""

# 角色档案保留字段（统一管理）
# - system: 由系统指定功能维护，不允许通用角色编辑接口直接修改
# - workshop: 创意工坊导入/发布流程专用，不应从外部角色卡直接透传
CHARACTER_SYSTEM_RESERVED_FIELDS = (
    "_reserved",
    "live2d",
    "voice_id",
    "system_prompt",
    "model_type",
    "live3d_sub_type",
    "vrm",
    "vrm_animation",
    "lighting",
    "vrm_rotation",
    "live2d_item_id",
    "live2d_idle_animation",
    "item_id",
    "idleAnimation",
    "idleAnimations",
    "mmd",
    "mmd_animation",
    "mmd_idle_animation",
    "mmd_idle_animations",
    "touch_set",
    "_field_order",
)

CHARACTER_WORKSHOP_RESERVED_FIELDS = (
    "原始数据",
    "文件路径",
    "创意工坊物品ID",
    "description",
    "tags",
    "name",
    "描述",
    "标签",
    "关键词",
)

CHARACTER_RESERVED_FIELDS = tuple(
    dict.fromkeys((*CHARACTER_SYSTEM_RESERVED_FIELDS, *CHARACTER_WORKSHOP_RESERVED_FIELDS))
)


def get_character_reserved_fields() -> tuple[str, ...]:
    """Return the reserved character-profile fields (deduplicated, ordered)."""
    return CHARACTER_RESERVED_FIELDS


# 角色保留字段 schema（v2）
# 所有系统保留字段统一收口到 `_reserved`，并按 avatar/live2d/vrm 分层。
RESERVED_FIELD_SCHEMA = {
    # voice_id 兼容两形态：旧扁平串 + 声音来源统一架构的结构对象 {source,provider,ref}
    # （并查集式惰性迁移，用户设音色时逐条迁移）。否则已迁移的角色每次 load 都被
    # validate_reserved_schema 误报 _reserved.voice_id 结构异常。
    "voice_id": (str, dict),
    "system_prompt": str,
    "field_order": list,
    "persona_override": {
        "preset_id": str,
        "selected_at": str,
        "source": str,
        "prompt_guidance": str,
        "profile": dict,
    },
    "ai_context": {
        "rename_events": list,
    },
    "character_origin": {
        "source": str,
        "source_id": str,
        "display_name": str,
        "model_ref": str,
    },
    "avatar": {
        "model_type": str,
        "live3d_sub_type": str,
        "asset_source": str,
        "asset_source_id": str,
        "live2d": {
            "model_path": str,
        },
        "vrm": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
        },
        "mmd": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "rendering": (dict, type(None)),
            "physics": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
        },
    },
}

# 兼容迁移映射：旧平铺字段 -> _reserved 路径
# 注意：rotation / camera_position / position / scale / viewport / display 保持本地偏好存储，
# 不迁移到 characters.json。
LEGACY_FLAT_TO_RESERVED = {
    "voice_id": ("voice_id",),
    "system_prompt": ("system_prompt",),
    "model_type": ("avatar", "model_type"),
    "live3d_sub_type": ("avatar", "live3d_sub_type"),
    "live2d_item_id": ("avatar", "asset_source_id"),
    "item_id": ("avatar", "asset_source_id"),
    "live2d": ("avatar", "live2d", "model_path"),
    "vrm": ("avatar", "vrm", "model_path"),
    "vrm_animation": ("avatar", "vrm", "animation"),
    "idleAnimation": ("avatar", "vrm", "idle_animation"),
    "idleAnimations": ("avatar", "vrm", "idle_animation"),
    "lighting": ("avatar", "vrm", "lighting"),
    "mmd": ("avatar", "mmd", "model_path"),
    "mmd_animation": ("avatar", "mmd", "animation"),
    "mmd_idle_animation": ("avatar", "mmd", "idle_animation"),
    "mmd_idle_animations": ("avatar", "mmd", "idle_animation"),
}
