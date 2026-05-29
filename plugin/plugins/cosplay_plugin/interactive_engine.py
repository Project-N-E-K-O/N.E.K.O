"""Cosplay 互动模式引擎。

1v1 实时对话 + 每轮 AI 生图。
用户可选设定情境目标，AI 围绕目标推进；无目标时自由发挥。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from plugin.logging_config import get_logger
from .cosplay_types import CosplayCharacter, _now_iso

_logger = get_logger("cosplay.interactive")


@dataclass
class InteractiveMessage:
    """互动对话中的一条消息。"""
    role: str = "user"          # user / assistant / system
    text: str = ""
    scene_description: str = "" # AI 回复中提取的场景描述（用于生图）
    mood: str = ""              # 当前情绪
    image_url: str = ""         # 生成的图片 URL
    image_path: str = ""        # 本地图片路径
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "scene_description": self.scene_description,
            "mood": self.mood,
            "image_url": self.image_url,
            "image_path": self.image_path,
            "timestamp": self.timestamp,
        }


class CosplayInteractiveEngine:
    """互动模式引擎。"""

    def __init__(self, llm_gateway: Any = None, image_generator: Any = None) -> None:
        self._llm = llm_gateway
        self._img_gen = image_generator

        self._character: CosplayCharacter | None = None
        self._goal: str = ""
        self._history: list[InteractiveMessage] = []
        self._system_prompt: str = ""
        self._active: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def character(self) -> CosplayCharacter | None:
        return self._character

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def history(self) -> list[InteractiveMessage]:
        return list(self._history)

    # ── 会话管理 ────────────────────────────────────────────

    def start_session(
        self,
        character: CosplayCharacter,
        goal: str = "",
    ) -> None:
        """开始互动会话。"""
        self._character = character
        self._goal = goal
        self._history = []
        self._active = True
        self._system_prompt = self._build_system_prompt()

        # 系统消息
        self._history.append(InteractiveMessage(
            role="system",
            text=f"互动会话开始。角色：{character.name}",
        ))
        _logger.info("interactive session started: character={}", character.name)

    def end_session(self) -> list[dict[str, Any]]:
        """结束会话，返回对话记录。"""
        self._active = False
        record = [m.to_dict() for m in self._history if m.role != "system"]
        _logger.info("interactive session ended: {} messages", len(record))
        return record

    def set_goal(self, goal: str) -> None:
        """设置/更新情境目标。"""
        self._goal = goal
        self._system_prompt = self._build_system_prompt()
        self._history.append(InteractiveMessage(
            role="system",
            text=f"情境目标已设置：{goal}",
        ))

    def clear_goal(self) -> None:
        """清除目标，回到自由对话。"""
        self._goal = ""
        self._system_prompt = self._build_system_prompt()
        self._history.append(InteractiveMessage(
            role="system",
            text="情境目标已清除，进入自由对话模式。",
        ))

    # ── 对话 ────────────────────────────────────────────────

    async def send_message(self, user_text: str) -> InteractiveMessage:
        """用户发消息，返回 AI 回复（含场景描述和图片）。"""
        if not self._active or not self._character:
            return InteractiveMessage(role="assistant", text="请先开始互动会话。")

        # 记录用户消息
        self._history.append(InteractiveMessage(role="user", text=user_text))

        # 构建 LLM 消息
        messages = self._build_llm_messages(user_text)

        # 调用 LLM
        llm_response = await self._call_llm(messages)

        # 解析回复（提取文字 + 场景描述 + 情绪）
        text, scene_desc, mood = self._parse_llm_response(llm_response)

        # 记录 AI 回复
        ai_msg = InteractiveMessage(
            role="assistant",
            text=text,
            scene_description=scene_desc,
            mood=mood,
        )
        self._history.append(ai_msg)

        # 生图（异步，不阻塞回复）
        if self._img_gen and scene_desc:
            try:
                image_url = await self._generate_image(scene_desc, mood)
                if image_url:
                    ai_msg.image_url = image_url
            except Exception as e:
                _logger.warning("image generation failed: {}", e)

        return ai_msg

    # ── LLM 交互 ────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        char = self._character
        if not char:
            return ""

        parts = [
            f"你现在扮演角色「{char.name}」。",
            f"角色描述：{char.description}" if char.description else "",
            f"外貌：{char.structured.to_prompt_text()}" if char.structured.to_prompt_text() else "",
            f"服装：{char.costume.to_prompt_text()}" if char.costume.to_prompt_text() else "",
            f"默认场景：{char.scene.to_prompt_text()}" if char.scene.to_prompt_text() else "",
        ]

        if self._goal:
            parts.append(f"\n当前情境目标：{self._goal}")
            parts.append("请围绕这个目标推进对话，保持角色人设。")
        else:
            parts.append("\n当前为自由对话模式，自然地扮演角色与用户互动。")

        parts.append("""
在每条回复的末尾，请用以下格式输出场景信息（用于生成画面）：
[SCENE] 用英文描述当前场景的画面内容（人物姿态、表情、环境细节）
[MOOD] 用一个词描述当前氛围（如：温馨、紧张、浪漫、搞笑）
示例：
[SCENE] a girl with long black hair wearing a white kimono, standing under cherry blossoms, gentle smile, petals falling
[MOOD] romantic""")

        return "\n".join(p for p in parts if p)

    def _build_llm_messages(self, user_text: str) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt}]

        # 最近 N 轮对话作为上下文
        recent = [m for m in self._history if m.role in ("user", "assistant")][-20:]
        for m in recent:
            messages.append({"role": m.role, "content": m.text})

        return messages

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM 获取回复。"""
        if self._llm is None:
            # 无 LLM 时返回占位回复
            return "（LLM 未配置，这是占位回复。请配置 LLM 网关后重试。）\n[SCENE] placeholder scene\n[MOOD] neutral"

        try:
            result = await self._llm.chat_completion(
                messages=messages,
                max_tokens=800,
                temperature=0.8,
            )
            if isinstance(result, dict):
                return result.get("content", str(result))
            return str(result)
        except Exception as e:
            _logger.error("LLM call failed: {}", e)
            return f"（对话出错：{e}）\n[SCENE] error scene\n[MOOD] confused"

    def _parse_llm_response(self, response: str) -> tuple[str, str, str]:
        """解析 LLM 回复，提取文字、场景描述、情绪。"""
        text = response
        scene_desc = ""
        mood = ""

        # 提取 [SCENE]
        scene_match = re.search(r"\[SCENE\]\s*(.+?)(?:\n|\[MOOD\]|$)", response, re.DOTALL)
        if scene_match:
            scene_desc = scene_match.group(1).strip()
            text = text.replace(scene_match.group(0), "").strip()

        # 提取 [MOOD]
        mood_match = re.search(r"\[MOOD\]\s*(.+?)(?:\n|$)", response)
        if mood_match:
            mood = mood_match.group(1).strip()
            text = text.replace(mood_match.group(0), "").strip()

        return text, scene_desc, mood

    async def _generate_image(self, scene_desc: str, mood: str) -> str:
        """调用图片生成。"""
        if self._img_gen is None:
            return ""

        char = self._character
        if not char:
            return ""

        # 组装 prompt
        from .prompt_assembler import PromptAssembler
        assembler = PromptAssembler()
        prompt = assembler.assemble_interactive_prompt(
            character=char,
            scene_description=scene_desc,
            mood=mood,
        )

        try:
            result = await self._img_gen.generate(prompt)
            if isinstance(result, dict):
                return result.get("url", result.get("image_url", ""))
            return str(result)
        except Exception as e:
            _logger.error("image gen failed: {}", e)
            return ""

    # ── 导出 ────────────────────────────────────────────────

    def export_history(self) -> list[dict[str, Any]]:
        """导出完整对话历史。"""
        return [m.to_dict() for m in self._history]
