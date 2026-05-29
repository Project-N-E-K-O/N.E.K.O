"""Cosplay Prompt 组装器。

将三要素（角色+服装+场景）+ 动作 + 镜头指令 → 生图 prompt。
"""
from __future__ import annotations

from typing import Any

from .cosplay_types import (
    CosplayCharacter,
    Action,
    StoryboardNode,
)

# ── 镜头指令映射 ────────────────────────────────────────────

CAMERA_PROMPT_MAP: dict[str, str] = {
    "全景": "full body shot, wide angle",
    "全身": "full body shot",
    "半身": "upper body shot, medium shot",
    "特写": "close-up shot, face focus",
    "远景": "establishing shot, wide landscape",
    "仰拍": "low angle shot, looking up",
    "俯拍": "high angle shot, looking down, bird's eye view",
    "侧面": "side view, profile shot",
    "背影": "back view, from behind",
    "群像": "group shot, multiple characters",
}

# ── 画风模板 ────────────────────────────────────────────────

STYLE_PROMPTS: dict[str, str] = {
    "anime": "anime style, cel shading, vibrant colors, detailed illustration",
    "realistic": "photorealistic, cinematic lighting, high detail, 8k",
    "watercolor": "watercolor painting style, soft edges, pastel colors",
    "oil_painting": "oil painting style, rich textures, dramatic lighting",
    "pixel": "pixel art style, retro, 16-bit",
    "sketch": "pencil sketch, hand-drawn, line art",
    "chinese_ink": "Chinese ink wash painting, traditional, brush strokes",
    "comic": "comic book style, bold lines, halftone dots",
}


class PromptAssembler:
    """将 cosplay 三要素组装为生图 prompt。"""

    def __init__(self, style: str = "anime", extra_quality_tags: str = "") -> None:
        self._style = style
        self._quality_tags = extra_quality_tags or "masterpiece, best quality, highly detailed"

    @property
    def style(self) -> str:
        return self._style

    @style.setter
    def style(self, value: str) -> None:
        self._style = value

    def assemble_character_prompt(
        self,
        character: CosplayCharacter,
        action: str = "",
        camera: str = "",
    ) -> str:
        """为单个角色组装完整生图 prompt。"""
        parts = []

        # 质量标签
        parts.append(self._quality_tags)

        # 画风
        style_text = STYLE_PROMPTS.get(self._style, STYLE_PROMPTS["anime"])
        parts.append(style_text)

        # 角色外貌
        structured_text = character.structured.to_prompt_text()
        if structured_text:
            parts.append(structured_text)

        # 服装
        costume_text = character.costume.to_prompt_text()
        if costume_text:
            parts.append(f"wearing {costume_text}")

        # 场景
        scene_text = character.scene.to_prompt_text()
        if scene_text:
            parts.append(scene_text)

        # 动作
        if action:
            parts.append(action)

        # 镜头
        camera_text = CAMERA_PROMPT_MAP.get(camera, "")
        if camera_text:
            parts.append(camera_text)

        # 角色自由描述补充
        if character.description:
            parts.append(character.description)

        return ", ".join(p for p in parts if p)

    def assemble_storyboard_prompt(self, node: StoryboardNode) -> str:
        """为分镜节点组装生图 prompt。"""
        parts = []

        # 质量标签
        parts.append(self._quality_tags)

        # 画风
        style_text = STYLE_PROMPTS.get(self._style, STYLE_PROMPTS["anime"])
        parts.append(style_text)

        # 角色（多角色同框）
        for char_name in node.characters:
            char_cfg = node.character_configs.get(char_name)
            if char_cfg:
                char_text = char_cfg.structured.to_prompt_text()
                costume_text = char_cfg.costume.to_prompt_text()
                if char_text:
                    parts.append(char_text)
                if costume_text:
                    parts.append(f"wearing {costume_text}")

        # 场景
        if node.scene_desc:
            parts.append(node.scene_desc)

        # 动作
        for act in node.actions:
            if act.description:
                parts.append(act.description)

        # 镜头
        camera_text = CAMERA_PROMPT_MAP.get(node.camera, "")
        if camera_text:
            parts.append(camera_text)

        # 氛围
        if node.mood:
            parts.append(f"{node.mood} mood")

        return ", ".join(p for p in parts if p)

    def assemble_interactive_prompt(
        self,
        character: CosplayCharacter,
        scene_description: str = "",
        mood: str = "",
    ) -> str:
        """为互动模式组装生图 prompt（每轮对话用）。"""
        parts = []

        parts.append(self._quality_tags)

        style_text = STYLE_PROMPTS.get(self._style, STYLE_PROMPTS["anime"])
        parts.append(style_text)

        # 角色外貌
        structured_text = character.structured.to_prompt_text()
        if structured_text:
            parts.append(structured_text)

        # 服装
        costume_text = character.costume.to_prompt_text()
        if costume_text:
            parts.append(f"wearing {costume_text}")

        # 场景（优先用对话中提取的场景描述，否则用角色默认场景）
        if scene_description:
            parts.append(scene_description)
        else:
            default_scene = character.scene.to_prompt_text()
            if default_scene:
                parts.append(default_scene)

        # 氛围
        if mood:
            parts.append(f"{mood} mood")

        return ", ".join(p for p in parts if p)

    def get_available_styles(self) -> dict[str, str]:
        return dict(STYLE_PROMPTS)

    def get_available_cameras(self) -> dict[str, str]:
        return dict(CAMERA_PROMPT_MAP)
