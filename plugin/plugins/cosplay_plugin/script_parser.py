"""Cosplay 剧本解析器。

解析 cosplay 格式剧本，输出结构化的分镜节点。

剧本格式：
  【角色】块：定义角色 + 服装 + 场景
  【第N幕：标题】块：场景划分
  [镜头：XXX] 指令
  [场景：XXX] 指令
  [动作：XXX] 指令
  角色名：台词
  （内心）独白
  #旁白#
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from plugin.logging_config import get_logger
from .cosplay_types import (
    Action,
    CosplayCharacter,
    Costume,
    Dialogue,
    SceneDefinition,
    StoryboardNode,
    StructuredAttributes,
    _new_id,
)

_logger = get_logger("cosplay.script_parser")

# ── 正则 ────────────────────────────────────────────────────

_RE_SCENE_HEADER = re.compile(r"^【第(\d+)幕[：:](.+?)】\s*$")
_RE_ROLE_BLOCK_START = re.compile(r"^【角色】\s*$")
_RE_CAMERA = re.compile(r"^\[镜头[：:](.+?)\]\s*$")
_RE_SCENE_DIRECTIVE = re.compile(r"^\[场景[：:](.+?)\]\s*$")
_RE_ACTION = re.compile(r"^\[动作[：:](.+?)\]\s*$")
_RE_BGM = re.compile(r"^\[音乐[：:](.+?)\]\s*$")
_RE_DIALOGUE = re.compile(r"^([^：:\s\[#（]{1,20})[：:](.+)$")
_RE_INNER = re.compile(r"^[（(](.+?)[）)](.+)$")  # （内心）独白
_RE_NARRATION = re.compile(r"^#(.+)#$")  # #旁白#
_RE_ROLE_NAME = re.compile(r"^\s+(\S+)[：:](.+)$")  # 角色定义行：缩进 + 名：描述
_RE_ROLE_COSTUME = re.compile(r"^\s+服装[：:](.+)$")
_RE_ROLE_SCENE = re.compile(r"^\s+场景[：:](.+)$")


@dataclass
class ParsedScript:
    """解析后的剧本结构。"""
    characters: dict[str, CosplayCharacter] = field(default_factory=dict)
    scenes: list[ParsedScene] = field(default_factory=list)
    raw_text: str = ""
    debug: str = ""

    @property
    def total_turns(self) -> int:
        return sum(len(s.nodes) for s in self.scenes)


@dataclass
class ParsedScene:
    """解析后的一幕。"""
    scene_id: int = 0
    title: str = ""
    nodes: list[RawNode] = field(default_factory=list)


@dataclass
class RawNode:
    """解析但未生成图片的原始节点。"""
    camera: str = ""
    scene_desc: str = ""
    actions: list[Action] = field(default_factory=list)
    dialogues: list[Dialogue] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    mood: str = ""
    bgm: str = ""


class CosplayScriptParser:
    """Cosplay 剧本解析器。"""

    def parse(self, script_text: str) -> ParsedScript:
        """解析完整剧本文本。"""
        result = ParsedScript(raw_text=script_text)
        lines = script_text.strip().split("\n")

        current_characters: dict[str, CosplayCharacter] = {}
        current_scene: ParsedScene | None = None
        current_node: RawNode | None = None
        in_role_block = False
        current_role_name = ""
        current_role_lines: list[str] = []
        scene_counter = 0

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line:
                continue

            # ── 角色块开始 ──
            if _RE_ROLE_BLOCK_START.match(line):
                in_role_block = True
                continue

            # ── 在角色块内 ──
            if in_role_block:
                # 角色块结束条件：遇到场景头或非缩进行
                if _RE_SCENE_HEADER.match(line) or (line.startswith("【") and not line.startswith("【角色")):
                    in_role_block = False
                    # 继续处理当前行
                else:
                    # 解析角色定义
                    role_match = _RE_ROLE_NAME.match(raw_line)
                    costume_match = _RE_ROLE_COSTUME.match(raw_line)
                    scene_match = _RE_ROLE_SCENE.match(raw_line)

                    if role_match:
                        # 保存上一个角色
                        if current_role_name and current_role_lines:
                            current_characters[current_role_name] = self._build_character(
                                current_role_name, current_role_lines
                            )
                        current_role_name = role_match.group(1).strip()
                        current_role_lines = [role_match.group(2).strip()]
                    elif costume_match and current_role_name:
                        current_role_lines.append(f"服装：{costume_match.group(1).strip()}")
                    elif scene_match and current_role_name:
                        current_role_lines.append(f"场景：{scene_match.group(1).strip()}")
                    elif current_role_name and raw_line.startswith("  "):
                        current_role_lines.append(line)
                    else:
                        # 角色块结束
                        if current_role_name and current_role_lines:
                            current_characters[current_role_name] = self._build_character(
                                current_role_name, current_role_lines
                            )
                            current_role_name = ""
                            current_role_lines = []
                        in_role_block = False
                        # 不 continue，让当前行继续走下面的解析
                    if in_role_block:
                        continue

            # ── 保存角色块最后一个 ──
            if current_role_name and current_role_lines:
                current_characters[current_role_name] = self._build_character(
                    current_role_name, current_role_lines
                )
                current_role_name = ""
                current_role_lines = []

            # ── 场景头 ──
            scene_match = _RE_SCENE_HEADER.match(line)
            if scene_match:
                # 保存上一个节点
                if current_node and current_scene:
                    current_scene.nodes.append(current_node)
                    current_node = None

                scene_counter += 1
                current_scene = ParsedScene(
                    scene_id=scene_counter,
                    title=scene_match.group(2).strip(),
                )
                result.scenes.append(current_scene)
                continue

            # ── 如果没有场景头，创建默认场景 ──
            if current_scene is None:
                current_scene = ParsedScene(scene_id=1, title="默认场景")
                result.scenes.append(current_scene)

            # ── 镜头指令 ──
            cam_match = _RE_CAMERA.match(line)
            if cam_match:
                if current_node and (current_node.dialogues or current_node.actions):
                    current_scene.nodes.append(current_node)
                    current_node = None
                if current_node is None:
                    current_node = RawNode()
                current_node.camera = cam_match.group(1).strip()
                continue

            # ── 场景指令 ──
            sc_match = _RE_SCENE_DIRECTIVE.match(line)
            if sc_match:
                if current_node and (current_node.dialogues or current_node.actions):
                    current_scene.nodes.append(current_node)
                    current_node = None
                if current_node is None:
                    current_node = RawNode()
                current_node.scene_desc = sc_match.group(1).strip()
                continue

            # ── 动作指令 ──
            act_match = _RE_ACTION.match(line)
            if act_match:
                if current_node is None:
                    current_node = RawNode()
                desc = act_match.group(1).strip()
                # 尝试提取角色名
                char_name = ""
                for cname in current_characters:
                    if desc.startswith(cname):
                        char_name = cname
                        desc = desc[len(cname):].strip()
                        break
                current_node.actions.append(Action(description=desc, character=char_name))
                continue

            # ── 音乐指令 ──
            bgm_match = _RE_BGM.match(line)
            if bgm_match:
                if current_node is None:
                    current_node = RawNode()
                current_node.bgm = bgm_match.group(1).strip()
                continue

            # ── 旁白 ──
            narr_match = _RE_NARRATION.match(line)
            if narr_match:
                if current_node is None:
                    current_node = RawNode()
                current_node.dialogues.append(Dialogue(
                    character="",
                    text=narr_match.group(1).strip(),
                    is_narration=True,
                ))
                continue

            # ── 内心独白 ──
            inner_match = _RE_INNER.match(line)
            if inner_match:
                if current_node is None:
                    current_node = RawNode()
                current_node.dialogues.append(Dialogue(
                    character=inner_match.group(1).strip(),
                    text=inner_match.group(2).strip(),
                    is_inner=True,
                ))
                continue

            # ── 对话 ──
            dial_match = _RE_DIALOGUE.match(line)
            if dial_match:
                if current_node is None:
                    current_node = RawNode()
                char_name = dial_match.group(1).strip()
                text = dial_match.group(2).strip()
                current_node.dialogues.append(Dialogue(character=char_name, text=text))
                if char_name not in current_node.characters:
                    current_node.characters.append(char_name)
                continue

            # ── 其他行视为旁白 ──
            if current_node is None:
                current_node = RawNode()
            current_node.dialogues.append(Dialogue(character="", text=line, is_narration=True))

        # 收尾
        if current_role_name and current_role_lines:
            current_characters[current_role_name] = self._build_character(
                current_role_name, current_role_lines
            )
        if current_node and current_scene:
            current_scene.nodes.append(current_node)

        result.characters = current_characters
        return result

    def _build_character(self, name: str, lines: list[str]) -> CosplayCharacter:
        """从解析的行构建角色对象。"""
        structured = StructuredAttributes()
        costume = Costume()
        scene = SceneDefinition()
        description_parts = []

        for line in lines:
            if line.startswith("服装："):
                costume_text = line[3:].strip()
                costume.name = costume_text
                costume.description = costume_text
            elif line.startswith("场景："):
                scene_text = line[3:].strip()
                scene.name = scene_text
                scene.description = scene_text
            else:
                # 尝试从描述中提取结构化属性
                self._extract_attributes(line, structured)
                description_parts.append(line)

        return CosplayCharacter(
            id=_new_id(),
            name=name,
            description="，".join(description_parts),
            structured=structured,
            costume=costume,
            scene=scene,
        )

    def _extract_attributes(self, text: str, attrs: StructuredAttributes) -> None:
        """从自由文本中提取结构化属性。"""
        gender_keywords = {"男": "男", "女": "女", "少年": "男", "少女": "女"}
        for kw, gender in gender_keywords.items():
            if kw in text:
                attrs.gender = gender
                break

        hair_keywords = ["黑长直", "双马尾", "短发", "长发", "马尾", "披肩发", "卷发"]
        for kw in hair_keywords:
            if kw in text:
                attrs.hair_style = kw
                break

        color_keywords = ["黑色", "棕色", "银白", "粉色", "金色", "蓝色", "红色", "紫色"]
        for kw in color_keywords:
            if kw in text:
                attrs.hair_color = kw
                break

        personality_keywords = ["温柔", "活泼", "冷酷", "元气", "内敛", "开朗", "神秘", "傲娇"]
        for kw in personality_keywords:
            if kw in text:
                attrs.personality = kw
                break
